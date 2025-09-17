# app.py
# 실행 예:
#   Windows: set PORT=10000 && python app.py
#   macOS/Linux: export PORT=10000 && python app.py
#   Render(권장): gunicorn -w 1 -k gthread --threads 8 --timeout 120 --keep-alive 30 -b 0.0.0.0:$PORT app:app

import os
import math
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from functools import lru_cache
from urllib.parse import quote

import requests
from flask import Flask, request, jsonify, send_from_directory, redirect, Response
from flask_cors import CORS

# .env (로컬 개발)
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# beaches_coordinates.py: beach_coords = {"해변명": (lon, lat), ...}
try:
    from beaches_coordinates import beach_coords  # type: ignore
except Exception:
    beach_coords = {}

app = Flask(__name__, static_folder="static")
CORS(app)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("coastal-drive")

APP_DIR = Path(__file__).resolve().parent

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
ORS_API_KEY = os.getenv("ORS_API_KEY")
TOURAPI_KEY = os.getenv("TOURAPI_KEY")  # ← 환경변수 필수

if not ORS_API_KEY:
    raise RuntimeError("ORS_API_KEY not set")
if not GOOGLE_API_KEY:
    raise RuntimeError("GOOGLE_API_KEY not set")
if not TOURAPI_KEY:
    raise RuntimeError("TOURAPI_KEY not set (KorService 키)")

# =========================================================
# index.html 서빙 (없으면 임시 페이지 제공)
# =========================================================
def _find_index_html() -> Optional[Path]:
    for p in [APP_DIR / "index.html", APP_DIR / "templates" / "index.html", APP_DIR / "static" / "index.html"]:
        if p.is_file():
            return p
    return None

def _fallback_index_html() -> str:
    return """<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8"><title>Coastal Drive</title></head><body><p>index.html이 없습니다.</p></body></html>"""

@app.route("/", methods=["GET", "HEAD"])
def index():
    p = _find_index_html()
    if p:
        return send_from_directory(p.parent.as_posix(), p.name)
    return Response(_fallback_index_html(), mimetype="text/html")

@app.route("/favicon.ico")
def favicon():
    return "", 204

@app.route("/healthz")
def healthz():
    return jsonify({"ok": True})

# =========================================================
# 공통 유틸
# =========================================================
def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2.0)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2.0)**2
    return 2.0 * R * math.asin(math.sqrt(a))

def _coerce_json() -> Dict[str, Any]:
    j = request.get_json(silent=True, force=True)
    if isinstance(j, dict):
        return j
    if request.form:
        return {k: request.form.get(k) for k in request.form}
    if request.args:
        return {k: request.args.get(k) for k in request.args}
    return {}

def _to_float(x: Any) -> Optional[float]:
    try:
        return float(x)
    except Exception:
        return None

# =========================================================
# 지오코딩 (Google)
# =========================================================
def geocode_google(address: str) -> Optional[Tuple[float, float]]:
    if not GOOGLE_API_KEY or not address:
        return None
    try:
        r = requests.get(
            "https://maps.googleapis.com/maps/api/geocode/json",
            params={"address": address, "key": GOOGLE_API_KEY},
            timeout=10,
        )
        r.raise_for_status()
        j = r.json()
        loc = j["results"][0]["geometry"]["location"]
        return float(loc["lat"]), float(loc["lng"])
    except Exception as e:
        log.warning("geocode_google failed: %s", e)
        return None

@lru_cache(maxsize=2048)
def reverse_geocode_google(lat: float, lon: float) -> str:
    if not GOOGLE_API_KEY:
        return ""
    try:
        r = requests.get(
            "https://maps.googleapis.com/maps/api/geocode/json",
            params={"latlng": f"{lat},{lon}", "key": GOOGLE_API_KEY},
            timeout=10,
        )
        r.raise_for_status()
        return r.json()["results"][0]["formatted_address"]
    except Exception:
        return ""

# =========================================================
# 경유지 선택 (기존 성공 로직 유지)
# =========================================================
def _ll_to_xy_km(lat: float, lon: float, lat0: float, lon0: float) -> Tuple[float, float]:
    x = (lon - lon0) * math.cos(math.radians(lat0)) * 111.32
    y = (lat - lat0) * 110.57
    return x, y

