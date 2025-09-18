# app.py
# 실행 예:
#   로컬: python app.py  (또는 set/export PORT=10000 후 실행)
#   Render 권장 Start Command:
#   gunicorn -w 1 -k gthread --threads 8 --timeout 120 --keep-alive 30 -b 0.0.0.0:$PORT app:app

import os
import math
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from functools import lru_cache
from urllib.parse import unquote

import requests
from flask import Flask, request, jsonify, send_from_directory, redirect, Response

# .env (로컬 개발 시)
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# beach_coords: {"해변명": (lon, lat), ...}
try:
    from beaches_coordinates import beach_coords
except Exception:
    beach_coords = {}

app = Flask(__name__, static_folder="static")
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("coastal-drive")
APP_DIR = Path(__file__).resolve().parent

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
ORS_API_KEY = os.getenv("ORS_API_KEY")

# ---- TourAPI 설정 (KorService1/2 아무거나) ----
_RAW_TOURAPI_KEY = os.getenv("TOURAPI_KEY", "").strip()
# 포털에서 주는 인코딩 키/디코딩 키 모두 허용: %xx 포함하면 자동 디코딩
TOURAPI_KEY = unquote(_RAW_TOURAPI_KEY) if "%" in _RAW_TOURAPI_KEY else _RAW_TOURAPI_KEY
TOURAPI_BASE = (os.getenv("TOURAPI_BASE") or "https://apis.data.go.kr/B551011/KorService1").rstrip("/")

# ===============================
# index.html 서빙 (파일 없으면 임시 페이지)
# ===============================
def _find_index_html() -> Optional[Path]:
    for p in [APP_DIR / "templates" / "index.html", APP_DIR / "index.html", APP_DIR / "static" / "index.html"]:
        if p.is_file():
            return p
    return None

@app.route("/", methods=["GET", "HEAD"])
def index():
    p = _find_index_html()
    if p:
        # 정적 서빙 (Jinja 렌더링 불필요)
        return send_from_directory(p.parent.as_posix(), p.name)
    return Response("<h2>index.html이 없습니다 (templates/index.html에 두세요)</h2>", mimetype="text/html")

@app.route("/favicon.ico")
def favicon():
    return "", 204

@app.route("/healthz")
def healthz():
    return jsonify({"ok": True})

# ===============================
# 공통 유틸 (경로 로직: 기존 성공본 그대로)
# ===============================
def haversine(lat1, lon1, lat2, lon2) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2.0)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2.0)**2
    return 2.0 * R * math.asin(math.sqrt(a))  # km

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

def geocode_google(address: str) -> Optional[Tuple[float, float]]:
    if not GOOGLE_API_KEY or not address:
        return None
    try:
        r = requests.get(
            "https://maps.googleapis.com/maps/api/geocode/json",
            params={"address": address, "key": GOOGLE_API_KEY},
            timeout=15,
        )
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
            timeout=15,
        )
        return r.json()["results"][0]["formatted_address"]
    except Exception:
        return ""

# ---- 투영/코리도/우회비용 기반 경유지 선택 (최대 3개) : [성공본 그대로] ----
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

# ---- ORS 라우팅: [성공본 그대로] ----
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

# ===============================
# TourAPI: 반경 폴백 + 조밀 샘플링 (경로 코어 불변)
# ===============================
TOUR_TIMEOUT = 7

def _tour_get(path: str, params: Dict[str, Any]) -> Tuple[Dict[str, Any], int, str]:
    """KorService1/2 자동 대응 + HTTPS 고정"""
    base = TOURAPI_BASE
    url = f"{base}/{path.lstrip('/')}"
    try:
        r = requests.get(url, params=params, timeout=TOUR_TIMEOUT)
        j = r.json()
        # resultCode 추출 시도
        rc = ""
        try:
            rc = j.get("response", {}).get("header", {}).get("resultCode", "")
        except Exception:
            rc = ""
        return j, r.status_code, rc or ""
    except Exception as e:
        return {"error": str(e)}, 599, ""

