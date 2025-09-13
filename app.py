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

import requests
from flask import Flask, request, jsonify, send_from_directory, redirect, Response
from flask_cors import CORS

# .env 사용 (선택)
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
TOURAPI_KEY = os.getenv("TOURAPI_KEY") or "e1tU33wjMx2nynKjH8yDBm/S4YNne6B8mpCOWtzMH9TSONF71XG/xAwPqyv1fANpgeOvbPY+Le+gM6cYCnWV8w=="

# =========================================================
# index.html 서빙 (없으면 임시 페이지 제공 - 라인 호버 팝업 포함)
# =========================================================

def _find_index_html() -> Optional[Path]:
    for p in [APP_DIR / "index.html", APP_DIR / "templates" / "index.html", APP_DIR / "static" / "index.html"]:
        if p.is_file():
            return p
    return None


def _fallback_index_html() -> str:
    # 최소 UI. 독립 index.html을 별도로 제공하니, 이건 비상시에만 사용됩니다.
    return """<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8"><title>Coastal Drive</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/ol@7.3.0/ol.css">
<script src="https://cdn.jsdelivr.net/npm/ol@7.3.0/dist/ol.js"></script>
<style>
body{margin:0;font-family:system-ui,-apple-system,Segoe UI,Roboto,'Noto Sans KR',sans-serif}
#controls{padding:10px;background:#f7f7f7;border-bottom:1px solid #ddd;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
#controls input{width:280px;padding:8px 10px;border:1px solid #ccc;border-radius:6px}
#controls button{padding:8px 14px;border:0;border-radius:6px;background:#111;color:#fff;cursor:pointer}
#map{width:100%;height:calc(100vh - 64px)}
.ol-overlaycontainer,.ol-overlaycontainer-stopevent{z-index:1000 !important}
.ol-popup{position:absolute;background:#fff;box-shadow:0 1px 4px rgba(0,0,0,.3);padding:10px 12px;border-radius:8px;border:1px solid #ccc;min-width:220px}
.popup-title{font-weight:600;margin-bottom:6px}
</style></head><body>
<div id="controls">
  <input id="start" placeholder="출발지">
  <input id="end" placeholder="도착지">
  <button id="btnRoute">경로 계산</button>
</div>
<div id="map"></div>
<script>
const map=new ol.Map({target:"map",layers:[new ol.layer.Tile({source:new ol.source.OSM()})],view:new ol.View({center:ol.proj.fromLonLat([127.5,36.5]),zoom:7})});
let routeLayer=null;
const overlayEl=document.createElement('div'); overlayEl.className='ol-popup';
const hoverOverlay=new ol.Overlay({element:overlayEl,autoPan:false}); map.addOverlay(hoverOverlay);

function fmtDist(m){if(!isFinite(m))return"";return m>=1000?(m/1000).toFixed(1)+" km":Math.round(m)+" m";}
function fmtDur(s){if(!isFinite(s))return"";const h=Math.floor(s/3600),m=Math.round((s%3600)/60);return(h?`${h}시간 `:"")+`${m}분`; }

function drawRoute(coords, summary, counts){
  const coords3857=coords.map(([lon,lat])=>ol.proj.fromLonLat([lon,lat]));
  if(routeLayer) map.removeLayer(routeLayer);
  const feat=new ol.Feature({geometry:new ol.geom.LineString(coords3857)});
  feat.setProperties({category:'route',distance:summary?.distance||NaN,duration:summary?.duration||NaN,tourCount:counts?.tour||0,foodCount:counts?.food||0});
  routeLayer=new ol.layer.Vector({source:new ol.source.Vector({features:[feat]}),style:new ol.style.Style({stroke:new ol.style.Stroke({color:'#0066ff',width:4})}),zIndex:10});
  map.addLayer(routeLayer);
  map.getView().fit(new ol.geom.LineString(coords3857),{padding:[50,50,50,50],maxZoom:12});
}

map.on('pointermove',(evt)=>{
  const pixel=evt.pixel;
  let hit=false, f=null;
  map.forEachFeatureAtPixel(pixel,function(feat,layer){f=feat; return true;},{hitTolerance:8});
  if(f && f.get('category')==='route'){
    hit=true;
    const dist=fmtDist(f.get('distance')); const dur=fmtDur(f.get('duration'));
    const tc=f.get('tourCount')||0, fc=f.get('foodCount')||0;
    overlayEl.innerHTML=`<div class="popup-title">경로 요약</div>거리 ${dist} · 예상 ${dur}<br>관광지 ${tc} · 맛집 ${fc}`;
    hoverOverlay.setPosition(evt.coordinate);
    map.getTargetElement().style.cursor='pointer';
  }else{
    hoverOverlay.setPosition(undefined);
    map.getTargetElement().style.cursor='';
  }
});

document.getElementById('btnRoute').addEventListener('click', async ()=>{
  const start=document.getElementById('start').value.trim();
  const end=document.getElementById('end').value.trim();
  if(!start||!end){alert('출발지/도착지를 입력하세요.');return;}
  const res=await fetch('/route',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({start,end})});
  const data=await res.json();
  if(!res.ok||!data.route){alert(data.error||'경로 실패');return;}
  const feat=data.route.features[0], coords=feat.geometry.coordinates, summary=feat.properties?.summary||null;
  const counts={tour:(data.spots_grouped?.tour||[]).length,food:(data.spots_grouped?.food||[]).length};
  drawRoute(coords, summary, counts);
});
</script></body></html>"""

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

