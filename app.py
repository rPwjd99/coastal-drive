# app.py
# 실행 예:
#   로컬:    export PORT=10000 && python app.py
#   Render:  python app.py   (또는 gunicorn -w 1 -k gthread -b 0.0.0.0:$PORT app:app)

import os
import math
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from functools import lru_cache
from urllib.parse import quote

import requests
from flask import Flask, request, jsonify, send_from_directory, redirect, Response

# .env (로컬 개발 시)
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# 해변 좌표 테이블
try:
    from beaches_coordinates import beach_coords  # dict: {"해변명": (lon, lat), ...}
except Exception:
    beach_coords = {}

app = Flask(__name__, static_folder="static")
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("coastal-drive")

APP_DIR = Path(__file__).resolve().parent

# ===== 필수 키/설정 (환경변수) =====
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
ORS_API_KEY    = os.getenv("ORS_API_KEY", "")
TOURAPI_KEY    = os.getenv("TOURAPI_KEY", "")
TOURAPI_BASE   = (os.getenv("TOURAPI_BASE") or "https://apis.data.go.kr/B551011/KorService2").rstrip("/")

# ===== index.html 서빙 (기존 성공 버전 유지) =====
def _find_index_html() -> Optional[Path]:
    for p in [APP_DIR / "index.html", APP_DIR / "templates" / "index.html", APP_DIR / "static" / "index.html"]:
        if p.is_file():
            return p
    return None

def _fallback_index_html() -> str:
    return "<!doctype html><meta charset='utf-8'><title>Coastal Drive</title><h3>index.html이 없습니다.</h3>"

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

# ===== 공통 유틸 =====
def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """두 위경도 간 대원거리(km)."""
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

# ===== 지오코딩 (Google) =====
def geocode_google(address: str) -> Optional[Tuple[float, float]]:
    if not GOOGLE_API_KEY or not address:
        return None
    try:
        r = requests.get(
            "https://maps.googleapis.com/maps/api/geocode/json",
            params={"address": address, "key": GOOGLE_API_KEY},
            timeout=12
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
            timeout=12
        )
        return r.json()["results"][0]["formatted_address"]
    except Exception:
        return ""

# ===== 경유지 선택 (기존 성공 로직 유지) =====
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
    vx, vy = (ex - sx), (ey - sy)   # SE 벡터
    ux, uy = (px - sx), (py - sy)   # SP 벡터
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
    """기존 성공 로직: 직선 경로 주변(corridor) 해변 후보 중 우회비용 제한 내 최대 3개 선택."""
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
    """후방 호환: 후보 없을 때 1개라도 고르는 심플 규칙."""
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

# ===== ORS 라우팅 (기존 성공 로직 유지) =====
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

# ===== TourAPI 호출 헬퍼 (KorService2 우선 + KorService1 폴백) =====
def _enc_key(k: str) -> str:
    if not k:
        return ""
    # 이미 인코딩된 키('%', '+', '=')가 포함된 경우 그대로, 아니면 URL 인코딩
    return k if "%" in k else quote(k, safe="")

def _tour_call(endpoint_name: str, params: Dict[str, Any], timeout: int = 10) -> Dict[str, Any]:
    """KorService2 우선, 실패/에러/XML이면 KorService1로 폴백. 결과/상태/에러 텍스트 포함."""
    base2 = TOURAPI_BASE  # ex) https://apis.data.go.kr/B551011/KorService2
    base1 = "https://apis.data.go.kr/B551011/KorService1"
    svc_key = _enc_key(TOURAPI_KEY)

    for base, variant in ((base2, "KorService2"), (base1, "KorService1")):
        url = f"{base}/{endpoint_name}"
        q = dict(params)
        q["serviceKey"] = svc_key
        try:
            r = requests.get(url, params=q, timeout=timeout)
            ct = (r.headers.get("Content-Type") or "").lower()
            # JSON 응답 우선 처리
            if "json" in ct:
                try:
                    j = r.json()
                    return {"ok": True, "json": j, "status": r.status_code, "variant": variant, "url": r.url}
                except Exception as e:
                    return {"ok": False, "status": r.status_code, "variant": variant, "url": r.url, "error": f"json-parse-failed: {e}"}
            # XML/기타 에러 본문 그대로 노출
            text = r.text
            # TourAPI 게이트웨이 에러는 XML로 옴(<OpenAPI ServiceResponse>...)
            return {"ok": False, "status": r.status_code, "variant": variant, "url": r.url, "text": text}
        except Exception as e:
            # 다음 베이스로 폴백
            last = {"ok": False, "variant": variant, "error": str(e), "url": url}
            continue
    # 둘 다 실패
    return {"ok": False, "error": "both KorService2 and KorService1 failed", "status": 500}

