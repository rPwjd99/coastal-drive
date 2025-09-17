# app.py
# 실행 예:
#   Windows: set PORT=10000 && python app.py
#   macOS/Linux: export PORT=10000 && python app.py
# Render(권장): gunicorn -w 1 -k gthread --threads 8 --timeout 120 --keep-alive 30 -b 0.0.0.0:$PORT app:app

import os
import math
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from functools import lru_cache
from urllib.parse import unquote

import requests
from flask import Flask, request, jsonify, send_from_directory, redirect, Response

# .env 사용 (선택)
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# 해변 좌표 (기존 파일 그대로 사용)
try:
    from beaches_coordinates import beach_coords  # type: ignore
except Exception:
    beach_coords = {}

app = Flask(__name__, static_folder="static")
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("coastal-drive")

APP_DIR = Path(__file__).resolve().parent

# === Environment ===
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
ORS_API_KEY = os.getenv("ORS_API_KEY")

# TourAPI 키(인코딩/디코딩 무관 입력 지원) + 엔드포인트
_RAW_TOUR_KEY = os.getenv("TOURAPI_KEY", "").strip()
# 인코딩된 값이면(unquote 시 변화가 있으면) 디코딩하여 사용
TOURAPI_KEY = unquote(_RAW_TOUR_KEY) if "%2" in _RAW_TOUR_KEY or "%3" in _RAW_TOUR_KEY else _RAW_TOUR_KEY
# 1/2중 뭘 쓰든 상관없게 베이스를 환경변수로 열어둠 (디폴트: KorService1)
TOURAPI_BASE = os.getenv("TOURAPI_BASE", "https://apis.data.go.kr/B551011/KorService1").rstrip("/")

# =========================================================
# index.html 서빙 (파일 없으면 임시 페이지)
# =========================================================

def _find_index_html() -> Optional[Path]:
    for p in [APP_DIR / "index.html", APP_DIR / "templates" / "index.html", APP_DIR / "static" / "index.html"]:
        if p.is_file():
            return p
    return None

