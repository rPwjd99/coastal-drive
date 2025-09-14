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
from html import escape

import requests
from flask import Flask, request, jsonify, send_from_directory, redirect, Response
from flask_cors import CORS

# .env 사용(선택)
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
# 사용자가 제공한 TourAPI 키가 .env에 없더라도 동작하도록 안전 기본값 유지
TOURAPI_KEY = os.getenv("TOURAPI_KEY") or "e1tU33wjMx2nynKjH8yDBm/S4YNne6B8mpCOWtzMH9TSONF71XG/xAwPqyv1fANpgeOvbPY+Le+gM6cYCnWV8w=="

# ------------------------ index.html 서빙 ------------------------

def _find_index_html() -> Optional[Path]:
    for p in [APP_DIR / "index.html", APP_DIR / "templates" / "index.html", APP_DIR / "static" / "index.html"]:
        if p.is_file():
            return p
    return None

@app.route("/", methods=["GET", "HEAD"])
def index():
    p = _find_index_html()
    if p:
        return send_from_directory(p.parent.as_posix(), p.name)
    return Response("<!doctype html><meta charset='utf-8'><p>index.html이 없습니다. 같은 폴더에 배치하세요.</p>", mimetype="text/html")

@app.route("/favicon.ico")
def favicon():
    return "", 204

@app.route("/healthz")
def healthz():
    return jsonify({"ok": True})

# ------------------------ 유틸 ------------------------

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

def _to_float(x: Any) -> Optional[float]:
    try:
        return float(x)
    except Exception:
        return None

# ------------------------ 지오코딩 ------------------------

def geocode_google(address: str) -> Optional[Tuple[float, float]]:
    if not GOOGLE_API_KEY or not address:
        return None
    try:
        r = requests.get(
            "https://maps.googleapis.com/maps/api/geocode/json",
            params={"address": address, "key": GOOGLE_API_KEY},
            timeout=8,
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
            timeout=8,
        )
        return r.json()["results"][0]["formatted_address"]
    except Exception:
        return ""

# ------------------------ 경유지(해변) 선택 ------------------------

def _ll_to_xy_km(lat: float, lon: float, lat0: float, lon0: float) -> Tuple[float, float]:
    x = (lon - lon0) * math.cos(math.radians(lat0)) * 111.32
    y = (lat - lat0) * 110.57
    return x, y

def _projection_metrics(start: Tuple[float, float], end: Tuple[float, float], p: Tuple[float, float]) -> Tuple[float, float]:
    (slat, slon), (elat, elon), (plat, plon) = start, end, p
    lat0 = (slat + elat) / 2.0; lon0 = (slon + elon) / 2.0
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
    start_lat, start_lon = start; end_lat, end_lon = end
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

# ------------------------ ORS 라우팅 ------------------------

def get_ors_route_multi(points: List[Tuple[float, float]]) -> Tuple[Dict[str, Any], int]:
    if not ORS_API_KEY:
        return {"error": "ORS_API_KEY is missing"}, 500
    coords = [[lon, lat] for (lat, lon) in points]  # ORS는 [lon,lat]
    try:
        r = requests.post(
            "https://api.openrouteservice.org/v2/directions/driving-car/geojson",
            headers={"Authorization": ORS_API_KEY, "Content-Type": "application/json"},
            json={"coordinates": coords},
            timeout=15,
        )
        return r.json(), r.status_code
    except Exception as e:
        return {"error": str(e)}, 500

# ------------------------ TourAPI (경로 주변 30km) ------------------------

BASE_TOUR = "http://apis.data.go.kr/B551011/KorService1"

@lru_cache(maxsize=4096)
def _tourapi_location_based_cached(lon: float, lat: float, content_type_id: int, radius_m: int, rows: int) -> Tuple[Dict[str, Any], int]:
    params = {
        "serviceKey": TOURAPI_KEY,
        "mapX": lon, "mapY": lat,
        "radius": radius_m,   # TourAPI 최대 20km
        "listYN": "Y",
        "arrange": "E",
        "numOfRows": rows,
        "pageNo": 1,
        "MobileOS": "ETC",
        "MobileApp": "CoastalDrive",
        "_type": "json",
        "contentTypeId": content_type_id,
    }
    try:
        r = requests.get(f"{BASE_TOUR}/locationBasedList1", params=params, timeout=8)
        return r.json(), r.status_code
    except Exception as e:
        return {"error": str(e)}, 500

def _tourapi_location_based(lon: float, lat: float, content_type_id: int, radius_m: int = 20000, rows: int = 30) -> List[Dict[str, Any]]:
    radius_m = min(int(radius_m), 20000)  # API 한도
    data, status = _tourapi_location_based_cached(lon, lat, int(content_type_id), radius_m, rows)
    if status != 200:
        return []
    try:
        items = data.get("response", {}).get("body", {}).get("items", {}).get("item", []) or []
        if isinstance(items, dict):
            items = [items]
        return items
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
        "MobileApp": "CoastalDrive",
    }
    try:
        r = requests.get(f"{BASE_TOUR}/detailIntro1", params=params, timeout=8)
        return r.json(), r.status_code
    except Exception as e:
        return {"error": str(e)}, 500

