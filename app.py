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
# index.html 서빙 (없으면 임시 페이지 제공 + 팝업 포함)
# =========================================================

def _find_index_html() -> Optional[Path]:
    for p in [APP_DIR / "index.html", APP_DIR / "templates" / "index.html", APP_DIR / "static" / "index.html"]:
        if p.is_file():
            return p
    return None


def _fallback_index_html() -> str:
    # 안전하게 동작하는 최소 UI (경로 + 관광지/맛집 팝업)
    return """
<!DOCTYPE html>
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
    #controls label { display:flex; align-items:center; gap:4px; }
    #map { width:100%; height: calc(100vh - 64px); }
    .ol-popup {
      position: absolute;
      background-color: white;
      box-shadow: 0 1px 4px rgba(0,0,0,0.3);
      padding: 10px 12px;
      border-radius: 8px;
      border: 1px solid #cccccc;
      min-width: 240px;
    }
    .ol-popup:after, .ol-popup:before {
      top: 100%;
      border: solid transparent;
      content: " ";
      height: 0;
      width: 0;
      position: absolute;
      pointer-events: none;
    }
    .ol-popup:after {
      border-top-color: white;
      border-width: 10px;
      left: 48px;
      margin-left: -10px;
    }
    .ol-popup:before {
      border-top-color: #cccccc;
      border-width: 11px;
      left: 48px;
      margin-left: -11px;
    }
    .popup-title { font-weight:600; margin-bottom:6px; }
    .popup-row { font-size: 13px; color:#333; margin:3px 0; }
    .popup-img { width:100%; max-height:160px; object-fit:cover; border-radius:6px; margin:6px 0; display:none; }
    .legend { display:flex; gap:10px; margin-left: auto; margin-right: 10px; font-size:13px; color:#333; }
    .legend i { display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:4px; vertical-align:middle; }
    .legend .tour i { background:#1976d2; }
    .legend .food i { background:#388e3c; }
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
    <label><input type="checkbox" id="showTour" checked> 관광지</label>
    <label><input type="checkbox" id="showFood" checked> 맛집</label>
    <div class="legend">
      <span class="tour"><i></i>관광지</span>
      <span class="food"><i></i>맛집</span>
    </div>
  </div>
  <div id="map"></div>

  <script>
    const map = new ol.Map({
      target: "map",
      layers: [ new ol.layer.Tile({ source: new ol.source.OSM() }) ],
      view: new ol.View({ center: ol.proj.fromLonLat([127.5, 36.5]), zoom: 7 })
    });

    // 경로 레이어
    let routeLayer = null;
    // 스팟 레이어 (관광/맛집 분리)
    const tourLayer = new ol.layer.Vector({ source: new ol.source.Vector() });
    const foodLayer = new ol.layer.Vector({ source: new ol.source.Vector() });
    tourLayer.setZIndex(3);
    foodLayer.setZIndex(4);

    // 스타일
    const routeStyle = new ol.style.Style({
      stroke: new ol.style.Stroke({ color: '#0066ff', width: 4 })
    });
    const tourStyle = new ol.style.Style({
      image: new ol.style.Circle({ radius: 6, fill: new ol.style.Fill({ color: '#1976d2' }), stroke: new ol.style.Stroke({ color: '#ffffff', width: 1 }) })
    });
    const foodStyle = new ol.style.Style({
      image: new ol.style.Circle({ radius: 6, fill: new ol.style.Fill({ color: '#388e3c' }), stroke: new ol.style.Stroke({ color: '#ffffff', width: 1 }) })
    });
    tourLayer.setStyle(tourStyle);
    foodLayer.setStyle(foodStyle);
    map.addLayer(tourLayer);
    map.addLayer(foodLayer);

    function drawRoute(coordsLonLat) {
      const coords3857 = coordsLonLat.map(([lon, lat]) => ol.proj.fromLonLat([lon, lat]));
      if (routeLayer) map.removeLayer(routeLayer);
      routeLayer = new ol.layer.Vector({
        source: new ol.source.Vector({ features: [ new ol.Feature({ geometry: new ol.geom.LineString(coords3857) }) ] }),
        style: routeStyle
      });
      map.addLayer(routeLayer);
      routeLayer.setZIndex(1);
      map.getView().fit(new ol.geom.LineString(coords3857), { padding: [50,50,50,50], maxZoom: 12 });
    }

    function clearSpots() {
      tourLayer.getSource().clear();
      foodLayer.getSource().clear();
    }

    function escapeHtml(text) {
      if (!text) return "";
      return text.replace(/[&<>"']/g, function (m) {
        return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]);
      });
    }

    function addSpots(spots) {
      clearSpots();
      if (!Array.isArray(spots)) return;
      spots.forEach(s => {
        const lon = parseFloat(s.mapx);
        const lat = parseFloat(s.mapy);
        if (!isFinite(lon) || !isFinite(lat)) return;
        const feat = new ol.Feature({
          geometry: new ol.geom.Point(ol.proj.fromLonLat([lon, lat])),
          ...s
        });
        if (s.category === 'food') {
          foodLayer.getSource().addFeature(feat);
        } else {
          tourLayer.getSource().addFeature(feat);
        }
      });
    }

    // 팝업
    const container = document.createElement('div');
    container.className = 'ol-popup';
    const overlay = new ol.Overlay({ element: container, autoPan: { animation: { duration: 250 } } });
    map.addOverlay(overlay);

    function makePopupHTML(props) {
      const title = escapeHtml(props.title || '제목 없음');
      const addr = escapeHtml(props.addr1 || '');
      const tel = escapeHtml(props.tel || '');
      const hours = escapeHtml(props.openhour || '');
      const rest = escapeHtml(props.restday || '');
      const park = escapeHtml(props.parking_info || '');
      const cat = props.category === 'food' ? '맛집' : '관광지';
      const img = (props.firstimage && typeof props.firstimage === 'string') ? props.firstimage : '';

      const lines = [];
      if (addr) lines.push(`<div class="popup-row">${addr}</div>`);
      if (tel) lines.push(`<div class="popup-row">전화: ${tel}</div>`);
      if (hours) lines.push(`<div class="popup-row">운영시간: ${hours}</div>`);
      if (rest) lines.push(`<div class="popup-row">휴무: ${rest}</div>`);
      if (park) lines.push(`<div class="popup-row">주차: ${park}</div>`);
      if (props.homepage) {
        const link = escapeHtml(props.homepage);
        lines.push(`<div class="popup-row"><a href="${link}" target="_blank" rel="noopener">홈페이지</a></div>`);
      }

      return `
        <div class="popup-title">[${cat}] ${title}</div>
        ${img ? `<img class="popup-img" src="${img}" onload="this.style.display='block'" onerror="this.style.display='none'">` : ''}
        ${lines.join('')}
      `;
    }

    map.on('singleclick', function(evt) {
      let feature = null;
      map.forEachFeatureAtPixel(evt.pixel, function(feat) { feature = feat; return true; });
      if (!feature) {
        overlay.setPosition(undefined);
        return;
      }
      const props = feature.getProperties();
      const coord = feature.getGeometry().getCoordinates();
      container.innerHTML = makePopupHTML(props);
      overlay.setPosition(coord);
    });

    // 레이어 표시 토글
    document.getElementById('showTour').addEventListener('change', (e) => {
      tourLayer.setVisible(e.target.checked);
    });
    document.getElementById('showFood').addEventListener('change', (e) => {
      foodLayer.setVisible(e.target.checked);
    });

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
        if (Array.isArray(data.spots)) addSpots(data.spots);
      } catch (err) {
        alert('경로 요청 실패: ' + (err.message || err));
        console.error(err);
      }
    });
  </script>
</body>
</html>
"""


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
    """두 위경도 간 대원거리(km)."""
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
# 경유지 선택 (투영/코리도/우회비용 기반, 최대 3개)
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

    vx, vy = (ex - sx), (ey - sy)     # SE 벡터
    ux, uy = (px - sx), (py - sy)     # SP 벡터

    denom = vx*vx + vy*vy
    if denom <= 0:
        return 0.0, float("inf")
    t = (ux*vx + uy*vy) / denom       # 0~1 사이면 선분 위 사영
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
    """
    start/end 직선 경로 주변(corridor_km)에서 진행방향(t) 순으로 경유 해변 선택.
    우회거리 절대/상대 제한으로 필터링.
    반환: [(name, lat, lon, t), ...]
    """
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
    """과거 버전 호환용 간단 규칙"""
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


