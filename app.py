# app.py
# 실행 예:
#   Windows: set PORT=10000 && python app.py
#   macOS/Linux: export PORT=10000 && python app.py
#   Render: gunicorn -w 1 -k gthread --threads 8 --timeout 120 --keep-alive 30 -b 0.0.0.0:$PORT app:app

import os
import math
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from functools import lru_cache
from urllib.parse import quote, unquote, urlencode

import requests
from flask import Flask, request, jsonify, send_from_directory, redirect, Response
from flask_cors import CORS

# .env (선택)
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
TOURAPI_KEY = os.getenv("TOURAPI_KEY")  # 인코딩/디코딩 어떤 형식이든 OK(아래서 자동 시도)

# -------------------------------------------------------
# index.html 서빙 (없으면 임시 페이지 제공)
# -------------------------------------------------------
def _find_index_html() -> Optional[Path]:
    for p in [APP_DIR / "index.html", APP_DIR / "templates" / "index.html", APP_DIR / "static" / "index.html"]:
        if p.is_file():
            return p
    return None

def _fallback_index_html() -> str:
    return """<!DOCTYPE html><meta charset="utf-8"><title>Coastal Drive</title>
    <p>index.html이 없습니다. 같은 디렉토리에 index.html을 두거나 /templates/index.html을 두세요.</p>"""

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

# -------------------------------------------------------
# 공통 유틸
# -------------------------------------------------------
def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
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

# -------------------------------------------------------
# 지오코딩 (Google)
# -------------------------------------------------------
def geocode_google(address: str) -> Optional[Tuple[float, float]]:
    if not GOOGLE_API_KEY or not address:
        return None
    try:
        r = requests.get(
            "https://maps.googleapis.com/maps/api/geocode/json",
            params={"address": address, "key": GOOGLE_API_KEY},
            timeout=10,
        )
        j = r.json()
        loc = j["results"][0]["geometry"]["location"]
        return float(loc["lat"]), float(loc["lng"])
    except Exception as e:
        log.warning("geocode_google failed: %s", e)
        return None

@lru_cache(maxsize=4096)
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

# -------------------------------------------------------
# 경유지 선택 (기존 성공 로직 유지)
# -------------------------------------------------------
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

# -------------------------------------------------------
# ORS 라우팅 (기존 성공 로직 유지)
# -------------------------------------------------------
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

# -------------------------------------------------------
# TourAPI 호출 (KorService2 우선 + KorService1/HTTP 폴백 + 인코딩/디코딩 자동시도)
# -------------------------------------------------------
KOR_BASES = [
    "https://apis.data.go.kr/B551011/KorService2",
    "https://apis.data.go.kr/B551011/KorService1",
    "http://apis.data.go.kr/B551011/KorService2",
    "http://apis.data.go.kr/B551011/KorService1",
]

def _extract_resultcode(j: Dict[str, Any]) -> str:
    try:
        return str(j.get("response", {}).get("header", {}).get("resultCode", "")) or ""
    except Exception:
        return ""

def _tourapi_try(endpoint: str, params: Dict[str, Any], key: str, mode: str) -> Tuple[Dict[str, Any], int, str]:
    """
    mode:
      - 'encoded_url': URL에 serviceKey=그대로 붙이고 나머지만 params
      - 'decoded_param': serviceKey=unquote(key)를 params에 넣음
    """
    if mode == "encoded_url":
        url = f"{endpoint}?serviceKey={key}"
        rest = {k: v for k, v in params.items() if k != "serviceKey"}
        r = requests.get(url, params=rest, timeout=8)
    elif mode == "decoded_param":
        url = endpoint
        p = dict(params)
        p["serviceKey"] = unquote(key)  # 디코딩 키 가정
        r = requests.get(url, params=p, timeout=8)
    else:
        raise ValueError("invalid mode")
    try:
        j = r.json()
    except Exception:
        j = {}
    return j, r.status_code, _extract_resultcode(j)

