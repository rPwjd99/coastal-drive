# app.py
# 실행:
#   Windows: set PORT=10000 && python app.py
#   macOS/Linux: export PORT=10000 && python app.py
#   Render: gunicorn -w 1 -k gthread --threads 8 --timeout 120 --keep-alive 30 -b 0.0.0.0:$PORT app:app

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

# .env (선택)
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# 해변 후보 좌표 (기존 성공 로직 유지)
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
# KorService 키(국문 관광정보). 인/디코딩 아무거나 넣어도 자동 시도
TOURAPI_KEY = os.getenv("TOURAPI_KEY")
# 디버그 켜면 /route 응답에 tourapi_debug가 함께 내려옵니다.
TOURAPI_DEBUG = (os.getenv("TOURAPI_DEBUG", "0").lower() in ("1", "true", "yes"))

# ----- 기본 유틸 -----
def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2.0)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2.0)**2
    return 2.0 * R * math.asin(math.sqrt(a))

def _coerce_json() -> Dict[str, Any]:
    j = request.get_json(silent=True, force=True)
    if isinstance(j, dict): return j
    if request.form: return {k: request.form.get(k) for k in request.form}
    if request.args: return {k: request.args.get(k) for k in request.args}
    return {}

# ----- index.html 서빙 -----
def _find_index_html() -> Optional[Path]:
    for p in [APP_DIR / "index.html", APP_DIR / "templates" / "index.html", APP_DIR / "static" / "index.html"]:
        if p.is_file():
            return p
    return None

def _fallback_index_html() -> str:
    return """<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8"><title>Coastal Drive</title></head>
    <body style="font-family:sans-serif;padding:24px">
    <h2>Coastal Drive</h2>
    <p>index.html이 없어서 임시 페이지를 표시합니다.</p>
    <p>API 상태: <a href="/healthz" target="_blank">/healthz</a> · TourAPI 테스트: <code>/api/tourspot?lon=127.5&lat=36.5&radius=20000</code></p>
    </body></html>"""

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

# ----- Google 지오코딩 -----
def geocode_google(address: str) -> Optional[Tuple[float, float]]:
    if not GOOGLE_API_KEY or not address:
        return None
    try:
        r = requests.get(
            "https://maps.googleapis.com/maps/api/geocode/json",
            params={"address": address, "key": GOOGLE_API_KEY},
            timeout=10,
        )
        loc = r.json()["results"][0]["geometry"]["location"]
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
        return r.json()["results"][0]["formatted_address"]
    except Exception:
        return ""

# ----- 경유지 (기존 성공 로직 유지) -----
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

def _max_direct(val: float) -> float:
    return max(val, 1e-6)

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

# ----- ORS 라우팅 (그대로) -----
def get_ors_route_multi(points: List[Tuple[float, float]]) -> Tuple[Dict[str, Any], int]:
    if not ORS_API_KEY:
        return {"error": "ORS_API_KEY is missing"}, 500
    coords = [[lon, lat] for (lat, lon) in points]  # ORS는 [lon,lat]
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
# TourAPI: KorService2→KorService1, 인/디코딩 키 자동 시도
# =========================================================
def _result_code(j: Dict[str, Any]) -> str:
    try: return str(j["response"]["header"]["resultCode"])
    except Exception: return ""

def _servicekey_variants(key: str) -> List[Tuple[str, str]]:
    if not key: return []
    vars = [("as_is", key)]
    if "%" not in key:  # Decoding 키만 받은 경우
        try: vars.append(("percent_encoded", quote(key, safe="")))
        except Exception: pass
    return vars

def _tourapi_request(path: str, params: Dict[str, Any], debug_list: Optional[List[Dict[str, Any]]] = None) -> Tuple[Dict[str, Any], int]:
    if not TOURAPI_KEY:
        return {"error": "TOURAPI_KEY missing"}, 500
    bases = ["https://apis.data.go.kr/B551011/KorService2", "https://apis.data.go.kr/B551011/KorService1"]
    key_vars = _servicekey_variants(TOURAPI_KEY)
    for base in bases:
        for label, keyv in key_vars:
            q = dict(params); q["serviceKey"] = keyv
            try:
                r = requests.get(f"{base}/{path}", params=q, timeout=8)
                status = r.status_code
                try: data = r.json()
                except Exception: data = {}
                rc = _result_code(data)
                if debug_list is not None:
                    debug_list.append({"base": base.rsplit("/",1)[-1], "path": path, "variant": label, "status": status, "resultCode": rc})
                if status == 200 and rc == "0000":
                    return data, status
            except Exception as e:
                if debug_list is not None:
                    debug_list.append({"base": base.rsplit("/",1)[-1], "path": path, "variant": label, "status": 0, "error": str(e)})
                continue
    return {"error": "TourAPI request failed"}, 500