def _projection_metrics(start: Tuple[float, float], end: Tuple[float, float], p: Tuple[float, float]) -> Tuple[float, float]:
    (slat, slon), (elat, elon), (plat, plon) = start, end, p
    lat0 = (slat + elat) / 2.0
    lon0 = (slon + elon) / 2.0
    sx, sy = _ll_to_xy_km(slat, slon, lat0, lon0)
    ex, ey = _ll_to_xy_km(elat, elon, lat0, lon0)
    px, py = _ll_to_xy_km(plat, plon, lat0, lon0)
    vx, vy = (ex - sx), (ey - sy)
    ux, uy = (px - sx), (py - sy)
    denom = vx*vx + vy*vy
    if denom <= 0:
        return 0.0, float("inf")
    t = (ux*vx + uy*vy) / denom
    cross = abs(vx*uy - vy*ux)
    vnorm = math.sqrt(denom)
    perp_km = cross / vnorm if vnorm > 0 else float("inf")
    return t, perp_km

def _approx_chain_length(points: List[Tuple[float, float]], end: Tuple[float, float]) -> float:
    seq = points + [end]
    total = 0.0
    for i in range(len(seq)-1):
        a, b = seq[i], seq[i+1]
        total += haversine(a[0], a[1], b[0], b[1])
    return total

def _max_direct(v: float) -> float:
    return max(v, 1e-6)

def find_waypoints_along_direction(
    start: Tuple[float, float],
    end: Tuple[float, float],
    max_n: int = 3,
    corridor_km: float = 30.0,
    max_abs_detour_km: float = 50.0,
    max_rel_detour: float = 0.35,
) -> List[Tuple[str, float, float, float]]:
    cands: List[Tuple[float, str, float, float]] = []
    for name, (lon, lat) in beach_coords.items():
        t, offset = _projection_metrics(start, end, (lat, lon))
        if 0.0 < t < 1.0 and offset <= corridor_km:
            cands.append((t, name, lat, lon))
    cands.sort(key=lambda x: x[0])
    if not cands:
        return []

    sel: List[Tuple[str, float, float, float]] = []
    base_direct = haversine(start[0], start[1], end[0], end[1])
    chain_points: List[Tuple[float, float]] = [start]
    for t, name, lat, lon in cands:
        tentative_points = chain_points + [(lat, lon)]
        chain_len = _approx_chain_length(tentative_points, end)
        detour = chain_len - base_direct
        if detour <= max_abs_detour_km and (detour / _max_direct(base_direct)) <= max_rel_detour:
            sel.append((name, lat, lon, t))
            chain_points.append((lat, lon))
            if len(sel) >= max_n:
                break
    return sel

def find_best_beach_waypoint_legacy(start: Tuple[float,float], end: Tuple[float,float]) -> Optional[Tuple[str,float,float]]:
    start_lat, start_lon = start
    end_lat, end_lon = end
    lat_cands, lon_cands = [], []
    for name, (lon, lat) in beach_coords.items():
        if not ((35 <= lat <= 38 and 128 <= lon <= 131) or (33 <= lat <= 35 and 126 <= lon <= 129) or (34 <= lat <= 38 and 124 <= lon <= 126)):
            continue
        if abs(lat - start_lat) < 0.2 and (end_lon - start_lon) * (lon - start_lon) > 0:
            lat_cands.append((name, lat, lon, haversine(end_lat, end_lon, lat, lon)))
        if abs(lon - start_lon) < 0.2 and (end_lat - start_lat) * (lat - start_lat) > 0:
            lon_cands.append((name, lat, lon, haversine(end_lat, end_lon, lat, lon)))
    best_lat = min(lat_cands, key=lambda x: x[3]) if lat_cands else None
    best_lon = min(lon_cands, key=lambda x: x[3]) if lon_cands else None
    if best_lat and best_lon:
        return (best_lat if best_lat[3] <= best_lon[3] else best_lon)[:3]
    return (best_lat or best_lon)[:3] if (best_lat or best_lon) else None

# =========================================================
# ORS 라우팅 (기존 성공 로직 유지)
# =========================================================
def get_ors_route_multi(points: List[Tuple[float, float]]) -> Tuple[Dict[str, Any], int]:
    if not ORS_API_KEY:
        return {"error": "ORS_API_KEY is missing"}, 500
    coords = [[lon, lat] for (lat, lon) in points]  # ORS는 [lon, lat]
    try:
        r = requests.post(
            "https://api.openrouteservice.org/v2/directions/driving-car/geojson",
            headers={"Authorization": ORS_API_KEY, "Content-Type": "application/json"},
            json={"coordinates": coords},
            timeout=30,
        )
        return r.json(), r.status_code
    except Exception as e:
        return {"error": str(e)}, 500