def _extract_items(j: Dict[str, Any]) -> List[Dict[str, Any]]:
    try:
        items = j.get("response", {}).get("body", {}).get("items", {}).get("item", [])
        if isinstance(items, dict):
            return [items]
        return items or []
    except Exception:
        return []

@lru_cache(maxsize=4096)
def _tour_location_based(lon: float, lat: float, content_type_id: int, radius_m: int) -> Dict[str, Any]:
    """locationBasedList1 호출(캐시). 디버그용 원시정보 동봉."""
    params = {
        "mapX": lon,
        "mapY": lat,
        "radius": radius_m,            # 실사용 최대 20000 권장
        "contentTypeId": content_type_id,  # 12=관광지, 39=음식
        "listYN": "Y",
        "arrange": "E",
        "numOfRows": 30,
        "pageNo": 1,
        "MobileOS": "ETC",
        "MobileApp": "CoastalDrive",
        "_type": "json",
    }
    res = _tour_call("locationBasedList1", params, timeout=10)
    return res

@lru_cache(maxsize=4096)
def _tour_detail_intro(content_id: str, content_type_id: int) -> Dict[str, Any]:
    params = {
        "contentId": content_id,
        "contentTypeId": content_type_id,
        "_type": "json",
        "MobileOS": "ETC",
        "MobileApp": "CoastalDrive",
    }
    res = _tour_call("detailIntro1", params, timeout=10)
    if not res.get("ok"):
        return {}
    return (_extract_items(res["json"]) or [{}])[0]

def _norm_number(x: Any) -> Optional[float]:
    try:
        return float(x)
    except Exception:
        return None

def _normalize_item(item: Dict[str, Any], intro: Dict[str, Any], category: str) -> Optional[Dict[str, Any]]:
    lon = _norm_number(item.get("mapx"))
    lat = _norm_number(item.get("mapy"))
    if lon is None or lat is None:
        return None
    res = {
        "contentid": str(item.get("contentid") or ""),
        "title": item.get("title") or "",
        "addr1": item.get("addr1") or "",
        "mapx": lon,
        "mapy": lat,
        "firstimage": item.get("firstimage") or "",
        "homepage": item.get("homepage") or "",
        "tel": item.get("tel") or "",
        "category": category,  # tour/food
        "openhour": "",
        "restday": "",
        "parking_info": "",
    }
    if category == "tour":
        res["openhour"] = intro.get("usetime") or ""
        res["restday"] = intro.get("restdate") or ""
        res["parking_info"] = intro.get("parking") or ""
    else:  # food
        res["openhour"] = intro.get("opentimefood") or ""
        res["restday"] = intro.get("restdatefood") or ""
        res["parking_info"] = intro.get("parkingfood") or ""
    return res