def _tourapi_detail_intro(content_id: str, content_type_id: int) -> Dict[str, Any]:
    data, status = _tourapi_detail_intro_cached(content_id, int(content_type_id))
    if status != 200:
        return {}
    try:
        items = data.get("response", {}).get("body", {}).get("items", {}).get("item", []) or []
        if isinstance(items, dict): items = [items]
        return items[0] if items else {}
    except Exception:
        return {}

@lru_cache(maxsize=4096)
def _tourapi_detail_common_cached(content_id: str) -> Tuple[Dict[str, Any], int]:
    params = {
        "serviceKey": TOURAPI_KEY,
        "contentId": content_id,
        "defaultYN": "Y",
        "overviewYN": "Y",
        "addrinfoYN": "Y",
        "mapinfoYN": "Y",
        "firstImageYN": "Y",
        "_type": "json",
        "MobileOS": "ETC",
        "MobileApp": "CoastalDrive",
    }
    try:
        r = requests.get(f"{BASE_TOUR}/detailCommon1", params=params, timeout=8)
        return r.json(), r.status_code
    except Exception as e:
        return {"error": str(e)}, 500

def _tourapi_detail_common(content_id: str) -> Dict[str, Any]:
    data, status = _tourapi_detail_common_cached(content_id)
    if status != 200:
        return {}
    try:
        items = data.get("response", {}).get("body", {}).get("items", {}).get("item", []) or []
        if isinstance(items, dict): items = [items]
        return items[0] if items else {}
    except Exception:
        return {}

def _normalize_detail(item: Dict[str, Any], intro: Dict[str, Any], common: Dict[str, Any], category: str, src_lat: float, src_lon: float) -> Dict[str, Any]:
    mapx = _to_float(item.get("mapx"))
    mapy = _to_float(item.get("mapy"))
    dist_km = None
    if mapx is not None and mapy is not None:
        dist_km = round(haversine(src_lat, src_lon, mapy, mapx), 2)

    # 공통 필드 보강
    tel = item.get("tel") or common.get("tel") or ""
    homepage = item.get("homepage") or common.get("homepage") or ""

    res = {
        "contentid": str(item.get("contentid") or ""),
        "title": item.get("title") or "",
        "addr1": item.get("addr1") or "",
        "mapx": mapx if mapx is not None else 0.0,
        "mapy": mapy if mapy is not None else 0.0,
        "firstimage": item.get("firstimage") or common.get("firstimage") or "",
        "tel": tel,
        "homepage": homepage,
        "category": category,  # 'tour' or 'food'
        "distance_km": dist_km if dist_km is not None else "",
        "openhour": "",
        "restday": "",
        "parking_info": "",
    }

    if category == "tour":
        res["openhour"] = intro.get("usetime") or ""
        res["restday"]  = intro.get("restdate") or ""
        res["parking_info"] = intro.get("parking") or ""
    else:  # food
        res["openhour"] = intro.get("opentimefood") or ""
        res["restday"]  = intro.get("restdatefood") or ""
        res["parking_info"] = intro.get("parkingfood") or ""

    p = (res["parking_info"] or "").strip()
    res["has_parking"] = bool(p) and ("불가" not in p and "없" not in p)

    return res

