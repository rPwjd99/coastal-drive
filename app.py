# app.py
import os
import math
import logging
from typing import Any, Dict, List, Optional, Tuple
from functools import lru_cache

import requests
from flask import Flask, request, jsonify, render_template, Response, redirect

# ----------------- 환경 -----------------
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

try:
    from beaches_coordinates import beach_coords  # {"해변명": (lon, lat), ...}
except Exception:
    beach_coords = {}

app = Flask(__name__, static_folder="static", template_folder="templates")
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("coastal-drive")

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
ORS_API_KEY    = os.getenv("ORS_API_KEY", "")
TOURAPI_KEY    = os.getenv("TOURAPI_KEY", "")
TOURAPI_BASE   = (os.getenv("TOURAPI_BASE") or "https://apis.data.go.kr/B551011/KorService2").rstrip("/")

# ----------------- 유틸 -----------------
def _to_float(x: Any) -> Optional[float]:
    try:
        return float(x)
    except Exception:
        return None

def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2.0)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2.0)**2
    return 2.0 * R * math.asin(math.sqrt(a))  # km

def _coerce_json() -> Dict[str, Any]:
    j = request.get_json(silent=True, force=True)
    if isinstance(j, dict): return j
    if request.form: return {k: request.form.get(k) for k in request.form}
    if request.args: return {k: request.args.get(k) for k in request.args}
    return {}

# ----------------- 구글 지오코딩 -----------------
def geocode_google(address: str) -> Optional[Tuple[float, float]]:
    if not GOOGLE_API_KEY or not address:
        return None
    try:
        r = requests.get(
            "https://maps.googleapis.com/maps/api/geocode/json",
            params={"address": address, "key": GOOGLE_API_KEY},
            timeout=12,
        )
        j = r.json()
        loc = j["results"][0]["geometry"]["location"]
        return float(loc["lat"]), float(loc["lng"])
    except Exception as e:
        log.warning("geocode_google failed: %s", e)
        return None

def reverse_geocode_google(lat: float, lon: float) -> str:
    if not GOOGLE_API_KEY:
        return ""
    try:
        r = requests.get(
            "https://maps.googleapis.com/maps/api/geocode/json",
            params={"latlng": f"{lat},{lon}", "key": GOOGLE_API_KEY},
            timeout=12,
        )
        return r.json()["results"][0]["formatted_address"]
    except Exception:
        return ""

# ----------------- (성공했던) 경유지 로직 유지 -----------------
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
    if denom <= 0: return 0.0, float("inf")
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

def _max_direct(x: float) -> float:
    return max(1e-6, x)

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
    if not cands: return []

    sel: List[Tuple[str, float, float, float]] = []
    base_direct = haversine(start[0], start[1], end[0], end[1])
    chain_points: List[Tuple[float, float]] = [start]
    for t, name, lat, lon in cands:
        tentative_points = chain_points + [(lat, lon)]
        chain_len = _approx_chain_length(tentative_points, end)
        detour = chain_len - base_direct
        if detour <= max_abs_detour_km and detour / _max_direct(base_direct) <= max_rel_detour:
            sel.append((name, lat, lon, t))
            chain_points.append((lat, lon))
            if len(sel) >= max_n:
                break
    return sel

# ----------------- ORS 라우팅 -----------------
def get_ors_route_multi(points: List[Tuple[float, float]]) -> Tuple[Dict[str, Any], int]:
    if not ORS_API_KEY:
        return {"error": "ORS_API_KEY is missing"}, 500
    coords = [[lon, lat] for (lat, lon) in points]  # ORS: [lon, lat]
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

# ----------------- 경로 좌표 → 거리 간격 샘플 -----------------
def resample_by_distance(coords: List[List[float]], interval_km: float = 12.0, max_samples: int = 40) -> List[Tuple[float, float]]:
    """coords: [[lon,lat],...] → interval_km 간격으로 최대 max_samples개 추출"""
    if not coords: return []
    pts = []
    last_pick = coords[0]
    last_lat, last_lon = last_pick[1], last_pick[0]
    pts.append((last_lon, last_lat))
    acc = 0.0
    for i in range(1, len(coords)):
        lon, lat = coords[i][0], coords[i][1]
        acc += haversine(last_lat, last_lon, lat, lon)
        last_lat, last_lon = lat, lon
        if acc >= interval_km:
            pts.append((lon, lat))
            acc = 0.0
            if len(pts) >= max_samples:
                break
    return pts

