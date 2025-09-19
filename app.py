# app.py
import os
import math
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from functools import lru_cache

import requests
from flask import Flask, request, jsonify, send_from_directory, render_template
from flask_cors import CORS

# -------------------------
# 기본 설정/로그
# -------------------------
app = Flask(__name__, static_folder="static", template_folder="templates")
CORS(app)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("coastal-drive")

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
ORS_API_KEY = os.getenv("ORS_API_KEY")
# KorService(GW) 키(Decoding 값) 권장. 여기서는 ENV만 사용(하드코딩 X).
TOURAPI_KEY = os.getenv("TOURAPI_KEY")
# 우선 KorService2를 쓰되, 실패/빈응답시 KorService1로 자동 폴백
TOURAPI_BASE = (os.getenv("TOURAPI_BASE") or "https://apis.data.go.kr/B551011/KorService2").rstrip("/")

# 해변 후보 데이터
try:
    from beaches_coordinates import beach_coords  # {"해변명": (lon, lat)}
except Exception:
    beach_coords = {}

APP_DIR = Path(__file__).resolve().parent

# -------------------------
# 공통 유틸
# -------------------------
def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2.0)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2.0)**2
    return 2.0 * R * math.asin(math.sqrt(a))

def _to_float(x: Any) -> Optional[float]:
    try:
        return float(x)
    except Exception:
        return None

# -------------------------
# 지오코딩(Google)
# -------------------------
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

# -------------------------
# 경유지 선택 (기존 성공 로직 유지)
# -------------------------
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
        # 한국 연안 대략 필터
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

# -------------------------
# ORS 라우팅 (기존 유지)
# -------------------------
def get_ors_route_multi(points: List[Tuple[float, float]]) -> Tuple[Dict[str, Any], int]:
    if not ORS_API_KEY:
        return {"error": "ORS_API_KEY is missing"}, 500
    coords = [[lon, lat] for (lat, lon) in points]
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

# -------------------------
# TourAPI 호출 (안정화)
# -------------------------
def _clean_base(b: str) -> str:
    return (b or "").strip().rstrip("/")

def _try_location_based_once(base_url: str, lon: float, lat: float, content_type_id: Optional[int], radius_m: int, num_rows: int) -> List[Dict[str, Any]]:
    """단일 베이스에서 한 번 호출."""
    if not TOURAPI_KEY:
        return []
    url = f"{_clean_base(base_url)}/locationBasedList1"
    params = {
        "serviceKey": TOURAPI_KEY,
        "mapX": lon,
        "mapY": lat,
        "radius": max(1000, min(20000, int(radius_m))),  # TourAPI 제한: 1000~20000
        "listYN": "Y",
        "arrange": "E",
        "numOfRows": max(5, min(50, num_rows)),
        "pageNo": 1,
        "MobileOS": "ETC",
        "MobileApp": "CoastalDrive",
        "_type": "json",
    }
    if content_type_id:
        params["contentTypeId"] = content_type_id

    try:
        r = requests.get(url, params=params, timeout=8)
        # JSON 파싱 실패 시(=SOAP Fault/HTML) 텍스트 앞부분 로깅
        try:
            j = r.json()
        except Exception:
            txt = r.text[:180].replace("\n", " ")
            log.warning("[TourAPI] Non-JSON response(%s): %s ...", r.status_code, txt)
            return []

        items = j.get("response", {}).get("body", {}).get("items", {}).get("item", []) or []
        if isinstance(items, dict):
            items = [items]
        return items
    except Exception as e:
        log.warning("[TourAPI] request failed: %s", e)
        return []

def _location_based_fallback(lon: float, lat: float, content_type_id: Optional[int], radius_m: int, num_rows: int) -> List[Dict[str, Any]]:
    """KorService2 → 0건이면 KorService1로 폴백, 그래도 0이면 contentType 없이 느슨 재조회."""
    # 1차: KorService2
    items = _try_location_based_once(TOURAPI_BASE or "https://apis.data.go.kr/B551011/KorService2",
                                     lon, lat, content_type_id, radius_m, num_rows)
    if items:
        return items
    # 2차: KorService1
    items = _try_location_based_once("https://apis.data.go.kr/B551011/KorService1",
                                     lon, lat, content_type_id, radius_m, num_rows)
    if items:
        return items
    # 3차: contentType 없이(느슨)
    items = _try_location_based_once("https://apis.data.go.kr/B551011/KorService1",
                                     lon, lat, None, radius_m, num_rows)
    return items

def _normalize_item(raw: Dict[str, Any], category: str) -> Optional[Dict[str, Any]]:
    lon = _to_float(raw.get("mapx"))
    lat = _to_float(raw.get("mapy"))
    if lon is None or lat is None:
        return None
    return {
        "contentid": str(raw.get("contentid", "")),
        "title": raw.get("title") or "",
        "addr1": raw.get("addr1") or "",
        "mapx": lon,
        "mapy": lat,
        "firstimage": raw.get("firstimage") or "",
        "tel": raw.get("tel") or "",
        "homepage": raw.get("homepage") or "",
        "category": category,
    }