# =========================================================
# TourAPI (KorService2 우선, 1로 폴백) + 60km 커버리지
# =========================================================
KOR_BASES = [
    # (엔드포인트 prefix, suffix 맵)
    ("https://apis.data.go.kr/B551011/KorService2", {"loc": "locationBasedList2", "intro": "detailIntro2", "common": "detailCommon2"}),
    ("https://apis.data.go.kr/B551011/KorService1", {"loc": "locationBasedList1", "intro": "detailIntro1", "common": "detailCommon1"}),
]

def _try_get(url: str, params: Dict[str, Any], timeout: float = 6.0) -> Tuple[Optional[Dict[str, Any]], int, str]:
    """serviceKey 인/디코딩 두 가지로 시도"""
    key_as_is = TOURAPI_KEY
    key_enc = TOURAPI_KEY if "%" in TOURAPI_KEY else quote(TOURAPI_KEY, safe="")
    variants = [("as_is", key_as_is), ("encoded", key_enc)]
    last_status = 0
    last_variant = ""
    for variant_name, key in variants:
        q = dict(params)
        q["serviceKey"] = key
        try:
            r = requests.get(url, params=q, timeout=timeout)
            last_status = r.status_code
            last_variant = variant_name
            if r.status_code != 200:
                continue
            return r.json(), r.status_code, variant_name
        except Exception:
            continue
    return None, last_status, last_variant