# ----------------- TourAPI 래퍼 -----------------
def _tourapi_try(base: str, ep: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        r = requests.get(f"{base.rstrip('/')}/{ep}", params=params, timeout=12, headers={"Accept": "application/json"})
        # JSON이 아니면 실패 (SOAP Fault 등)
        if "json" not in (r.headers.get("Content-Type", "")).lower():
            return None
        return r.json()
    except Exception:
        return None

def _tourapi_get(ep: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not TOURAPI_KEY:
        return None
    base_params = {
        "serviceKey": TOURAPI_KEY,
        "MobileOS": "ETC",
        "MobileApp": "CoastalDrive",
        "_type": "json",
    }
    q = {**base_params, **params}

    # 우선 지정 BASE / KorService2, 실패 시 KorService1
    bases = []
    if TOURAPI_BASE:
        bases.append(TOURAPI_BASE)
    if "KorService2" not in TOURAPI_BASE:
        bases.append("https://apis.data.go.kr/B551011/KorService2")
    bases.append("https://apis.data.go.kr/B551011/KorService1")

    for b in bases:
        j = _tourapi_try(b, ep, q)
        if j:
            return j
    return None

@lru_cache(maxsize=4096)
def _location_based_once(lon: float, lat: float, radius_m: int, content_type_id: Optional[int], num_rows: int) -> List[Dict[str, Any]]:
    p = {
        "mapX": lon, "mapY": lat,
        "radius": min(max(1000, radius_m), 20000),
        "listYN": "Y", "arrange": "E",
        "numOfRows": num_rows, "pageNo": 1,
    }
    if content_type_id:
        p["contentTypeId"] = content_type_id

    j = _tourapi_get("locationBasedList1", p)
    if not j:
        return []

    body = (j.get("response") or {}).get("body") or {}
    total = body.get("totalCount")
    try:
        log.info(f"[TourAPI] locationBasedList1 ct={content_type_id} total={total} @({lat:.4f},{lon:.4f}) r={p['radius']}")
    except Exception:
        pass

    items = ((body.get("items") or {}).get("item") or [])
    if isinstance(items, dict):
        items = [items]
    return items

def _location_based_with_fallback(lon: float, lat: float, radius_m: int, content_type_id: int, num_rows: int) -> List[Dict[str, Any]]:
    # 1) contentTypeId 지정
    items = _location_based_once(lon, lat, radius_m, content_type_id, num_rows)
    if items:
        return items
    # 2) 0개면 contentTypeId 없이 느슨 조회
    return _location_based_once(lon, lat, radius_m, None, num_rows)

@lru_cache(maxsize=4096)
def _detail_intro(content_id: str, content_type_id: int) -> Dict[str, Any]:
    p = {"contentId": content_id, "contentTypeId": content_type_id}
    j = _tourapi_get("detailIntro1", p)
    if not j:
        return {}
    items = (((j.get("response", {}) or {}).get("body", {}) or {}).get("items", {}) or {}).get("item", []) or []
    if isinstance(items, dict):
        items = [items]
    return items[0] if items else {}

def _normalize(item: Dict[str, Any], intro: Dict[str, Any], category: str, ref_lat: float, ref_lon: float) -> Optional[Dict[str, Any]]:
    mapx = _to_float(item.get("mapx"))
    mapy = _to_float(item.get("mapy"))
    if mapx is None or mapy is None:
        return None
    d_km = haversine(ref_lat, ref_lon, mapy, mapx)

    res = {
        "contentid": str(item.get("contentid") or ""),
        "title": item.get("title") or "",
        "addr1": item.get("addr1") or "",
        "mapx": mapx,
        "mapy": mapy,
        "firstimage": item.get("firstimage") or "",
        "tel": item.get("tel") or "",
        "homepage": item.get("homepage") or "",
        "distance": round(d_km, 2),
        "category": category
    }
    # intro 매핑
    if category == "tour":
        res["openhour"] = intro.get("usetime") or ""
        res["restday"] = intro.get("restdate") or ""
        res["parking_info"] = intro.get("parking") or ""
    elif category == "food":
        res["openhour"] = intro.get("opentimefood") or ""
        res["restday"] = intro.get("restdatefood") or ""
        res["parking_info"] = intro.get("parkingfood") or ""
    else:
        res["openhour"] = res["restday"] = res["parking_info"] = ""
    return res

def search_along_route(geojson: Dict[str, Any],
                       radius_m: int = 20000,
                       interval_km: float = 12.0,
                       max_samples: int = 40,
                       list_rows: int = 20,
                       intro_limit_each: int = 15) -> Dict[str, List[Dict[str, Any]]]:
    """경로를 12km 간격, 최대 40지점 샘플 → 각 지점 반경 20km 검색"""
    try:
        coords = geojson["features"][0]["geometry"]["coordinates"]  # [lon,lat]
    except Exception:
        return {"tour": [], "food": [], "all": []}

    samples = resample_by_distance(coords, interval_km=interval_km, max_samples=max_samples)
    log.info(f"[TourAPI] samples={len(samples)} radius={radius_m}m interval={interval_km}km")

    seen: set = set()
    tour_raw: List[Tuple[float, float, Dict[str, Any]]] = []
    food_raw: List[Tuple[float, float, Dict[str, Any]]] = []

    for (lon, lat) in samples:
        # 관광(12)
        for it in _location_based_with_fallback(lon, lat, radius_m, 12, list_rows):
            cid = str(it.get("contentid") or "")
            if cid and cid not in seen:
                seen.add(cid)
                tour_raw.append((lat, lon, it))
        # 맛집(39)
        for it in _location_based_with_fallback(lon, lat, radius_m, 39, list_rows):
            cid = str(it.get("contentid") or "")
            if cid and cid not in seen:
                seen.add(cid)
                food_raw.append((lat, lon, it))

    # intro(운영/휴무/주차) - 과다호출 방지
    tour_items: List[Dict[str, Any]] = []
    for i, (lat, lon, it) in enumerate(tour_raw):
        intro = _detail_intro(str(it.get("contentid") or ""), 12) if i < intro_limit_each else {}
        norm = _normalize(it, intro, "tour", lat, lon)
        if norm: tour_items.append(norm)

    food_items: List[Dict[str, Any]] = []
    for i, (lat, lon, it) in enumerate(food_raw):
        intro = _detail_intro(str(it.get("contentid") or ""), 39) if i < intro_limit_each else {}
        norm = _normalize(it, intro, "food", lat, lon)
        if norm: food_items.append(norm)

    log.info(f"[TourAPI] collected tour={len(tour_items)} food={len(food_items)} total={len(tour_items)+len(food_items)}")
    return {"tour": tour_items, "food": food_items, "all": tour_items + food_items}

# ----------------- Flask 라우트 -----------------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/tour_detail/<contentid>")
def tour_detail(contentid: str):
    p = {
        "contentId": contentid,
        "defaultYN": "Y",
        "firstImageYN": "Y",
        "addrinfoYN": "Y",
        "mapinfoYN": "Y",
        "overviewYN": "Y",
    }
    j = _tourapi_get("detailCommon1", p) or {}
    items = (((j.get("response", {}) or {}).get("body", {}) or {}).get("items", {}) or {}).get("item", []) or []
    if isinstance(items, dict):
        items = [items]
    item = items[0] if items else {}
    return render_template("tour_detail.html", item=item)

def _handle_route():
    data = _coerce_json()
    start_in = data.get("start") or data.get("origin") or data.get("from")
    end_in   = data.get("end")   or data.get("destination") or data.get("to")
    max_wps  = int(data.get("max_waypoints") or 3)
    max_wps  = max(0, min(3, max_wps))

    if not start_in or not end_in:
        return jsonify({"error": "start/end 누락"}), 400

    start = geocode_google(start_in) if isinstance(start_in, str) else tuple(start_in) if isinstance(start_in, (list, tuple)) else None
    end   = geocode_google(end_in)   if isinstance(end_in, str)   else tuple(end_in)   if isinstance(end_in, (list, tuple))   else None
    if not start or not end:
        return jsonify({"error": "주소 변환 실패"}), 400

    # (성공했던) 경유지 선택 로직 유지
    def find_waypoints_along_direction_wrapper():
        return find_waypoints_along_direction(start, end, max_n=max_wps)

    way_sel = find_waypoints_along_direction_wrapper()
    if not way_sel and max_wps >= 1 and beach_coords:
        # 레거시 1개 폴백
        # 사용자 코드에서 쓰던 레거시를 소극적으로만 사용
        try:
            from beaches_coordinates import beach_coords as bc
        except Exception:
            bc = {}
        if bc:
            # 가장 중앙쯤의 해안 한 곳이라도
            for name, (lon, lat) in bc.items():
                way_sel = [(name, lat, lon, 0.5)]
                break

    # ORS 경로: start -> [waypoints] -> end
    points = [start] + [(lat, lon) for (_, lat, lon, _) in way_sel] + [end]
    route_data, status = get_ors_route_multi(points)
    if status != 200 or "error" in route_data:
        return jsonify({"error": route_data.get("error", f"OpenRouteService 실패({status})")}), status

    # 경로 주변 관광/맛집 수집
    spots = search_along_route(
        route_data,
        radius_m=20000,       # 20km (최대)
        interval_km=12.0,     # 12km 간격
        max_samples=40,       # 최대 40포인트
        list_rows=20,         # 포인트당 최대 20개
        intro_limit_each=15,  # intro 조회는 카테고리당 15개까지만
    )

    # 응답
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
        "spots_grouped": {"tour": spots["tour"], "food": spots["food"]}
    }
    if len(wp_objs) >= 1: resp["waypoint"]  = wp_objs[0]
    if len(wp_objs) >= 2: resp["waypoint2"] = wp_objs[1]
    if len(wp_objs) >= 3: resp["waypoint3"] = wp_objs[2]

    return jsonify(resp), 200

@app.route("/route", methods=["POST", "GET"])
def route():
    if request.method == "GET":
        return redirect("/")
    return _handle_route()

@app.route("/healthz")
def healthz():
    return jsonify({"ok": True})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    log.info(f"Starting on 0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port)