def _polyline_samples(coords: List[List[float]], interval_km: float = 8.0, max_samples: int = 50) -> List[Tuple[float, float]]:
    """경로 좌표(경도,위도) 리스트를 일정 거리 간격으로 샘플링."""
    if not coords:
        return []
    samples: List[Tuple[float, float]] = []
    last_lat = coords[0][1]
    last_lon = coords[0][0]
    samples.append((last_lon, last_lat))
    acc = 0.0
    for i in range(1, len(coords)):
        lon, lat = coords[i]
        d = haversine(last_lat, last_lon, lat, lon)
        acc += d
        if acc >= interval_km:
            samples.append((lon, lat))
            acc = 0.0
            if len(samples) >= max_samples:
                break
        last_lat, last_lon = lat, lon
    # 도착점 보장
    if samples and samples[-1] != (coords[-1][0], coords[-1][1]):
        samples.append((coords[-1][0], coords[-1][1]))
    return samples

def search_tour_items_along_route(geojson: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """경로 주변 관광/맛집 검색(반경 20km 제한 하에서 다중 샘플링 + 폴백)."""
    try:
        coords = geojson["features"][0]["geometry"]["coordinates"]
    except Exception:
        return {"tour": [], "food": [], "all": []}

    # 20km가 최대이므로 반경을 키우는 대신 '조밀 샘플링'으로 커버한다.
    interval_km = 8.0
    radius_m = 20000
    samples = _polyline_samples(coords, interval_km=interval_km, max_samples=50)
    log.info("[TourAPI] samples=%d radius=%dm interval=%.1fkm", len(samples), radius_m, interval_km)

    seen: set = set()
    tours: List[Dict[str, Any]] = []
    foods: List[Dict[str, Any]] = []

    # contentTypeId 12 → 관광
    for (lon, lat) in samples:
        items = _location_based_fallback(lon, lat, content_type_id=12, radius_m=radius_m, num_rows=20)
        for raw in items:
            cid = str(raw.get("contentid") or "")
            if not cid or cid in seen:
                continue
            seen.add(cid)
            norm = _normalize_item(raw, "tour")
            if norm:
                tours.append(norm)
        if len(tours) >= 40:  # 과도 수집 방지
            break

    # contentTypeId 39 → 음식
    for (lon, lat) in samples:
        items = _location_based_fallback(lon, lat, content_type_id=39, radius_m=radius_m, num_rows=20)
        for raw in items:
            cid = str(raw.get("contentid") or "")
            if not cid or cid in seen:
                continue
            seen.add(cid)
            norm = _normalize_item(raw, "food")
            if norm:
                foods.append(norm)
        if len(foods) >= 40:
            break

    log.info("[TourAPI] collected tour=%d food=%d total=%d", len(tours), len(foods), len(tours)+len(foods))
    all_items = tours + foods
    return {"tour": tours, "food": foods, "all": all_items}

# -------------------------
# 라우팅 핸들러 (경로 로직 유지)
# -------------------------
def _coerce_json() -> Dict[str, Any]:
    j = request.get_json(silent=True, force=True)
    return j if isinstance(j, dict) else {}

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

    # 경유지 선택(기존 성공 로직)
    way_sel = find_waypoints_along_direction(start, end, max_n=max_wps)
    if not way_sel and max_wps >= 1 and beach_coords:
        legacy = find_best_beach_waypoint_legacy(start, end)
        if legacy:
            way_sel = [(legacy[0], legacy[1], legacy[2], 0.5)]

    points = [start] + [(lat, lon) for (_, lat, lon, _) in way_sel] + [end]
    route_data, status = get_ors_route_multi(points)
    if status != 200 or "error" in route_data:
        return jsonify({"error": route_data.get("error", f"OpenRouteService 실패({status})")}), status

    # 경로 주변 관광지/맛집 (보강판)
    spots = search_tour_items_along_route(route_data)

    # 경유지 응답 구성
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
        "spots_grouped": { "tour": spots["tour"], "food": spots["food"] }
    }
    if len(wp_objs) >= 1: resp["waypoint"] = wp_objs[0]
    if len(wp_objs) >= 2: resp["waypoint2"] = wp_objs[1]
    if len(wp_objs) >= 3: resp["waypoint3"] = wp_objs[2]

    return jsonify(resp), 200

# -------------------------
# 라우팅/템플릿 라우트
# -------------------------
@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")

@app.route("/route", methods=["POST"])
def route():
    return _handle_route()

# 헬스체크
@app.route("/healthz", methods=["GET"])
def healthz():
    return jsonify({"ok": True})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    log.info(f"Starting on 0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port)