# =========================================================
# ORS 라우팅
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
# TourAPI (검색/상세) - 안전 파싱 + 캐시
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
        "numOfRows": 30,   # 넉넉히 받고 아래에서 중복/정제
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


def _tourapi_location_based(lon: float, lat: float, content_type_id: Optional[int] = None, num_rows: int = 10, radius_m: int = 5000) -> List[Dict[str, Any]]:
    if content_type_id is None:
        return []
    data, status = _tourapi_location_based_cached(lon, lat, content_type_id, radius_m)
    if status != 200:
        return []
    try:
        items = data.get("response", {}).get("body", {}).get("items", {}).get("item", []) or []
        # item이 dict 한 개일 수 있어 리스트화
        if isinstance(items, dict):
            items = [items]
        # 상위 num_rows만 사용
        return items[:num_rows]
    except Exception:
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
    # 숫자 변환
    mapx = _to_float(item.get("mapx"))
    mapy = _to_float(item.get("mapy"))
    if mapx is None or mapy is None:
        # 좌표 없으면 스킵하도록 호출부에서 필터
        mapx = mapx if mapx is not None else 0.0
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


def search_tour_items_along_route(geojson: Dict[str, Any], limit_each: int = 25) -> Dict[str, List[Dict[str, Any]]]:
    # 경로 좌표
    try:
        coords = geojson["features"][0]["geometry"]["coordinates"]
    except Exception:
        return {"tour": [], "food": [], "all": []}

    def collect(radius_m: int, sample_goal: int):
        seen = set()
        tours, foods = [], []
        step = max(1, len(coords) // max(1, sample_goal))
        for idx in range(0, len(coords), step):
            lon, lat = coords[idx]
            # 관광지(12)
            for it in _tourapi_location_based(lon, lat, content_type_id=12, num_rows=10, radius_m=radius_m):
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
        for idx in range(0, len(coords), step):
            lon, lat = coords[idx]
            for it in _tourapi_location_based(lon, lat, content_type_id=39, num_rows=10, radius_m=radius_m):
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
        return tours, foods

    # 1차 5km, 부족하면 8km로 재시도
    tour_items, food_items = collect(radius_m=5000, sample_goal=200)
    if len(tour_items) + len(food_items) < 10:
        t2, f2 = collect(radius_m=8000, sample_goal=300)
        if len(tour_items) < len(t2):
            tour_items = t2
        if len(food_items) < len(f2):
            food_items = f2

    all_items = tour_items + food_items
    return {"tour": tour_items, "food": food_items, "all": all_items}


# =========================================================
# 라우팅 핸들러
# =========================================================

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

    # 경로 주변 관광지/맛집
    spots = search_tour_items_along_route(route_data)

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
    # 임의 좌표 주변 검색 (프런트 테스트용)
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
        # 로컬 개발 시만 10000 사용. Render에서는 반드시 $PORT가 설정되어야 함.
        log.warning("PORT env not set; falling back to 10000 (local dev).")
    port = int(port_env or "10000")
    log.info(f"Starting on 0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port)