def _tour_location_based_once(lon: float, lat: float, content_type_id: int, radius_m: int) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    params = {
        "serviceKey": TOURAPI_KEY,
        "mapX": lon,
        "mapY": lat,
        "radius": radius_m,
        "listYN": "Y",
        "arrange": "E",
        "numOfRows": 30,
        "pageNo": 1,
        "MobileOS": "ETC",
        "MobileApp": "SeaRoute",
        "_type": "json",
        "contentTypeId": content_type_id,
    }
    data, status, rc = _tour_get("locationBasedList1", params)
    items = []
    try:
        items = data.get("response", {}).get("body", {}).get("items", {}).get("item", []) or []
        if isinstance(items, dict):
            items = [items]
    except Exception:
        items = []
    return items, {"status": status, "resultCode": rc, "radius": radius_m}

@lru_cache(maxsize=4096)
def _tour_detail_intro(content_id: str, content_type_id: int) -> Dict[str, Any]:
    params = {
        "serviceKey": TOURAPI_KEY,
        "contentId": content_id,
        "contentTypeId": content_type_id,
        "_type": "json",
        "MobileOS": "ETC",
        "MobileApp": "SeaRoute",
    }
    data, status, _ = _tour_get("detailIntro1", params)
    if status != 200:
        return {}
    try:
        items = data.get("response", {}).get("body", {}).get("items", {}).get("item", []) or []
        if isinstance(items, dict):
            items = [items]
        return items[0] if items else {}
    except Exception:
        return {}

def _normalize(item: Dict[str, Any], intro: Dict[str, Any], category: str) -> Optional[Dict[str, Any]]:
    mapx = _to_float(item.get("mapx"))
    mapy = _to_float(item.get("mapy"))
    if mapx is None or mapy is None:
        return None
    res = {
        "contentid": str(item.get("contentid") or ""),
        "title": item.get("title") or "",
        "addr1": item.get("addr1") or "",
        "mapx": mapx,
        "mapy": mapy,
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

def _sample_indices_by_distance(coords: List[List[float]], step_km: float) -> List[int]:
    """경로 좌표를 거리 기준으로 샘플링(조밀)."""
    if not coords:
        return []
    res = [0]
    acc = 0.0
    for i in range(1, len(coords)):
        lon1, lat1 = coords[i-1]
        lon2, lat2 = coords[i]
        d = haversine(lat1, lon1, lat2, lon2)
        acc += d
        if acc >= step_km:
            res.append(i)
            acc = 0.0
    if res[-1] != len(coords)-1:
        res.append(len(coords)-1)
    return res

def search_tour_items_along_route_wide(geojson: Dict[str, Any]) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, Any]]:
    """
    경로 코어 불변. TourAPI만:
      - 반경: 최대 20km 사용 (API 제한), 20→15→10→8→5 km 폴백
      - 샘플링: 약 7km 간격 (조밀)로 경로점 선택 → 20km 원 여러 개의 합집합으로 실질적인 '약 30km 코리도' 커버
      - detailIntro1로 팝업 정보 보강
      - 디버그: status/resultCode 기록
    """
    debug = {"calls": []}
    try:
        coords = geojson["features"][0]["geometry"]["coordinates"]
    except Exception:
        return {"tour": [], "food": [], "all": []}, debug

    # 조밀 샘플링 (약 7km 간격)
    idxs = _sample_indices_by_distance(coords, step_km=7.0)
    seen: set = set()
    tours: List[Dict[str, Any]] = []
    foods: List[Dict[str, Any]] = []

    # 반경 폴백 사다리
    radii = [20000, 15000, 10000, 8000, 5000]

    def collect_for_type(content_type_id: int, bucket: List[Dict[str, Any]], cat: str):
        for ii in idxs:
            lon, lat = coords[ii]
            got = False
            for r in radii:
                items, meta = _tour_location_based_once(lon, lat, content_type_id, r)
                debug["calls"].append({"type": cat, "idx": ii, "lon": lon, "lat": lat, **meta})
                if meta["status"] == 200 and meta["resultCode"] == "0000" and items:
                    got = True
                    for it in items:
                        cid = str(it.get("contentid") or "")
                        if not cid or cid in seen:
                            continue
                        seen.add(cid)
                        intro = _tour_detail_intro(cid, content_type_id)
                        norm = _normalize(it, intro, cat)
                        if norm:
                            bucket.append(norm)
                    break  # 반경 폴백 종료
            # radii를 모두 돌아도 없으면 다음 포인트로
        return

    # 관광지(12), 음식(39) 각각 수집
    collect_for_type(12, tours, "tour")
    collect_for_type(39, foods, "food")

    all_items = tours + foods
    debug["summary"] = {
        "tour_count": len(tours),
        "food_count": len(foods),
        "total": len(all_items),
        "sample_points": len(idxs),
    }
    return {"tour": tours, "food": foods, "all": all_items}, debug