def _kor_call(kind: str, params: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    kind: 'loc' | 'intro' | 'common'
    반환: (items, debug)
    """
    debug = {"trials": []}
    for base, suf in KOR_BASES:
        path = suf[kind]
        url = f"{base}/{path}"
        data, status, variant = _try_get(url, params, timeout=6.0)
        entry = {"base": base, "url": url, "status": status, "variant": variant, "resultCode": ""}
        if data:
            try:
                body = data.get("response", {}).get("body", {})
                items = body.get("items", {}).get("item", [])
                if isinstance(items, dict):
                    items = [items]
                entry["resultCode"] = data.get("response", {}).get("header", {}).get("resultCode", "")
                debug["trials"].append(entry)
                return items or [], debug
            except Exception:
                pass
        debug["trials"].append(entry)
    return [], debug

def _km_to_lonlat(dx_km: float, dy_km: float, at_lat: float) -> Tuple[float, float]:
    dlat = dy_km / 110.574
    dlon = dx_km / (111.320 * max(math.cos(math.radians(at_lat)), 1e-6))
    return dlon, dlat

def _sample_along_route(coords: List[List[float]], step_km: float = 25.0) -> List[Tuple[float, float, Tuple[float, float]]]:
    """
    경로를 일정 거리(step_km)마다 샘플링. 각 샘플은 (lon, lat, tangent(km단위 vx,vy)) 반환.
    """
    if not coords:
        return []
    # 누적 길이 기반 샘플링
    out: List[Tuple[float, float, Tuple[float, float]]] = []
    target = 0.0
    cur_acc = 0.0
    lon0, lat0 = coords[0]
    for i in range(1, len(coords)):
        lon1, lat1 = coords[i-1]
        lon2, lat2 = coords[i]
        seg_km = haversine(lat1, lon1, lat2, lon2)
        if seg_km <= 1e-6:
            continue
        while cur_acc + seg_km >= target:
            remain = target - cur_acc
            r = max(0.0, min(1.0, remain / seg_km))
            lon = lon1 + (lon2 - lon1) * r
            lat = lat1 + (lat2 - lat1) * r
            # 접선(대략 세그먼트 방향)
            vx_km = (lon2 - lon1) * 111.320 * math.cos(math.radians(lat))
            vy_km = (lat2 - lat1) * 110.574
            out.append((lon, lat, (vx_km, vy_km)))
            target += step_km
        cur_acc += seg_km
    # 끝점도 하나 추가
    lon_last, lat_last = coords[-1]
    vx_last = (lon_last - lon0) * 111.320 * math.cos(math.radians(lat_last))
    vy_last = (lat_last - lat0) * 110.574
    out.append((lon_last, lat_last, (vx_last, vy_last)))
    return out

def _normalize_item(item: Dict[str, Any], category: str) -> Optional[Dict[str, Any]]:
    lon = _to_float(item.get("mapx"))
    lat = _to_float(item.get("mapy"))
    if lon is None or lat is None:
        return None
    return {
        "contentid": str(item.get("contentid") or ""),
        "title": item.get("title") or "",
        "addr1": item.get("addr1") or "",
        "mapx": lon,
        "mapy": lat,
        "firstimage": item.get("firstimage") or "",
        "tel": item.get("tel") or "",
        "homepage": item.get("homepage") or "",
        "category": category,
    }

def search_tour_items_along_route(geojson: Dict[str, Any],
                                  corridor_half_km: float = 30.0,  # 양쪽 30 km → 총 60 km 커버리지
                                  sample_step_km: float = 25.0,
                                  num_rows: int = 100,
                                  limit_each: int = 40,
                                  debug_on: bool = False) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, Any]]:
    """
    Tour(12) + Food(39) 수집.
    - API 반경 제한(최대 20km)을 감안하여, 경로 중심선에서 ±30km 커버하려고
      오프셋 샘플링(±30, 0 km)을 수행 + radius=20000 사용.
    """
    dbg = {"samples": 0, "queries": [], "counts": {"tour": 0, "food": 0}}

    try:
        coords = geojson["features"][0]["geometry"]["coordinates"]
    except Exception:
        return {"tour": [], "food": [], "all": []}, dbg

    # 샘플링 지점
    samples = _sample_along_route(coords, step_km=sample_step_km)
    dbg["samples"] = len(samples)

    # 오프셋 (km): ±30, 0 → 20km 반경과 합치면 대략 ±50~60km 커버
    offsets = [-30.0, 0.0, 30.0]
    RADIUS = 20000  # TourAPI 최대 반경

    seen: set = set()
    tours: List[Dict[str, Any]] = []
    foods: List[Dict[str, Any]] = []

    def do_loc_query(lon: float, lat: float, ctype: int) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        params = {
            "mapX": lon,
            "mapY": lat,
            "radius": RADIUS,
            "listYN": "Y",
            "arrange": "E",
            "numOfRows": num_rows,
            "pageNo": 1,
            "MobileOS": "ETC",
            "MobileApp": "SeaRoute",
            "_type": "json",
            "contentTypeId": ctype,
        }
        items, dbg_call = _kor_call("loc", params)
        if debug_on:
            # 가장 최근 trial 기록만 축약 저장
            tail = dbg_call.get("trials", [])[-1] if dbg_call.get("trials") else {}
            dbg["queries"].append({"ctype": ctype, "status": tail.get("status", 0), "resultCode": tail.get("resultCode", ""), "variant": tail.get("variant", "")})
        return items, dbg_call

    for (lon, lat, (vx, vy)) in samples:
        if len(tours) >= limit_each and len(foods) >= limit_each:
            break
        # 단위 법선
        norm = math.hypot(vx, vy)
        nx, ny = (0.0, 0.0)
        if norm > 1e-6:
            nx, ny = (-vy / norm, vx / norm)  # 접선의 좌측이 +방향

        for off in offsets:
            off_dx_km = nx * off
            off_dy_km = ny * off
            dlon, dlat = _km_to_lonlat(off_dx_km, off_dy_km, lat)
            qlon, qlat = lon + dlon, lat + dlat

            if len(tours) < limit_each:
                items, _ = do_loc_query(qlon, qlat, 12)
                for it in items:
                    cid = str(it.get("contentid") or "")
                    if not cid or cid in seen:
                        continue
                    seen.add(cid)
                    normd = _normalize_item(it, "tour")
                    if normd:
                        tours.append(normd)
                    if len(tours) >= limit_each:
                        break

            if len(foods) < limit_each:
                items, _ = do_loc_query(qlon, qlat, 39)
                for it in items:
                    cid = str(it.get("contentid") or "")
                    if not cid or cid in seen:
                        continue
                    seen.add(cid)
                    normd = _normalize_item(it, "food")
                    if normd:
                        foods.append(normd)
                    if len(foods) >= limit_each:
                        break

            if len(tours) >= limit_each and len(foods) >= limit_each:
                break

    dbg["counts"]["tour"] = len(tours)
    dbg["counts"]["food"] = len(foods)
    all_items = tours + foods
    return {"tour": tours, "food": foods, "all": all_items}, dbg

# =========================================================
# 라우팅 핸들러 (경로/거리·시간 + TourAPI 스팟)
# =========================================================
def _handle_route():
    data = _coerce_json()
    start_in = data.get("start") or data.get("origin") or data.get("from")
    end_in = data.get("end") or data.get("destination") or data.get("to")
    max_wps = int(data.get("max_waypoints") or 3)
    max_wps = max(0, min(3, max_wps))
    debug_on = str(data.get("debug") or "0") in ("1", "true", "True")

    if not start_in or not end_in:
        return jsonify({"error": "start/end 누락"}), 400

    start = geocode_google(start_in) if isinstance(start_in, str) else tuple(start_in) if isinstance(start_in, (list, tuple)) else None
    end = geocode_google(end_in) if isinstance(end_in, str) else tuple(end_in) if isinstance(end_in, (list, tuple)) else None
    if not start or not end:
        return jsonify({"error": "주소 변환 실패"}), 400

    # 경유지 (기존 성공 로직 유지)
    way_sel = find_waypoints_along_direction(start, end, max_n=max_wps)
    if not way_sel and max_wps >= 1 and beach_coords:
        legacy = find_best_beach_waypoint_legacy(start, end)
        if legacy:
            way_sel = [(legacy[0], legacy[1], legacy[2], 0.5)]

    # ORS 라우팅
    points = [start] + [(lat, lon) for (_, lat, lon, _) in way_sel] + [end]
    route_data, status = get_ors_route_multi(points)
    if status != 200 or "error" in route_data:
        return jsonify({"error": route_data.get("error", f"OpenRouteService 실패({status})")}), status

    # 경로 주변 TourAPI 수집 (60km 커버리지)
    spots, tour_dbg = search_tour_items_along_route(route_data, corridor_half_km=30.0, sample_step_km=25.0, num_rows=100, limit_each=40, debug_on=debug_on)

    # 경유지 정보
    wp_objs = []
    for i, (name, lat, lon, t) in enumerate(way_sel, start=1):
        wp_objs.append({
            "order": i, "name": name, "lat": lat, "lon": lon, "t": t,
            "address": reverse_geocode_google(lat, lon) or ""
        })

    resp: Dict[str, Any] = {
        "route": route_data,
        "waypoints_used": wp_objs,
        "spots": spots["all"],
        "spots_grouped": {"tour": spots["tour"], "food": spots["food"]},
    }
    if len(wp_objs) >= 1: resp["waypoint"] = wp_objs[0]
    if len(wp_objs) >= 2: resp["waypoint2"] = wp_objs[1]
    if len(wp_objs) >= 3: resp["waypoint3"] = wp_objs[2]

    if debug_on:
        resp["tourapi_debug"] = tour_dbg

    return jsonify(resp), 200

@app.route("/route", methods=["POST", "GET"])
def route():
    if request.method == "GET":
        return redirect("/")
    return _handle_route()

@app.route("/api/route", methods=["POST"])
def api_route():
    return _handle_route()

# 단독 진단용 (브라우저에서 테스트: /api/tourspot?lon=127.5&lat=36.5&radius=20000&debug=1)
@app.route("/api/tourspot")
def api_tourspot():
    lon = _to_float(request.args.get("lon"))
    lat = _to_float(request.args.get("lat"))
    radius = int(request.args.get("radius") or 20000)
    debug_on = str(request.args.get("debug") or "0") in ("1", "true", "True")
    if lon is None or lat is None:
        return jsonify({"error": "lon/lat 파라미터 필요"}), 400

    items_t, dbg_t = _kor_call("loc", {
        "mapX": lon, "mapY": lat, "radius": min(20000, max(1000, radius)),
        "listYN": "Y", "arrange": "E", "numOfRows": 100, "pageNo": 1,
        "MobileOS": "ETC", "MobileApp": "SeaRoute", "_type": "json", "contentTypeId": 12
    })
    items_f, dbg_f = _kor_call("loc", {
        "mapX": lon, "mapY": lat, "radius": min(20000, max(1000, radius)),
        "listYN": "Y", "arrange": "E", "numOfRows": 100, "pageNo": 1,
        "MobileOS": "ETC", "MobileApp": "SeaRoute", "_type": "json", "contentTypeId": 39
    })

    tours = [x for x in ([_normalize_item(i, "tour") for i in items_t]) if x]
    foods = [x for x in ([_normalize_item(i, "food") for i in items_f]) if x]

    resp = {"counts": {"tour": len(tours), "food": len(foods), "total": len(tours)+len(foods)},
            "all": tours + foods}
    if debug_on:
        tail_t = dbg_t.get("trials", [])[-1] if dbg_t.get("trials") else {}
        tail_f = dbg_f.get("trials", [])[-1] if dbg_f.get("trials") else {}
        resp["debug"] = {"tour": tail_t, "food": tail_f}
    return jsonify(resp), 200

if __name__ == "__main__":
    port_env = os.getenv("PORT")
    if not port_env:
        log.warning("PORT env not set; falling back to 10000 (local dev).")
    port = int(port_env or "10000")
    log.info(f"Starting on 0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port)
