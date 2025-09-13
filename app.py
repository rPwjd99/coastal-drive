# app.py
# 실행 예:
#   Windows: set PORT=10000 && python app.py
#   macOS/Linux: export PORT=10000 && python app.py
# Render(권장): gunicorn -w 1 -k gthread -b 0.0.0.0:$PORT app:app

import os
import math
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from flask import Flask, request, jsonify, send_from_directory, redirect, Response

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
TOURAPI_KEY = os.getenv("TOURAPI_KEY") or "e1tU33wjMx2nynKjH8yDBm/S4YNne6B8mpCOWtzMH9TSONF71XG/xAwPqyv1fANpgeOvbPY+Le+gM6cYCnWV8w=="

# ------------------------
# index.html 안전 서빙 유틸
# ------------------------
def _find_index_html() -> Optional[Path]:
    # 1) 루트
    cand = APP_DIR / "index.html"
    if cand.is_file():
        return cand
    # 2) templates/index.html
    cand = APP_DIR / "templates" / "index.html"
    if cand.is_file():
        return cand
    # 3) static/index.html
    cand = APP_DIR / "static" / "index.html"
    if cand.is_file():
        return cand
    return None

def _fallback_index_html() -> str:
    # 최소 동작용 임시 페이지 (지도 + 경로 버튼). index.html이 없어도 200으로 응답.
    return """<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <title>Coastal Drive</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/ol@7.3.0/ol.css">
  <script src="https://cdn.jsdelivr.net/npm/ol@7.3.0/dist/ol.js"></script>
  <style>
    body { margin:0; font-family:system-ui,-apple-system,Segoe UI,Roboto,'Noto Sans KR',sans-serif; }
    #controls { padding:10px; background:#f7f7f7; border-bottom:1px solid #ddd; display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
    #controls input { width:280px; padding:8px 10px; border:1px solid #ccc; border-radius:6px; }
    #controls button { padding:8px 14px; border:0; border-radius:6px; background:#111; color:#fff; cursor:pointer; }
    #map { width:100%; height: calc(100vh - 64px); }
  </style>
</head>
<body>
  <div id="controls">
    <input id="start" placeholder="출발지 (예: 세종시청)">
    <input id="end" placeholder="도착지 (예: 속초시청)">
    <button id="btnRoute" type="button">경로 계산</button>
    <label>경유 최대
      <select id="maxwps">
        <option value="3" selected>3</option>
        <option value="2">2</option>
        <option value="1">1</option>
        <option value="0">0</option>
      </select>
    </label>
  </div>
  <div id="map"></div>
  <script>
    const map = new ol.Map({
      target: "map",
      layers: [ new ol.layer.Tile({ source: new ol.source.OSM() }) ],
      view: new ol.View({ center: ol.proj.fromLonLat([127.5, 36.5]), zoom: 7 })
    });
    let routeLayer = null;

    function drawRoute(coordsLonLat) {
      const coords3857 = coordsLonLat.map(([lon, lat]) => ol.proj.fromLonLat([lon, lat]));
      if (routeLayer) map.removeLayer(routeLayer);
      routeLayer = new ol.layer.Vector({
        source: new ol.source.Vector({
          features: [ new ol.Feature({ geometry: new ol.geom.LineString(coords3857) }) ]
        }),
        style: new ol.style.Style({ stroke: new ol.style.Stroke({ color: '#0066ff', width: 4 }) })
      });
      map.addLayer(routeLayer);
      map.getView().fit(new ol.geom.LineString(coords3857), { padding: [50,50,50,50], maxZoom: 12 });
    }

    document.getElementById('btnRoute').addEventListener('click', async () => {
      const start = document.getElementById('start').value.trim();
      const end = document.getElementById('end').value.trim();
      const max_waypoints = document.getElementById('maxwps').value;
      if (!start || !end) { alert('출발지와 도착지를 모두 입력하세요.'); return; }
      try {
        const res = await fetch('/route', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ start, end, max_waypoints })
        });
        const data = await res.json();
        if (!res.ok || !data.route) throw new Error(data.error || '경로 요청 실패');
        const coords = data.route.features[0].geometry.coordinates;
        drawRoute(coords);
      } catch (err) {
        alert('경로 요청 실패: ' + (err.message || err));
        console.error(err);
      }
    });
  </script>
</body>
</html>"""

@app.route("/", methods=["GET", "HEAD"])
def index():
    p = _find_index_html()
    if p:
        return send_from_directory(p.parent.as_posix(), p.name)
    # 파일이 없어도 404가 아닌 임시 페이지로 응답
    return Response(_fallback_index_html(), mimetype="text/html")

@app.route("/favicon.ico")
def favicon():
    return "", 204

# ------------------------
# 기본 유틸
# ------------------------
def haversine(lat1, lon1, lat2, lon2) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2.0)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2.0)**2
    return 2.0 * R * math.asin(math.sqrt(a))  # km

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
    except Exception:
        return None

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

def _coerce_json() -> Dict[str, Any]:
    j = request.get_json(silent=True, force=True)
    if isinstance(j, dict):
        return j
    if request.form:
        return {k: request.form.get(k) for k in request.form}
    if request.args:
        return {k: request.args.get(k) for k in request.args}
    return {}

# ------------------------
# 투영/코리도/우회비용 기반 경유지 선택 (최대 3개)
# ------------------------
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

def max_direct(val: float) -> float:
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
        if detour <= max_abs_detour_km and (detour / max_direct(base_direct)) <= max_rel_detour:
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

# ------------------------
# ORS 라우팅
# ------------------------
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