def _to_float(s: Any) -> Optional[float]:
    try:
        return float(s)
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

# =========================================================
# 경유지 선택 (기존 로직 유지)
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

# =========================================================
# ORS 라우팅
# =========================================================

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

# =========================================================
# TourAPI (경로 주변 검색) - 30km 코리도 기본
# =========================================================

@lru_cache(maxsize=4096)
def _tourapi_location_based_cached(lon: float, lat: float, content_type_id: int, radius_m: int) -> Tuple[Dict[str, Any], int]:
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
    try:
        r = requests.get("https://apis.data.go.kr/B551011/KorService1/locationBasedList1", params=params, timeout=10)
        return r.json(), r.status_code
    except Exception as e:
        return {"error": str(e)}, 500

def _tourapi_location_based(lon: float, lat: float, content_type_id: Optional[int], num_rows: int, radius_m: int) -> List[Dict[str, Any]]:
    if content_type_id is None:
        return []
    # 1차: 요청 반경 시도, 실패할 경우 20km로 폴백
    tries = [radius_m] + ([20000] if radius_m > 20000 else [])
    for rad in tries:
        data, status = _tourapi_location_based_cached(lon, lat, content_type_id, rad)
        if status != 200:
            continue
        try:
            items = data.get("response", {}).get("body", {}).get("items", {}).get("item", []) or []
            if isinstance(items, dict):
                items = [items]
            return items[:num_rows]
        except Exception:
            return []
    return []

@lru_cache(maxsize=4096)
def _tourapi_detail_intro_cached(content_id: str, content_type_id: int) -> Tuple[Dict[str, Any], int]:
    params = {
        "serviceKey": TOURAPI_KEY,
        "contentId": content_id,
        "contentTypeId": content_type_id,
        "_type": "json",
        "MobileOS": "ETC",
        "MobileApp": "SeaRoute",
    }
    try:
        r = requests.get("https://apis.data.go.kr/B551011/KorService1/detailIntro1", params=params, timeout=10)
        return r.json(), r.status_code
    except Exception as e:
        return {"error": str(e)}, 500

def _tourapi_detail_intro(content_id: str, content_type_id: int) -> Dict[str, Any]:
    data, status = _tourapi_detail_intro_cached(content_id, content_type_id)
    if status != 200:
        return {}
    try:
        items = data.get("response", {}).get("body", {}).get("items", {}).get("item", []) or []
        if isinstance(items, dict):
            items = [items]
        return items[0] if items else {}
    except Exception:
        return {}

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

def _sample_indices_by_distance(coords: List[List[float]], interval_km: float) -> List[int]:
    """경로 좌표([lon,lat] 리스트)에서 대략 interval_km 간격으로 인덱스 샘플링"""
    if not coords:
        return []
    idxs = [0]
    accum = 0.0
    last_lon, last_lat = coords[0]
    last_pick = 0
    for i in range(1, len(coords)):
        lon, lat = coords[i]
        accum += haversine(last_lat, last_lon, lat, lon)
        last_lon, last_lat = lon, lat
        # 최소 1점 건너띄기 + 거리 기준
        if accum >= interval_km and i - last_pick >= 1:
            idxs.append(i)
            accum = 0.0
            last_pick = i
    if idxs[-1] != len(coords) - 1:
        idxs.append(len(coords) - 1)
    return idxs