# ===============================
# /route 핸들러 (경로/경유/ORS: 그대로)
# ===============================
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

    # ---- 경유지 선택 (성공본 그대로) ----
    way_sel = find_waypoints_along_direction(start, end, max_n=max_wps)
    if not way_sel and max_wps >= 1 and beach_coords:
        legacy = find_best_beach_waypoint_legacy(start, end)
        if legacy:
            way_sel = [(legacy[0], legacy[1], legacy[2], 0.5)]

    # ---- ORS 라우팅 (성공본 그대로) ----
    points = [start] + [(lat, lon) for (_, lat, lon, _) in way_sel] + [end]
    route_data, status = get_ors_route_multi(points)
    if status != 200 or "error" in route_data:
        return jsonify({"error": route_data.get("error", f"OpenRouteService 실패({status})")}), status

    # ---- TourAPI 수집 (경로 코어 미변, 수집만 확장) ----
    spots, tour_dbg = search_tour_items_along_route_wide(route_data)

    # ---- 응답 정리 (기존 키 유지) ----
    wp_objs = []
    for i, (name, lat, lon, t) in enumerate(way_sel, start=1):
        wp_objs.append({
            "order": i,
            "name": name,
            "lat": lat,
            "lon": lon,
            "t": t,
            "address": reverse_geocode_google(lat, lon) or ""
        })

    resp: Dict[str, Any] = {
        "route": route_data,
        "waypoints_used": wp_objs,
        "spots": spots["all"],
        "spots_grouped": {"tour": spots["tour"], "food": spots["food"]},
        "tourapi_debug": tour_dbg,  # 디버그: 0건일 때 원인 파악용
    }
    if len(wp_objs) >= 1: resp["waypoint"] = wp_objs[0]
    if len(wp_objs) >= 2: resp["waypoint2"] = wp_objs[1]
    if len(wp_objs) >= 3: resp["waypoint3"] = wp_objs[2]

    return jsonify(resp), 200

@app.route("/route", methods=["POST", "GET"])
def route():
    if request.method == "GET":
        return redirect("/")
    return _handle_route()

# ---- 단독 디버그: 특정 좌표로 TourAPI 확인 ----
@app.route("/api/tourspot", methods=["GET"])
def api_tourspot():
    lon = _to_float(request.args.get("lon"))
    lat = _to_float(request.args.get("lat"))
    if lon is None or lat is None:
        return jsonify({"error": "lon/lat 파라미터 필요"}), 400
    # 간단한 geojson 모의: 단일 포인트만 넣어 search 함수 재사용
    dummy = {"features":[{"geometry":{"coordinates":[[lon,lat]]}}]}
    spots, dbg = search_tour_items_along_route_wide(dummy)
    return jsonify({"spots_grouped": spots, "debug": dbg}), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    log.info(f"Starting on 0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port)