@lru_cache(maxsize=8000)
def _tourapi_location_cached(lon_q: int, lat_q: int, content_type_id: int, radius_m: int) -> Tuple[Dict[str, Any], int]:
    lon = lon_q / 1000.0
    lat = lat_q / 1000.0
    params = {
        "mapX": lon, "mapY": lat, "radius": radius_m,
        "listYN": "Y", "arrange": "E",
        "numOfRows": 25, "pageNo": 1,
        "MobileOS": "ETC", "MobileApp": "SeaRoute", "_type": "json",
        "contentTypeId": content_type_id,
    }
    return _tourapi_request("locationBasedList1", params, None)

def _tourapi_location_based(lon: float, lat: float, content_type_id: int, radius_m: int, debug_list: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    # 중복 호출 방지 위해 소수점 3자리(약 100m) 단위로 캐시
    lon_q = int(round(lon * 1000))
    lat_q = int(round(lat * 1000))
    data, status = _tourapi_location_cached(lon_q, lat_q, content_type_id, radius_m)
    if debug_list is not None:
        # 캐시 여부는 기록하지 않지만, 어떤 파라미터로 호출되었는지 남김
        debug_list.append({"base":"cached", "path":"locationBasedList1", "variant":f"ct{content_type_id}", "status": status, "resultCode": _result_code(data), "mapX": lon, "mapY": lat, "radius": radius_m})
    if status != 200:
        return []
    try:
        items = data.get("response", {}).get("body", {}).get("items", {}).get("item", []) or []
        if isinstance(items, dict): items = [items]
        return items
    except Exception:
        return []

@lru_cache(maxsize=4096)
def _tourapi_detail_intro_cached(content_id: str, content_type_id: int) -> Tuple[Dict[str, Any], int]:
    params = {
        "contentId": content_id, "contentTypeId": content_type_id,
        "_type": "json", "MobileOS": "ETC", "MobileApp": "SeaRoute",
    }
    return _tourapi_request("detailIntro1", params, None)

def _tourapi_detail_intro(content_id: str, content_type_id: int, debug_list: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    data, status = _tourapi_detail_intro_cached(content_id, content_type_id)
    if debug_list is not None:
        debug_list.append({"base":"cached", "path":"detailIntro1", "variant":f"ct{content_type_id}", "status": status, "resultCode": _result_code(data), "contentId": content_id})
    if status != 200: return {}
    try:
        items = data.get("response", {}).get("body", {}).get("items", {}).get("item", []) or []
        if isinstance(items, dict): items = [items]
        return items[0] if items else {}
    except Exception:
        return {}

def _normalize_detail(item: Dict[str, Any], intro: Dict[str, Any], category: str) -> Dict[str, Any]:
    def _f(x):
        try: return float(x)
        except: return None
    mx, my = _f(item.get("mapx")), _f(item.get("mapy"))
    res = {
        "contentid": str(item.get("contentid") or ""),
        "title": item.get("title") or "",
        "addr1": item.get("addr1") or "",
        "mapx": mx if mx is not None else "",
        "mapy": my if my is not None else "",
        "firstimage": item.get("firstimage") or "",
        "tel": item.get("tel") or "",
        "category": category,
        "homepage": item.get("homepage") or "",
        "parking_info": "",
        "openhour": "",
        "restday": "",
    }
    if category == "tour":
        res["openhour"] = intro.get("usetime") or ""
        res["restday"] = intro.get("restdate") or ""
        res["parking_info"] = intro.get("parking") or ""
    elif category == "food":
        res["openhour"] = intro.get("opentimefood") or ""
        res["restday"] = intro.get("restdatefood") or ""
        res["parking_info"] = intro.get("parkingfood") or ""
    p = (res["parking_info"] or "").strip()
    res["has_parking"] = bool(p) and ("불가" not in p and "없" not in p)
    return res

# =========================================================
# 60km 커버리지 구현
#  - 1차: 경로 중심선에서 radius=20km
#  - 부족하면 2차: 경로 좌/우 40km 오프셋에서 radius=20km (간격 제한)
# =========================================================
def _offset_point(lon: float, lat: float, nx_km: float, ny_km: float) -> Tuple[float, float]:
    # km -> deg 변환 (근사)
    dlon = nx_km / (111.32 * max(math.cos(math.radians(lat)), 1e-6))
    dlat = ny_km / 110.57
    return lon + dlon, lat + dlat

def _segment_normal_km(a: Tuple[float,float], b: Tuple[float,float]) -> Tuple[float,float]:
    # a,b: (lon,lat) -> km 좌표계로 변환 후 법선
    lon1, lat1 = a; lon2, lat2 = b
    lat0 = (lat1 + lat2)/2.0
    dx = (lon2 - lon1) * math.cos(math.radians(lat0)) * 111.32
    dy = (lat2 - lat1) * 110.57
    # 방향벡터 (dx,dy)에 대한 좌측 법선 (-dy, dx)
    nx, ny = -dy, dx
    nrm = math.hypot(nx, ny)
    if nrm < 1e-6: return (0.0, 0.0)
    return (nx/nrm, ny/nrm)

def search_tour_items_along_route(geojson: Dict[str, Any], limit_each: int = 50) -> Dict[str, List[Dict[str, Any]]]:
    try:
        feat = geojson["features"][0]
        coords = feat["geometry"]["coordinates"]  # [lon,lat]
        summary = feat.get("properties", {}).get("summary", {}) or {}
        distance_km = float(summary.get("distance", 0.0)) / 1000.0
    except Exception:
        return {"tour": [], "food": [], "all": []}

    # 샘플 개수 (기본 30~60)
    target_samples = 30 if distance_km <= 0 else max(30, min(60, int(math.ceil(distance_km / 15.0))))
    step = max(1, len(coords) // target_samples)

    debug_bucket: List[Dict[str, Any]] = [] if TOURAPI_DEBUG else None

    def collect(samples: List[Tuple[float,float]], radius_m: int):
        seen = set()
        tours, foods = [], []
        for (lon, lat) in samples:
            # 관광지(12)
            for it in _tourapi_location_based(lon, lat, content_type_id=12, radius_m=radius_m, debug_list=debug_bucket):
                cid = str(it.get("contentid") or "")
                if not cid or cid in seen: continue
                seen.add(cid)
                intro = _tourapi_detail_intro(cid, 12, debug_list=debug_bucket)
                norm = _normalize_detail(it, intro, "tour")
                try: float(norm["mapx"]); float(norm["mapy"])
                except Exception: continue
                tours.append(norm)
                if len(tours) >= limit_each: break
            if len(tours) >= limit_each: break

        for (lon, lat) in samples:
            # 맛집(39)
            for it in _tourapi_location_based(lon, lat, content_type_id=39, radius_m=radius_m, debug_list=debug_bucket):
                cid = str(it.get("contentid") or "")
                if not cid or cid in seen: continue
                seen.add(cid)
                intro = _tourapi_detail_intro(cid, 39, debug_list=debug_bucket)
                norm = _normalize_detail(it, intro, "food")
                try: float(norm["mapx"]); float(norm["mapy"])
                except Exception: continue
                foods.append(norm)
                if len(foods) >= limit_each: break
            if len(foods) >= limit_each: break

        return tours, foods

    # 중심선 샘플
    center_samples: List[Tuple[float,float]] = []
    for i in range(0, len(coords), step):
        lon, lat = coords[i]
        center_samples.append((float(lon), float(lat)))

    # 1차: 중심선 20km
    tour_items, food_items = collect(center_samples, radius_m=20000)

    # 부족하면 2차: 좌/우 40km 오프셋 (샘플 간격 더 넓힘)
    min_needed = 20
    if (len(tour_items) + len(food_items)) < min_needed and len(center_samples) >= 4:
        offset_samples: List[Tuple[float,float]] = []
        # 대략 3개 중 1개만 오프셋 샘플링해서 호출 수 제한
        for idx in range(1, len(center_samples)-1, 3):
            lon0, lat0 = center_samples[idx-1]
            lon1, lat1 = center_samples[idx]
            # 법선 방향
            nx, ny = _segment_normal_km((lon0, lat0), (lon1, lat1))
            if nx == 0 and ny == 0:
                continue
            # 좌/우 40km 오프셋
            for mul in (+40.0, -40.0):
                lon_off, lat_off = _offset_point(lon1, lat1, nx*mul, ny*mul)
                offset_samples.append((lon_off, lat_off))

        t2, f2 = collect(offset_samples, radius_m=20000)
        # 합치기
        # (collect() 안에서 seen 관리로 중복 제거됨)
        tour_items = (tour_items + t2)[:limit_each]
        food_items = (food_items + f2)[:limit_each]

    all_items = tour_items + food_items
    if TOURAPI_DEBUG:
        app.config["__tourapi_last_debug__"] = debug_bucket or []
    return {"tour": tour_items, "food": food_items, "all": all_items}

# ----- 라우팅 핸들러 (경로/요약/경유지 그대로, TourAPI만 보강) -----
def _handle_route():
    data = _coerce_json()
    start_in = data.get("start") or data.get("origin") or data.get("from")
    end_in = data.get("end") or data.get("destination") or data.get("to")
    max_wps = int(data.get("max_waypoints") or 3)
    max_wps = max(0, min(3, max_wps))

    if not start_in or not end_in:
        return jsonify({"error": "start/end 누락"}), 400

    start = geocode_google(start_in) if isinstance(start_in, str) else tuple(start_in) if isinstance(start_in, (list, tuple)) else None
    end = geocode_google(end_in) if isinstance(end_in, str) else tuple(end_in) if isinstance(end_in, (list, tuple)) else None
    if not start or not end:
        return jsonify({"error": "주소 변환 실패"}), 400

    way_sel = find_waypoints_along_direction(start, end, max_n=max_wps)
    if not way_sel and max_wps >= 1 and beach_coords:
        legacy = find_best_beach_waypoint_legacy(start, end)
        if legacy:
            way_sel = [(legacy[0], legacy[1], legacy[2], 0.5)]

    points = [start] + [(lat, lon) for (_, lat, lon, _) in way_sel] + [end]
    route_data, status = get_ors_route_multi(points)
    if status != 200 or "error" in route_data:
        return jsonify({"error": route_data.get("error", f"OpenRouteService 실패({status})")}), status

    spots = search_tour_items_along_route(route_data)

    wp_objs = []
    for i, (name, lat, lon, t) in enumerate(way_sel, start=1):
        wp_objs.append({
            "order": i, "name": name, "lat": lat, "lon": lon, "t": t,
            "address": reverse_geocode_google(lat, lon) or ""
        })

    feat = route_data["features"][0]
    summary = (feat.get("properties") or {}).get("summary") or {}
    distance = float(summary.get("distance", 0.0))
    duration = float(summary.get("duration", 0.0))

    resp: Dict[str, Any] = {
        "route": route_data,
        "waypoints_used": wp_objs,
        "spots": spots["all"],
        "spots_grouped": { "tour": spots["tour"], "food": spots["food"] },
        "counts": { "tour": len(spots["tour"]), "food": len(spots["food"]), "total": len(spots["all"]) },
        "summary": { "distance_m": distance, "duration_s": duration },
    }
    if len(wp_objs) >= 1: resp["waypoint"] = wp_objs[0]
    if len(wp_objs) >= 2: resp["waypoint2"] = wp_objs[1]
    if len(wp_objs) >= 3: resp["waypoint3"] = wp_objs[2]
    if TOURAPI_DEBUG: resp["tourapi_debug"] = app.config.get("__tourapi_last_debug__", [])

    return jsonify(resp), 200

@app.route("/route", methods=["POST", "GET"])
def route():
    if request.method == "GET":
        return redirect("/")
    return _handle_route()

@app.route("/api/route", methods=["POST"])
def api_route():
    return _handle_route()

# ----- TourAPI 단독 테스트 -----
@app.route("/api/tourspot")
def api_tourspot():
    lon = request.args.get("lon", type=float)
    lat = request.args.get("lat", type=float)
    radius = request.args.get("radius", default=20000, type=int)
    if lon is None or lat is None:
        return jsonify({"error": "lon/lat 파라미터 필요"}), 400
    dbg: List[Dict[str, Any]] = [] if TOURAPI_DEBUG else None
    items_t = _tourapi_location_based(lon, lat, content_type_id=12, radius_m=radius, debug_list=dbg)
    items_f = _tourapi_location_based(lon, lat, content_type_id=39, radius_m=radius, debug_list=dbg)
    return jsonify({
        "counts": {"tour": len(items_t), "food": len(items_f), "total": len(items_t) + len(items_f)},
        "sample_titles": {"tour": [it.get("title") for it in items_t[:5]], "food": [it.get("title") for it in items_f[:5]]},
        "debug": dbg or []
    }), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    log.info(f"Starting on 0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port)