def search_tour_items_along_route(
    geojson: Dict[str, Any],
    limit_each: int = 50,
    corridor_km: float = 30.0,
) -> Dict[str, List[Dict[str, Any]]]:
    """경로 라인에서 '주변 corridor_km' 이내 관광지/맛집 수집."""
    try:
        coords = geojson["features"][0]["geometry"]["coordinates"]  # [ [lon,lat], ... ]
    except Exception:
        return {"tour": [], "food": [], "all": []}

    # 반경(m) - 30km 기본. API 최대 반경 제한이 있을 수 있어 함수 내부에서 폴백 처리.
    radius_m = int(corridor_km * 1000)

    # 반경이 큰 만큼 샘플 간격은 15~20km로 설정(과도한 중복 방지)
    interval_km = 15.0 if corridor_km >= 25.0 else max(10.0, corridor_km * 0.6)
    idxs = _sample_indices_by_distance(coords, interval_km)

    seen: set = set()
    tours: List[Dict[str, Any]] = []
    foods: List[Dict[str, Any]] = []

    # 먼저 관광지(12)
    for i in idxs:
        lon, lat = coords[i]
        for it in _tourapi_location_based(lon, lat, content_type_id=12, num_rows=30, radius_m=radius_m):
            cid = str(it.get("contentid") or "")
            if not cid or cid in seen:
                continue
            seen.add(cid)
            intro = _tourapi_detail_intro(cid, 12)
            norm = _normalize_detail(it, intro, "tour")
            if _to_float(norm.get("mapx")) is None or _to_float(norm.get("mapy")) is None:
                continue
            tours.append(norm)
            if len(tours) >= limit_each:
                break
        if len(tours) >= limit_each:
            break

    # 맛집(39)
    for i in idxs:
        lon, lat = coords[i]
        for it in _tourapi_location_based(lon, lat, content_type_id=39, num_rows=30, radius_m=radius_m):
            cid = str(it.get("contentid") or "")
            if not cid or cid in seen:
                continue
            seen.add(cid)
            intro = _tourapi_detail_intro(cid, 39)
            norm = _normalize_detail(it, intro, "food")
            if _to_float(norm.get("mapx")) is None or _to_float(norm.get("mapy")) is None:
                continue
            foods.append(norm)
            if len(foods) >= limit_each:
                break
        if len(foods) >= limit_each:
            break

    all_items = tours + foods
    return {"tour": tours, "food": foods, "all": all_items}

# =========================================================
# 라우팅 핸들러
# =========================================================

def _handle_route():
    data = _coerce_json()

    start_in = data.get("start") or data.get("origin") or data.get("from")
    end_in = data.get("end") or data.get("destination") or data.get("to")
    max_wps = int(data.get("max_waypoints") or 3)
    max_wps = max(0, min(3, max_wps))

    # 경로 주변 탐색 코리도(km), 기본 30
    try:
        corridor_km = float(data.get("corridor_km") or 30.0)
    except Exception:
        corridor_km = 30.0
    corridor_km = max(5.0, min(50.0, corridor_km))  # 5~50km로 가드

    if not start_in or not end_in:
        return jsonify({"error": "start/end 누락"}), 400

    start = geocode_google(start_in) if isinstance(start_in, str) else tuple(start_in) if isinstance(start_in, (list, tuple)) else None
    end = geocode_google(end_in) if isinstance(end_in, str) else tuple(end_in) if isinstance(end_in, (list, tuple)) else None

    if not start or not end:
        return jsonify({"error": "주소 변환 실패"}), 400

    # 경유지 선택
    way_sel = find_waypoints_along_direction(start, end, max_n=max_wps)
    if not way_sel and max_wps >= 1 and beach_coords:
        legacy = find_best_beach_waypoint_legacy(start, end)
        if legacy:
            way_sel = [(legacy[0], legacy[1], legacy[2], 0.5)]

    # 라우팅 (start -> [waypoints] -> end)
    points = [start] + [(lat, lon) for (_, lat, lon, _) in way_sel] + [end]
    route_data, status = get_ors_route_multi(points)
    if status != 200 or "error" in route_data:
        return jsonify({"error": route_data.get("error", f"OpenRouteService 실패({status})")}), status

    # 경로 주변 관광지/맛집 (기본 30km)
    spots = search_tour_items_along_route(route_data, limit_each=50, corridor_km=corridor_km)

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
        "spots_grouped": { "tour": spots["tour"], "food": spots["food"] }
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

@app.route("/api/tourspot", methods=["GET"])
def api_tourspot():
    lon = _to_float(request.args.get("lon"))
    lat = _to_float(request.args.get("lat"))
    radius = int(request.args.get("radius") or 5000)
    if lon is None or lat is None:
        return jsonify({"error": "lon/lat 파라미터 필요"}), 400
    items_t = _tourapi_location_based(lon, lat, content_type_id=12, num_rows=20, radius_m=radius)
    items_f = _tourapi_location_based(lon, lat, content_type_id=39, num_rows=20, radius_m=radius)
    seen = set()
    tours, foods = [], []
    for it in items_t:
        cid = str(it.get("contentid") or "")
        if not cid or cid in seen: continue
        seen.add(cid)
        intro = _tourapi_detail_intro(cid, 12)
        norm = _normalize_detail(it, intro, "tour")
        if _to_float(norm.get("mapx")) is None or _to_float(norm.get("mapy")) is None: continue
        tours.append(norm)
    for it in items_f:
        cid = str(it.get("contentid") or "")
        if not cid or cid in seen: continue
        seen.add(cid)
        intro = _tourapi_detail_intro(cid, 39)
        norm = _normalize_detail(it, intro, "food")
        if _to_float(norm.get("mapx")) is None or _to_float(norm.get("mapy")) is None: continue
        foods.append(norm)
    return jsonify({"tour": tours, "food": foods, "all": tours + foods}), 200

if __name__ == "__main__":
    port_env = os.getenv("PORT")
    if not port_env:
        log.warning("PORT env not set; falling back to 10000 (local dev).")
    port = int(port_env or "10000")
    log.info(f"Starting on 0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port)