def search_tour_items_along_route(geojson: Dict[str, Any]) -> Dict[str, Any]:
    """경로 따라 촘촘히 샘플링 → 반경 20km로 호출 → 실질 60km 코리도 커버."""
    debug: Dict[str, Any] = {"calls": []}
    try:
        coords = geojson["features"][0]["geometry"]["coordinates"]
    except Exception:
        return {"tour": [], "food": [], "all": [], "debug": {"error": "no coords"}}

    n = len(coords)
    if n == 0:
        return {"tour": [], "food": [], "all": [], "debug": {"error": "empty coords"}}

    # 호출 예산: 너무 잦으면 쿼터 초과 → 길이에 따라 120~200회 내로 제한
    sample_goal = 160 if n > 800 else 120
    step = max(1, n // sample_goal)

    seen: set = set()
    tours: List[Dict[str, Any]] = []
    foods: List[Dict[str, Any]] = []

    # 최대 반경: 20km
    radius_m = 20000

    for idx in range(0, n, step):
        lon, lat = coords[idx]

        # 관광지(12)
        res_t = _tour_location_based(lon, lat, 12, radius_m)
        debug["calls"].append({"idx": idx, "type": "tour", **{k: res_t.get(k) for k in ("ok","status","variant","url") if k in res_t}})
        if res_t.get("ok"):
            for it in _extract_items(res_t["json"]):
                cid = str(it.get("contentid") or "")
                if not cid or cid in seen: 
                    continue
                seen.add(cid)
                intro = _tour_detail_intro(cid, 12)
                norm = _normalize_item(it, intro, "tour")
                if norm:
                    tours.append(norm)

        # 맛집(39)
        res_f = _tour_location_based(lon, lat, 39, radius_m)
        debug["calls"].append({"idx": idx, "type": "food", **{k: res_f.get(k) for k in ("ok","status","variant","url") if k in res_f}})
        if res_f.get("ok"):
            for it in _extract_items(res_f["json"]):
                cid = str(it.get("contentid") or "")
                if not cid or cid in seen:
                    continue
                seen.add(cid)
                intro = _tour_detail_intro(cid, 39)
                norm = _normalize_item(it, intro, "food")
                if norm:
                    foods.append(norm)

    all_items = tours + foods
    return {"tour": tours, "food": foods, "all": all_items, "debug": debug}

# ===== 라우팅 핸들러 (경로 로직 유지, TourAPI만 추가) =====
def _handle_route():
    data = _coerce_json()

    start_in = data.get("start") or data.get("origin") or data.get("from")
    end_in   = data.get("end")   or data.get("destination") or data.get("to")
    max_wps  = int(data.get("max_waypoints") or 3)
    max_wps  = max(0, min(3, max_wps))

    if not start_in or not end_in:
        return jsonify({"error": "start/end 누락"}), 400

    start = geocode_google(start_in) if isinstance(start_in, str) else tuple(start_in) if isinstance(start_in, (list,tuple)) else None
    end   = geocode_google(end_in)   if isinstance(end_in,   str) else tuple(end_in)   if isinstance(end_in,   (list,tuple)) else None

    if not start or not end:
        return jsonify({"error": "주소 변환 실패"}), 400

    # 기존 성공 경유지 로직
    way_sel = find_waypoints_along_direction(start, end, max_n=max_wps)
    if not way_sel and max_wps >= 1 and beach_coords:
        legacy = find_best_beach_waypoint_legacy(start, end)
        if legacy:
            way_sel = [(legacy[0], legacy[1], legacy[2], 0.5)]

    # 라우팅 (start -> [wps] -> end)
    points = [start] + [(lat, lon) for (_, lat, lon, _) in way_sel] + [end]
    route_data, status = get_ors_route_multi(points)
    if status != 200 or "error" in route_data:
        return jsonify({"error": route_data.get("error", f"OpenRouteService 실패({status})")}), status

    # 경로 주변 관광/맛집
    spots = search_tour_items_along_route(route_data)

    # 응답(경유지 정보 포함)
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
        # 프런트 콘솔에서 문제를 바로 볼 수 있게 최소 디버그도 같이 보냄
        "tourapi_debug": spots.get("debug", {}),
        "tourapi_base": TOURAPI_BASE,
        "tourapi_variant": "KorService2->KorService1 fallback enabled"
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

# 테스트: 특정 좌표 주변 호출(브라우저에서 진단용)
@app.route("/api/tourspot")
def api_tourspot():
    try:
        lon = float(request.args.get("lon"))
        lat = float(request.args.get("lat"))
    except Exception:
        return jsonify({"error": "lon/lat 쿼리 필요. 예: /api/tourspot?lon=127.0&lat=37.5"}), 400

    res_t = _tour_location_based(lon, lat, 12, 20000)  # 관광지
    res_f = _tour_location_based(lon, lat, 39, 20000)  # 음식
    out = {
        "tour_ok": res_t.get("ok"), "tour_status": res_t.get("status"), "tour_variant": res_t.get("variant"),
        "food_ok": res_f.get("ok"), "food_status": res_f.get("status"), "food_variant": res_f.get("variant"),
        "urls": {"tour": res_t.get("url"), "food": res_f.get("url")},
        "tour_count": len(_extract_items(res_t["json"])) if res_t.get("ok") else 0,
        "food_count": len(_extract_items(res_f["json"])) if res_f.get("ok") else 0,
    }
    return jsonify(out), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    log.info(f"Starting on 0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port)