def _tourapi_fetch(path: str, params: Dict[str, Any], debug_bucket: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """
    KorService2 우선 → KorService1 → http 폴백
    인코딩/디코딩 키 모두 시도
    """
    if not TOURAPI_KEY:
        return []

    items: List[Dict[str, Any]] = []
    for base in KOR_BASES:
        endpoint = f"{base}/{path}"
        # 1) 키를 인코딩 키로 가정하고 URL에 직접 부착
        try:
            j, st, rc = _tourapi_try(endpoint, params, TOURAPI_KEY, "encoded_url")
            if debug_bucket is not None:
                debug_bucket.append({"base": base, "path": path, "mode": "encoded", "status": st, "resultCode": rc})
            if st == 200 and rc == "0000":
                raw = j.get("response", {}).get("body", {}).get("items", {}).get("item", [])
                if isinstance(raw, dict):
                    raw = [raw]
                if raw:
                    items.extend(raw)
                    return items
        except Exception:
            if debug_bucket is not None:
                debug_bucket.append({"base": base, "path": path, "mode": "encoded", "status": 599, "resultCode": ""})

        # 2) 키를 디코딩 키로 가정하고 params에 세팅
        try:
            j, st, rc = _tourapi_try(endpoint, params, TOURAPI_KEY, "decoded_param")
            if debug_bucket is not None:
                debug_bucket.append({"base": base, "path": path, "mode": "decoded", "status": st, "resultCode": rc})
            if st == 200 and rc == "0000":
                raw = j.get("response", {}).get("body", {}).get("items", {}).get("item", [])
                if isinstance(raw, dict):
                    raw = [raw]
                if raw:
                    items.extend(raw)
                    return items
        except Exception:
            if debug_bucket is not None:
                debug_bucket.append({"base": base, "path": path, "mode": "decoded", "status": 599, "resultCode": ""})

    return items  # 비어있을 수 있음

def _normalize_detail(item: Dict[str, Any], intro: Dict[str, Any], category: str) -> Dict[str, Any]:
    mapx = _to_float(item.get("mapx"))
    mapy = _to_float(item.get("mapy"))
    res = {
        "contentid": str(item.get("contentid") or ""),
        "title": item.get("title") or "",
        "addr1": item.get("addr1") or "",
        "mapx": mapx if mapx is not None else 0.0,
        "mapy": mapy if mapy is not None else 0.0,
        "firstimage": item.get("firstimage") or "",
        "tel": item.get("tel") or "",
        "category": category,  # 'tour' or 'food'
        "homepage": item.get("homepage") or "",
        "parking_info": "",
        "openhour": "",
        "restday": "",
    }
    if category == "tour":
        res["openhour"] = intro.get("usetime") or ""
        res["restday"] = intro.get("restdate") or ""
        res["parking_info"] = intro.get("parking") or ""
    else:  # food (39)
        res["openhour"] = intro.get("opentimefood") or ""
        res["restday"] = intro.get("restdatefood") or ""
        res["parking_info"] = intro.get("parkingfood") or ""
    p = (res["parking_info"] or "").strip()
    res["has_parking"] = bool(p) and ("불가" not in p and "없" not in p)
    return res

def _tourapi_location_based(lon: float, lat: float, content_type_id: int, radius_m: int, debug_bucket: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    params = {
        "mapX": lon,
        "mapY": lat,
        "radius": radius_m,
        "listYN": "Y",
        "arrange": "E",
        "numOfRows": 30,
        "pageNo": 1,
        "MobileOS": "ETC",
        "MobileApp": "CoastalDrive",
        "_type": "json",
        "contentTypeId": content_type_id,  # 12=관광지, 39=음식
    }
    return _tourapi_fetch("locationBasedList1", params, debug_bucket)

def _tourapi_detail_intro(content_id: str, content_type_id: int, debug_bucket: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    params = {
        "contentId": content_id,
        "contentTypeId": content_type_id,
        "_type": "json",
        "MobileOS": "ETC",
        "MobileApp": "CoastalDrive",
    }
    items = _tourapi_fetch("detailIntro1", params, debug_bucket)
    return items[0] if items else {}

# -------------------------------------------------------
# 경로 주변 관광/맛집 수집 (30km 근사: 20km + 촘촘 샘플링)
# -------------------------------------------------------
def search_tour_items_along_route(geojson: Dict[str, Any], limit_each: int = 30) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """
    반환: (tour_list, food_list, probe_debug)
    probe_debug에는 첫 지점 진단(엔드포인트/키 모드/상태/resultCode)을 담아 클라이언트에서 확인 가능
    """
    try:
        coords = geojson["features"][0]["geometry"]["coordinates"]  # [ [lon,lat], ... ]
    except Exception:
        return [], [], {"message": "경로 좌표 파싱 실패"}

    n = len(coords)
    if n == 0:
        return [], [], {"message": "경로 좌표 없음"}

    # 진단: 첫 지점에서 두 타입 각각 1회 프로브
    probe = {"tour": [], "food": []}
    lon0, lat0 = coords[max(0, n // 2 - 1)]
    _ = _tourapi_location_based(lon0, lat0, 12, 20000, probe["tour"])
    _ = _tourapi_location_based(lon0, lat0, 39, 20000, probe["food"])

    seen: set = set()
    tours: List[Dict[str, Any]] = []
    foods: List[Dict[str, Any]] = []

    # 20km(최대권장) 반경으로 촘촘히, 부족하면 10km로 한 번 더 훑기
    def collect(radius_m: int, sample_goal: int):
        step = max(1, len(coords) // sample_goal)
        for idx in range(0, len(coords), step):
            lon, lat = coords[idx]
            # 관광(12)
            items = _tourapi_location_based(lon, lat, 12, radius_m)
            for it in items:
                cid = str(it.get("contentid") or "")
                if not cid or cid in seen:
                    continue
                seen.add(cid)
                intro = _tourapi_detail_intro(cid, 12)
                norm = _normalize_detail(it, intro, "tour")
                if _to_float(norm["mapx"]) is None or _to_float(norm["mapy"]) is None:
                    continue
                tours.append(norm)
                if len(tours) >= limit_each:
                    break
            # 맛집(39)
            items = _tourapi_location_based(lon, lat, 39, radius_m)
            for it in items:
                cid = str(it.get("contentid") or "")
                if not cid or cid in seen:
                    continue
                seen.add(cid)
                intro = _tourapi_detail_intro(cid, 39)
                norm = _normalize_detail(it, intro, "food")
                if _to_float(norm["mapx"]) is None or _to_float(norm["mapy"]) is None:
                    continue
                foods.append(norm)
                if len(foods) >= limit_each:
                    break
            if len(tours) >= limit_each and len(foods) >= limit_each:
                break

    collect(radius_m=20000, sample_goal=300)  # 20km, 촘촘히 → 30km 근사
    if len(tours) + len(foods) < 10:
        collect(radius_m=10000, sample_goal=400)  # 부족하면 더 촘촘히

    return tours, foods, {"message": "OK", "probe": probe}

# -------------------------------------------------------
# 라우팅 핸들러 (경로·요약·관광 팝업용 데이터 포함)
# -------------------------------------------------------
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

    # 경유지 (성공 로직 유지)
    way_sel = find_waypoints_along_direction(start, end, max_n=max_wps)
    if not way_sel and max_wps >= 1 and beach_coords:
        legacy = find_best_beach_waypoint_legacy(start, end)
        if legacy:
            way_sel = [(legacy[0], legacy[1], legacy[2], 0.5)]

    # ORS: start -> [wps] -> end
    points = [start] + [(lat, lon) for (_, lat, lon, _) in way_sel] + [end]
    route_data, status = get_ors_route_multi(points)
    if status != 200 or "error" in route_data:
        return jsonify({"error": route_data.get("error", f"OpenRouteService 실패({status})")}), status

    # 경로 요약(거리/시간) – index에서 표시/호버에 사용
    summary = {}
    try:
        f0 = route_data["features"][0]
        s = f0.get("properties", {}).get("summary", {})
        summary = {"distance": float(s.get("distance", 0.0)), "duration": float(s.get("duration", 0.0))}
    except Exception:
        summary = {"distance": 0.0, "duration": 0.0}

    # 경로 주변 관광/맛집 수집
    tour_items, food_items, probe = search_tour_items_along_route(route_data)
    all_items = tour_items + food_items

    # 경유지 표시용
    wp_objs = []
    for i, (name, lat, lon, t) in enumerate(way_sel, start=1):
        wp_objs.append({
            "order": i, "name": name, "lat": lat, "lon": lon, "t": t,
            "address": reverse_geocode_google(lat, lon) or ""
        })

    resp: Dict[str, Any] = {
        "route": route_data,
        "waypoints_used": wp_objs,
        "summary": summary,
        "spots": all_items,
        "spots_grouped": {"tour": tour_items, "food": food_items},
        "tourapi_probe": probe,  # 프런트 콘솔에서 상태 확인 가능
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

@app.route("/api/route", methods=["POST"])
def api_route():
    return _handle_route()

# 단건 테스트: /api/tourspot?lon=129.16&lat=35.16&radius=20000
@app.route("/api/tourspot", methods=["GET"])
def api_tourspot():
    lon = _to_float(request.args.get("lon"))
    lat = _to_float(request.args.get("lat"))
    radius = int(request.args.get("radius") or 20000)
    if lon is None or lat is None:
        return jsonify({"error": "lon/lat 파라미터 필요"}), 400

    debug: List[Dict[str, Any]] = []
    tours = _tourapi_location_based(lon, lat, 12, radius, debug)
    foods = _tourapi_location_based(lon, lat, 39, radius, debug)
    return jsonify({
        "debug": debug,
        "tour_count": len(tours),
        "food_count": len(foods),
        "samples": {
            "tour": tours[:2],
            "food": foods[:2],
        }
    }), 200

# 상세 페이지(옵션): contentid로 상세 공통정보 조회 (팝업의 '자세히'용)
@app.route("/tour_detail/<contentid>")
def tour_detail(contentid):
    # 간단히 KorService2 우선
    params = {
        "contentId": contentid,
        "defaultYN": "Y",
        "firstImageYN": "Y",
        "addrinfoYN": "Y",
        "mapinfoYN": "Y",
        "overviewYN": "Y",
        "_type": "json",
        "MobileOS": "ETC",
        "MobileApp": "CoastalDrive",
    }
    items = _tourapi_fetch("detailCommon1", params, [])
    item = items[0] if items else {}
    # HTML 간단 렌더
    html = f"""
    <meta charset="utf-8"><title>관광지 상세</title>
    <h2>{item.get('title','상세')}</h2>
    <p>{item.get('addr1','')}</p>
    <p>{item.get('overview','')}</p>
    """
    return Response(html, mimetype="text/html")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    log.info(f"Starting on 0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port)
