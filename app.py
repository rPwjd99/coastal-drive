# app.py
import os
import math
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from functools import lru_cache

import requests
from flask import Flask, request, jsonify, render_template, send_from_directory, Response, redirect

# ---------- 환경 ----------

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
APP_DIR = Path(__file__).resolve().parent

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
ORS_API_KEY    = os.getenv("ORS_API_KEY", "")
TOURAPI_KEY    = os.getenv("TOURAPI_KEY", "")
TOURAPI_BASE   = (os.getenv("TOURAPI_BASE") or "https://apis.data.go.kr/B551011/KorService2").rstrip("/")

# ---------- 공통 유틸 ----------

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

# ---------- Google Geocoding ----------

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

# ---------- 경유지 선택 (이미 성공한 로직 유지) ----------

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
    # 이미 성공한 경유지 선택 규칙 유지
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

def find_best_beach_waypoint_legacy(start: Tuple[float, float], end: Tuple[float, float]) -> Optional[Tuple[str, float, float]]:
    # 레거시 폴백 (1개 경유지)
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

# ---------- ORS 라우팅 ----------

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

# ---------- TourAPI 래퍼 (KorService2 → 1 폴백) ----------

def _tourapi_try(base: str, ep: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        r = requests.get(f"{base.rstrip('/')}/{ep}", params=params, timeout=12, headers={"Accept": "application/json"})
        # JSON이 아니면 실패 취급(게이트웨이 SOAP Fault 등)
        if "json" not in (r.headers.get("Content-Type", "")).lower():
            return None
        return r.json()
    except Exception:
        return None

def _tourapi_get(ep: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not TOURAPI_KEY:
        return None
    # 필수 공통 파라미터
    base_params = {
        "serviceKey": TOURAPI_KEY,
        "MobileOS": "ETC",
        "MobileApp": "CoastalDrive",
        "_type": "json",
    }
    q = {**base_params, **params}

    # 1) 사용자가 지정한 BASE 또는 KorService2 시도
    bases = []
    if TOURAPI_BASE:
        bases.append(TOURAPI_BASE)
    if "KorService2" not in TOURAPI_BASE:
        bases.append("https://apis.data.go.kr/B551011/KorService2")
    # 2) 실패 시 KorService1 폴백
    bases.append("https://apis.data.go.kr/B551011/KorService1")

    for b in bases:
        j = _tourapi_try(b, ep, q)
        if j:
            # 성공/실패는 items 존재 여부로 판단
            items = (((j.get("response", {}) or {}).get("body", {}) or {}).get("items", {}) or {}).get("item", None)
            if items is not None:
                return j
    return None

@lru_cache(maxsize=4096)
def _location_based(lon: float, lat: float, content_type_id: int, radius_m: int, num_rows: int) -> List[Dict[str, Any]]:
    params = {
        "mapX": lon, "mapY": lat,
        "radius": min(max(1000, radius_m), 20000),  # TourAPI 최대 20km
        "listYN": "Y", "arrange": "E",
        "numOfRows": num_rows, "pageNo": 1,
        "contentTypeId": content_type_id,
    }
    j = _tourapi_get("locationBasedList1", params)
    if not j:
        return []
    items = (((j.get("response", {}) or {}).get("body", {}) or {}).get("items", {}) or {}).get("item", []) or []
    if isinstance(items, dict):
        items = [items]
    return items

@lru_cache(maxsize=8192)
def _detail_intro(content_id: str, content_type_id: int) -> Dict[str, Any]:
    params = {"contentId": content_id, "contentTypeId": content_type_id}
    j = _tourapi_get("detailIntro1", params)
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
    # detailIntro1 매핑
    if category == "tour":  # contentTypeId 12
        res["openhour"] = intro.get("usetime") or ""
        res["restday"] = intro.get("restdate") or ""
        res["parking_info"] = intro.get("parking") or ""
    elif category == "food":  # contentTypeId 39
        res["openhour"] = intro.get("opentimefood") or ""
        res["restday"] = intro.get("restdatefood") or ""
        res["parking_info"] = intro.get("parkingfood") or ""
    else:
        res["openhour"] = res["restday"] = res["parking_info"] = ""
    return res

def search_along_route(geojson: Dict[str, Any], radius_m: int = 20000, sample_goal: int = 220,
                       limit_each: int = 60, intro_limit_each: int = 30) -> Dict[str, List[Dict[str, Any]]]:
    # 경로 좌표
    try:
        coords = geojson["features"][0]["geometry"]["coordinates"]  # [lon, lat] 리스트
    except Exception:
        return {"tour": [], "food": [], "all": []}

    # 샘플링 (경로 길이에 따라 200~220 지점 정도)
    step = max(1, len(coords) // max(1, sample_goal))
    seen: set = set()
    # contentTypeId: 관광지(12), 음식(39)
    tour_raw: List[Dict[str, Any]] = []
    food_raw: List[Dict[str, Any]] = []

    # 각 샘플 지점에서 20km 내 검색
    for idx in range(0, len(coords), step):
        lon, lat = coords[idx]
        for item in _location_based(lon, lat, 12, radius_m, num_rows=20):
            cid = str(item.get("contentid") or "")
            if not cid or cid in seen: continue
            seen.add(cid)
            tour_raw.append((lat, lon, item))
            if len(tour_raw) >= limit_each: break
        for item in _location_based(lon, lat, 39, radius_m, num_rows=20):
            cid = str(item.get("contentid") or "")
            if not cid or cid in seen: continue
            seen.add(cid)
            food_raw.append((lat, lon, item))
            if len(food_raw) >= limit_each: break
        if len(tour_raw) >= limit_each and len(food_raw) >= limit_each:
            break

    # detailIntro1 (팝업용 확장: 운영/휴무/주차) - 과부하 방지 위해 일부만
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

    return {"tour": tour_items, "food": food_items, "all": tour_items + food_items}

# ---------- Flask 라우트 ----------

@app.route("/")
def index():
    # templates/index.html 있으면 렌더, 없으면 임시 페이지
    p = APP_DIR / "templates" / "index.html"
    if p.is_file():
        return render_template("index.html")
    return Response("<h3>index.html not found</h3>", mimetype="text/html")

@app.route("/tour_detail/<contentid>")
def tour_detail(contentid: str):
    # 상세(overview 등)
    params = {
        "contentId": contentid,
        "defaultYN": "Y",
        "firstImageYN": "Y",
        "addrinfoYN": "Y",
        "mapinfoYN": "Y",
        "overviewYN": "Y",
    }
    j = _tourapi_get("detailCommon1", params) or {}
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

    # 경유지: 기존 성공 로직 유지
    way_sel = find_waypoints_along_direction(start, end, max_n=max_wps)
    if not way_sel and max_wps >= 1 and beach_coords:
        legacy = find_best_beach_waypoint_legacy(start, end)
        if legacy:
            way_sel = [(legacy[0], legacy[1], legacy[2], 0.5)]

    # 라우팅: start → [waypoints] → end
    points = [start] + [(lat, lon) for (_, lat, lon, _) in way_sel] + [end]
    route_data, status = get_ors_route_multi(points)
    if status != 200 or "error" in route_data:
        return jsonify({"error": route_data.get("error", f"OpenRouteService 실패({status})")}), status

    # 경로 주변 관광/맛집 수집 (반경 20km, 다지점 샘플)
    spots = search_along_route(route_data, radius_m=20000, sample_goal=220, limit_each=60, intro_limit_each=30)

    # 응답 구성
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