# ------------------------
# TourAPI 검색 및 상세
# ------------------------
def _tourapi_location_based(lon: float, lat: float, content_type_id: Optional[int] = None, num_rows: int = 10) -> List[Dict[str, Any]]:
    params = {
        "serviceKey": TOURAPI_KEY,
        "mapX": lon,
        "mapY": lat,
        "radius": 5000,
        "listYN": "Y",
        "arrange": "E",
        "numOfRows": num_rows,
        "pageNo": 1,
        "MobileOS": "ETC",
        "MobileApp": "SeaRoute",
        "_type": "json",
    }
    if content_type_id:
        params["contentTypeId"] = content_type_id
    try:
        r = requests.get("https://apis.data.go.kr/B551011/KorService1/locationBasedList1", params=params, timeout=20)
        items = r.json().get("response", {}).get("body", {}).get("items", {}).get("item", []) or []
        return items
    except Exception:
        return []

def _tourapi_detail_common(content_id: str) -> Dict[str, Any]:
    params = {
        "serviceKey": TOURAPI_KEY,
        "contentId": content_id,
        "defaultYN": "Y",
        "firstImageYN": "Y",
        "addrinfoYN": "Y",
        "mapinfoYN": "Y",
        "overviewYN": "N",
        "_type": "json",
        "MobileOS": "ETC",
        "MobileApp": "SeaRoute",
    }
    try:
        r = requests.get("https://apis.data.go.kr/B551011/KorService1/detailCommon1", params=params, timeout=20)
        items = r.json().get("response", {}).get("body", {}).get("items", {}).get("item", []) or []
        return items[0] if items else {}
    except Exception:
        return {}

def _tourapi_detail_intro(content_id: str, content_type_id: int) -> Dict[str, Any]:
    params = {
        "serviceKey": TOURAPI_KEY,
        "contentId": content_id,
        "contentTypeId": content_type_id,
        "_type": "json",
        "MobileOS": "ETC",
        "MobileApp": "SeaRoute",
    }
    try:
        r = requests.get("https://apis.data.go.kr/B551011/KorService1/detailIntro1", params=params, timeout=20)
        items = r.json().get("response", {}).get("body", {}).get("items", {}).get("item", []) or []
        return items[0] if items else {}
    except Exception:
        return {}

def _normalize_detail(item: Dict[str, Any], intro: Dict[str, Any], category: str) -> Dict[str, Any]:
    res = {
        "contentid": item.get("contentid"),
        "title": item.get("title"),
        "addr1": item.get("addr1"),
        "mapx": item.get("mapx"),
        "mapy": item.get("mapy"),
        "firstimage": item.get("firstimage"),
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

def search_tour_items_along_route(geojson: Dict[str, Any], limit_each: int = 25) -> Dict[str, List[Dict[str, Any]]]:
    try:
        coords = geojson["features"][0]["geometry"]["coordinates"]
    except Exception:
        return {"tour": [], "food": [], "all": []}

    seen = set()
    tour_items: List[Dict[str, Any]] = []
    food_items: List[Dict[str, Any]] = []

    step = max(1, len(coords) // 200)
    for idx in range(0, len(coords), step):
        lon, lat = coords[idx]

        for item in _tourapi_location_based(lon, lat, content_type_id=12, num_rows=10):
            cid = str(item.get("contentid"))
            if not cid or cid in seen:
                continue
            seen.add(cid)
            intro = _tourapi_detail_intro(cid, 12)
            tour_items.append(_normalize_detail(item, intro, "tour"))
            if len(tour_items) >= limit_each:
                break

        for item in _tourapi_location_based(lon, lat, content_type_id=39, num_rows=10):
            cid = str(item.get("contentid"))
            if not cid or cid in seen:
                continue
            seen.add(cid)
            intro = _tourapi_detail_intro(cid, 39)
            food_items.append(_normalize_detail(item, intro, "food"))
            if len(food_items) >= limit_each:
                break

        if len(tour_items) >= limit_each and len(food_items) >= limit_each:
            break

    all_items = tour_items + food_items
    return {"tour": tour_items, "food": food_items, "all": all_items}

# ------------------------
# 라우팅 핸들러
# ------------------------
def _handle_route():
    data = _coerce_json()
    start_in = data.get("start") or data.get("origin") or data.get("from")
    end_in = data.get("end") or data.get("destination") or data.get("to")
    max_wps = int(data.get("max_waypoints") or 3)
    max_wps = max(0, min(3, max_wps))

    if not start_in or not end_in:
        return jsonify({"error": "start/end 누락"}), 400

    start = geocode_google(start_in) if isinstance(start_in, str) else start_in
    end = geocode_google(end_in) if isinstance(end_in, str) else end_in
    if not start or not end:
        return jsonify({"error": "주소 변환 실패"}), 400

    way_sel = find_waypoints_along_direction(start, end, max_n=max_wps)
    if not way_sel and max_wps >= 1:
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
            "order": i,
            "name": name,
            "lat": lat,
            "lon": lon,
            "t": t,
            "address": reverse_geocode_google(lat, lon) or ""
        })

    resp = {
        "route": route_data,
        "waypoints_used": wp_objs,
        "spots": spots["all"],
        "spots_grouped": { "tour": spots["tour"], "food": spots["food"] }
    }
    if len(wp_objs) >= 1:
        resp["waypoint"] = wp_objs[0]
    if len(wp_objs) >= 2:
        resp["waypoint2"] = wp_objs[1]
    if len(wp_objs) >= 3:
        resp["waypoint3"] = wp_objs[2]

    return jsonify(resp), 200

@app.route("/route", methods=["POST", "GET"])
def route():
    if request.method == "GET":
        return redirect("/")
    return _handle_route()

@app.route("/api/route", methods=["POST"])
def api_route():
    return _handle_route()

@app.route("/healthz")
def healthz():
    return jsonify({"ok": True})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