def _fallback_index_html() -> str:
    return """<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8"><title>Coastal Drive</title></head>
<body style="font-family:sans-serif;padding:24px">
  <h2>Coastal Drive</h2>
  <p>index.html 파일이 없어서 임시 페이지를 보여드립니다.</p>
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
# 기존 경유지/경로 로직 (그대로 유지)
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
    """기존 방식 유지: 진행방향(t), 코리도/우회 제한, 최대 3개."""
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
# ORS 라우팅 (변경 없음)
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
# TourAPI (핵심: https, 디코딩키, 반경 폴백, 샘플링 강화, 상세정보 보강, 디버그)
# =========================================================

def _tourapi_get(path: str, params: Dict[str, Any], timeout: int = 10) -> Tuple[Dict[str, Any], int]:
    """HTTPS 고정 + 안전 호출. 인코딩 키는 requests가 알아서 인코딩."""
    if not TOURAPI_KEY:
        return {"error": "TOURAPI_KEY missing"}, 500
    p = dict(params)
    p["serviceKey"] = TOURAPI_KEY
    url = f"{TOURAPI_BASE}/{path.lstrip('/')}"
    try:
        r = requests.get(url, params=p, timeout=timeout)
        j = {}
        try:
            j = r.json()
        except Exception:
            pass
        return j, r.status_code
    except Exception as e:
        return {"error": str(e)}, 500

@lru_cache(maxsize=4096)
def _location_based(lon: float, lat: float, content_type_id: int, radius_m: int, num_rows: int) -> Tuple[Dict[str, Any], int]:
    params = {
        "mapX": lon,
        "mapY": lat,
        "radius": radius_m,        # 공식 상한 20000(20km)
        "listYN": "Y",
        "arrange": "E",            # 조회수 내림차
        "numOfRows": num_rows,
        "pageNo": 1,
        "MobileOS": "ETC",
        "MobileApp": "CoastalDrive",
        "_type": "json",
        "contentTypeId": content_type_id,
    }
    return _tourapi_get("locationBasedList1", params)

@lru_cache(maxsize=4096)
def _detail_intro(content_id: str, content_type_id: int) -> Tuple[Dict[str, Any], int]:
    params = {
        "contentId": content_id,
        "contentTypeId": content_type_id,
        "_type": "json",
        "MobileOS": "ETC",
        "MobileApp": "CoastalDrive",
    }
    return _tourapi_get("detailIntro1", params)

def _normalize_item(item: Dict[str, Any], intro: Dict[str, Any], category: str) -> Optional[Dict[str, Any]]:
    try:
        mapx = float(item.get("mapx"))
        mapy = float(item.get("mapy"))
    except Exception:
        return None
    res = {
        "contentid": str(item.get("contentid") or ""),
        "title": item.get("title") or "",
        "addr1": item.get("addr1") or "",
        "mapx": mapx,
        "mapy": mapy,
        "firstimage": item.get("firstimage") or "",
        "tel": item.get("tel") or "",
        "homepage": item.get("homepage") or "",
        "category": category,  # 'tour' | 'food'
        # 상세 보강
        "openhour": "",
        "restday": "",
        "parking_info": "",
    }
    if category == "tour":
        res["openhour"] = (intro.get("usetime") or "").strip()
        res["restday"] = (intro.get("restdate") or "").strip()
        res["parking_info"] = (intro.get("parking") or "").strip()
    else:  # food(39)
        res["openhour"] = (intro.get("opentimefood") or "").strip()
        res["restday"] = (intro.get("restdatefood") or "").strip()
        res["parking_info"] = (intro.get("parkingfood") or "").strip()
    return res

def search_tour_food_along_route(
    geojson: Dict[str, Any],
    corridor_km: float = 30.0,
    want_each: int = 40,           # 관광/맛집 각각 목표치
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """경로를 따라 조밀 샘플링 + 반경 폴백(20km→15→10→8→5)으로 수집."""
    debug = {"calls": []}
    try:
        coords = geojson["features"][0]["geometry"]["coordinates"]
    except Exception:
        return [], [], {"error": "invalid geojson"}

    # 경로 길이에 따라 샘플 개수 조절 (조밀 샘플링)
    N = len(coords)
    target_samples = min(320, max(120, N // 2))  # 너무 과도하지 않게 제한
    step = max(1, N // target_samples)

    # 반경 폴백 (공식 상한 20000)
    radii = [20000, 15000, 10000, 8000, 5000]
    seen = set()
    tours: List[Dict[str, Any]] = []
    foods: List[Dict[str, Any]] = []

    # 수집 루프
    for radius in radii:
        if len(tours) >= want_each and len(foods) >= want_each:
            break
        for idx in range(0, N, step):
            lon, lat = coords[idx]

            # 관광(12)
            if len(tours) < want_each:
                j, st = _location_based(lon, lat, 12, radius, 30)
                debug["calls"].append({"type": "tour", "status": st, "radius": radius, "idx": idx,
                                       "resultCode": (j.get("response", {}).get("header", {}).get("resultCode") if isinstance(j, dict) else None)})
                items = (j.get("response", {}).get("body", {}).get("items", {}).get("item", []) if isinstance(j, dict) else []) or []
                if isinstance(items, dict):
                    items = [items]
                for it in items:
                    cid = str(it.get("contentid") or "")
                    if not cid or cid in seen:
                        continue
                    # 상세(소개)로 보강
                    intro_json, _ = _detail_intro(cid, 12)
                    intro_item = (intro_json.get("response", {}).get("body", {}).get("items", {}).get("item", []) if isinstance(intro_json, dict) else [])
                    if isinstance(intro_item, dict):
                        intro_item = [intro_item]
                    intro = intro_item[0] if intro_item else {}
                    norm = _normalize_item(it, intro, "tour")
                    if norm:
                        seen.add(cid)
                        tours.append(norm)
                        if len(tours) >= want_each:
                            break

            # 맛집(39)
            if len(foods) < want_each:
                j, st = _location_based(lon, lat, 39, radius, 30)
                debug["calls"].append({"type": "food", "status": st, "radius": radius, "idx": idx,
                                       "resultCode": (j.get("response", {}).get("header", {}).get("resultCode") if isinstance(j, dict) else None)})
                items = (j.get("response", {}).get("body", {}).get("items", {}).get("item", []) if isinstance(j, dict) else []) or []
                if isinstance(items, dict):
                    items = [items]
                for it in items:
                    cid = str(it.get("contentid") or "")
                    if not cid or cid in seen:
                        continue
                    intro_json, _ = _detail_intro(cid, 39)
                    intro_item = (intro_json.get("response", {}).get("body", {}).get("items", {}).get("item", []) if isinstance(intro_json, dict) else [])
                    if isinstance(intro_item, dict):
                        intro_item = [intro_item]
                    intro = intro_item[0] if intro_item else {}
                    norm = _normalize_item(it, intro, "food")
                    if norm:
                        seen.add(cid)
                        foods.append(norm)
                        if len(foods) >= want_each:
                            break

        # radius 당 한 번 훑고, 부족하면 더 작은 반경으로 한 번 더 순회

    return tours, foods, debug

# =========================================================
# 라우팅 핸들러 (경로/경유지는 그대로, TourAPI만 강화)
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

    # === 경유지 (기존 방식 유지) ===
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

    # === TourAPI 수집 (강화) ===
    tours, foods, d = search_tour_food_along_route(route_data)
    all_spots = tours + foods

    # 응답 – 기존 필드 유지 + 디버그 보조
    wp_objs = []
    for i, (name, lat, lon, t) in enumerate(way_sel, start=1):
        wp_objs.append({
            "order": i, "name": name, "lat": lat, "lon": lon, "t": t,
            "address": reverse_geocode_google(lat, lon) or ""
        })

    resp: Dict[str, Any] = {
        "route": route_data,
        "waypoints_used": wp_objs,
        "spots": all_spots,
        "spots_grouped": {"tour": tours, "food": foods},
    }
    if len(wp_objs) >= 1: resp["waypoint"] = wp_objs[0]
    if len(wp_objs) >= 2: resp["waypoint2"] = wp_objs[1]
    if len(wp_objs) >= 3: resp["waypoint3"] = wp_objs[2]

    # 0건일 때 왜 그런지 바로 보이도록
    if len(all_spots) == 0:
        resp["tourapi_debug"] = d

    return jsonify(resp), 200

@app.route("/route", methods=["POST", "GET"])
def route():
    if request.method == "GET":
        return redirect("/")
    return _handle_route()

# ===== 프런트에서 바로 TourAPI 상태 확인 가능한 핑 엔드포인트 =====
@app.route("/api/tourspot", methods=["GET"])
def api_tourspot():
    try:
        lon = float(request.args.get("lon"))
        lat = float(request.args.get("lat"))
    except Exception:
        return jsonify({"error": "lon, lat 쿼리 필요. 예: /api/tourspot?lon=127.5&lat=36.5"}), 400

    tours_json, st1 = _location_based(lon, lat, 12, 20000, 30)
    foods_json, st2 = _location_based(lon, lat, 39, 20000, 30)
    return jsonify({
        "tour_status": st1,
        "food_status": st2,
        "tour_resultCode": tours_json.get("response", {}).get("header", {}).get("resultCode"),
        "food_resultCode": foods_json.get("response", {}).get("header", {}).get("resultCode"),
        "tour_count": len((tours_json.get("response", {}).get("body", {}).get("items", {}).get("item", []) or [])),
        "food_count": len((foods_json.get("response", {}).get("body", {}).get("items", {}).get("item", []) or [])),
    }), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    log.info(f"Starting on 0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port)