def _sample_indices_by_distance(coords: List[List[float]], interval_km: float, max_samples: int = 120) -> List[int]:
    """경로 좌표([lon,lat])에서 대략 interval_km 간격으로 샘플 인덱스 추출"""
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
        if (accum >= interval_km and i - last_pick >= 1) or (len(idxs) < 4 and i % max(1, len(coords)//4) == 0):
            idxs.append(i)
            accum = 0.0
            last_pick = i
        if len(idxs) >= max_samples:
            break
    if idxs[-1] != len(coords) - 1:
        idxs.append(len(coords) - 1)
    return sorted(set(idxs))

def search_tour_items_along_route(geojson: Dict[str, Any], corridor_km: float = 30.0, limit_each: int = 80) -> Dict[str, List[Dict[str, Any]]]:
    try:
        coords = geojson["features"][0]["geometry"]["coordinates"]  # [ [lon,lat], ... ]
    except Exception:
        return {"tour": [], "food": [], "all": []}

    # TourAPI는 반경 최대 20km → 샘플 간격을 12km 정도로 좁혀서 30km 코리도를 커버
    radius_m = 20000
    interval_km = 12.0 if corridor_km >= 25.0 else max(8.0, corridor_km * 0.4)
    idxs = _sample_indices_by_distance(coords, interval_km, max_samples=100)

    seen: set = set()
    tours: List[Dict[str, Any]] = []
    foods: List[Dict[str, Any]] = []

    # 관광지(12)
    for i in idxs:
        lon, lat = coords[i]
        for it in _tourapi_location_based(lon, lat, content_type_id=12, radius_m=radius_m, rows=30):
            cid = str(it.get("contentid") or "")
            if not cid or cid in seen:
                continue
            seen.add(cid)
            intro  = _tourapi_detail_intro(cid, 12)
            common = _tourapi_detail_common(cid)
            norm = _normalize_detail(it, intro, common, "tour", lat, lon)
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
        for it in _tourapi_location_based(lon, lat, content_type_id=39, radius_m=radius_m, rows=30):
            cid = str(it.get("contentid") or "")
            if not cid or cid in seen:
                continue
            seen.add(cid)
            intro  = _tourapi_detail_intro(cid, 39)
            common = _tourapi_detail_common(cid)
            norm = _normalize_detail(it, intro, common, "food", lat, lon)
            if _to_float(norm.get("mapx")) is None or _to_float(norm.get("mapy")) is None:
                continue
            foods.append(norm)
            if len(foods) >= limit_each:
                break
        if len(foods) >= limit_each:
            break

    return {"tour": tours, "food": foods, "all": tours + foods}

# ------------------------ 라우팅 핸들러 ------------------------

def _handle_route():
    data = _coerce_json()
    start_in = data.get("start") or data.get("origin") or data.get("from")
    end_in   = data.get("end") or data.get("destination") or data.get("to")
    max_wps  = int(data.get("max_waypoints") or 3)
    max_wps  = max(0, min(3, max_wps))

    try:
        corridor_km = float(data.get("corridor_km") or 30.0)
    except Exception:
        corridor_km = 30.0
    corridor_km = max(5.0, min(50.0, corridor_km))

    if not start_in or not end_in:
        return jsonify({"error": "start/end 누락"}), 400

    start = geocode_google(start_in) if isinstance(start_in, str) else tuple(start_in) if isinstance(start_in, (list, tuple)) else None
    end   = geocode_google(end_in)   if isinstance(end_in,   str) else tuple(end_in)   if isinstance(end_in,   (list, tuple)) else None
    if not start or not end:
        return jsonify({"error": "주소 변환 실패"}), 400

    # 경유지 선택
    way_sel = find_waypoints_along_direction(start, end, max_n=max_wps)
    if not way_sel and max_wps >= 1 and beach_coords:
        legacy = find_best_beach_waypoint_legacy(start, end)
        if legacy:
            way_sel = [(legacy[0], legacy[1], legacy[2], 0.5)]

    # ORS 라우팅 (start -> [wps] -> end)
    points = [start] + [(lat, lon) for (_, lat, lon, _) in way_sel] + [end]
    route_data, status = get_ors_route_multi(points)
    if status != 200 or "error" in route_data:
        return jsonify({"error": route_data.get("error", f"OpenRouteService 실패({status})")}), status

    # 경로 주변 TourAPI 수집
    spots = search_tour_items_along_route(route_data, corridor_km=corridor_km, limit_each=80)

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

# 상세 페이지(템플릿 없이 즉시 렌더)
@app.route("/tour_detail/<contentid>")
def tour_detail(contentid: str):
    common = _tourapi_detail_common(contentid) or {}
    # contenttypeid가 있으면 intro도 시도
    ctype = 12
    try:
        ctype = int(common.get("contenttypeid") or 12)
    except Exception:
        ctype = 12
    intro = _tourapi_detail_intro(contentid, ctype) or {}

    title = escape(common.get("title") or "상세정보")
    addr1 = escape(common.get("addr1") or "")
    tel   = escape(common.get("tel") or "")
    hp    = escape(common.get("homepage") or "")
    img   = escape(common.get("firstimage") or "")
    ovw   = common.get("overview") or ""
    # overview는 HTML일 수 있어 최소 변환
    ovw_safe = escape(ovw).replace("\n", "<br>")

    if ctype == 39:
        openhour = intro.get("opentimefood") or ""
        restday  = intro.get("restdatefood") or ""
        park     = intro.get("parkingfood") or ""
    else:
        openhour = intro.get("usetime") or ""
        restday  = intro.get("restdate") or ""
        park     = intro.get("parking") or ""

    html = f"""
<!doctype html><html lang="ko"><meta charset="utf-8"><title>{title}</title>
<style>body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,'Noto Sans KR',sans-serif;margin:24px;line-height:1.5}}
h1{{margin:0 0 8px}} .row{{margin:4px 0}} img{{max-width:640px;height:auto;border-radius:8px}}
a{{color:#1565c0}}</style>
<h1>{title}</h1>
<div class="row">{addr1}</div>
<div class="row">{("☎ "+tel) if tel else ""}</div>
<div class="row">{('<a href="'+hp+'" target="_blank" rel="noopener">홈페이지</a>') if hp else ""}</div>
<div class="row">{("운영시간: "+escape(openhour)) if openhour else ""}</div>
<div class="row">{("휴무: "+escape(restday)) if restday else ""}</div>
<div class="row">{("주차: "+escape(park)) if park else ""}</div>
{"<p><img src='"+img+"'></p>" if img else ""}
<hr>
<div>{ovw_safe}</div>
</html>
"""
    return Response(html, mimetype="text/html")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    log.info(f"Starting on 0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port)
