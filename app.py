# app.py
import os
import math
import logging
from typing import Any, Dict, List, Optional, Tuple

import requests
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__, static_folder="static", template_folder="templates")
CORS(app)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("coastal-drive")

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
ORS_API_KEY = os.getenv("ORS_API_KEY")
# TourAPI는 'Decoding' 키(슬래시/플러스 포함 원문)를 ENV에 저장해 두세요.
TOURAPI_KEY = os.getenv("TOURAPI_KEY")
# 기본은 KorService2, 실패 시 자동으로 KorService1로 폴백
TOURAPI_BASE = (os.getenv("TOURAPI_BASE") or "https://apis.data.go.kr/B551011/KorService2").rstrip("/")

# 해변 후보(기존 파일)
try:
    from beaches_coordinates import beach_coords  # {"해변명": (lon, lat)}
except Exception:
    beach_coords = {}

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
# Google 지오코딩
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
def is_in_coastal_bounds(lat, lon):
    return (
        (35 <= lat <= 38 and 128 <= lon <= 131) or
        (33 <= lat <= 35 and 126 <= lon <= 129) or
        (34 <= lat <= 38 and 124 <= lon <= 126)
    )

def find_best_beach_waypoint(start: Tuple[float, float], end: Tuple[float, float]) -> Optional[Tuple[str, float, float]]:
    start_lat, start_lon = start
    end_lat, end_lon = end
    lat_candidates, lon_candidates = [], []
    for name, (lon, lat) in beach_coords.items():
        if not is_in_coastal_bounds(lat, lon):
            continue
        if abs(lat - start_lat) < 0.2 and (end_lon - start_lon) * (lon - start_lon) > 0:
            lat_candidates.append((name, lat, lon, haversine(end_lat, end_lon, lat, lon)))
        if abs(lon - start_lon) < 0.2 and (end_lat - start_lat) * (lat - start_lat) > 0:
            lon_candidates.append((name, lat, lon, haversine(end_lat, end_lon, lat, lon)))
    best_lat = min(lat_candidates, key=lambda x: x[3]) if lat_candidates else None
    best_lon = min(lon_candidates, key=lambda x: x[3]) if lon_candidates else None
    if best_lat and best_lon:
        return (best_lat if best_lat[3] <= best_lon[3] else best_lon)[:3]
    return (best_lat or best_lon)[:3] if (best_lat or best_lon) else None

# -------------------------
# ORS 라우팅 (기존 유지: start → waypoint → end)
# -------------------------
def get_ors_route(start: Tuple[float,float], waypoint: Tuple[str,float,float], end: Tuple[float,float]):
    if not ORS_API_KEY:
        return {"error": "ORS_API_KEY is missing"}, 500
    url = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
    headers = {"Authorization": ORS_API_KEY, "Content-Type": "application/json"}
    body = {
        "coordinates": [
            [start[1], start[0]],
            [waypoint[2], waypoint[1]],
            [end[1], end[0]],
        ]
    }
    try:
        res = requests.post(url, headers=headers, json=body, timeout=30)
        return res.json(), res.status_code
    except Exception as e:
        return {"error": str(e)}, 500

# -------------------------
# TourAPI 호출 보강
# -------------------------
def _clean_base(b: str) -> str:
    return (b or "").strip().rstrip("/")

def _try_loc_once(base_url: str, lon: float, lat: float, content_type_id: Optional[int], radius_m: int, num_rows: int):
    if not TOURAPI_KEY:
        return []
    url = f"{_clean_base(base_url)}/locationBasedList1"
    params = {
        "serviceKey": TOURAPI_KEY,
        "mapX": lon,
        "mapY": lat,
        "radius": max(1000, min(20000, int(radius_m))),  # TourAPI 제한
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
        try:
            j = r.json()
        except Exception:
            txt = (r.text or "")[:140].replace("\n", " ")
            log.warning("[TourAPI] non-JSON(%s): %s ...", r.status_code, txt)
            return []
        items = j.get("response", {}).get("body", {}).get("items", {}).get("item", []) or []
        if isinstance(items, dict):
            items = [items]
        return items
    except Exception as e:
        log.warning("[TourAPI] request failed: %s", e)
        return []

def _loc_with_fallback(lon: float, lat: float, content_type_id: Optional[int], radius_m: int, num_rows: int):
    # 1) KorService2
    items = _try_loc_once(TOURAPI_BASE or "https://apis.data.go.kr/B551011/KorService2", lon, lat, content_type_id, radius_m, num_rows)
    if items:
        return items
    # 2) KorService1
    items = _try_loc_once("https://apis.data.go.kr/B551011/KorService1", lon, lat, content_type_id, radius_m, num_rows)
    if items:
        return items
    # 3) 느슨 재조회(contentType 미지정)
    items = _try_loc_once("https://apis.data.go.kr/B551011/KorService1", lon, lat, None, radius_m, num_rows)
    return items

def _normalize(raw: Dict[str,Any], category: str, anchor_lat: float, anchor_lon: float):
    lon = _to_float(raw.get("mapx"))
    lat = _to_float(raw.get("mapy"))
    if lon is None or lat is None:
        return None
    dist_km = haversine(anchor_lat, anchor_lon, lat, lon)
    return {
        "contentid": str(raw.get("contentid") or ""),
        "title": raw.get("title") or "",
        "addr1": raw.get("addr1") or "",
        "mapx": lon,
        "mapy": lat,
        "firstimage": raw.get("firstimage") or "",
        "tel": raw.get("tel") or "",
        "homepage": raw.get("homepage") or "",
        "readcount": raw.get("readcount") or "",
        "category": category,
        "distance": round(dist_km, 2),
    }

def _polyline_samples(coords: List[List[float]], interval_km: float = 9.0, max_samples: int = 50) -> List[Tuple[float,float]]:
    if not coords:
        return []
    samples = []
    last_lon, last_lat = coords[0]
    samples.append((last_lon, last_lat))
    acc = 0.0
    for i in range(1, len(coords)):
        lon, lat = coords[i]
        acc += haversine(last_lat, last_lon, lat, lon)
        if acc >= interval_km:
            samples.append((lon, lat))
            acc = 0.0
            if len(samples) >= max_samples:
                break
        last_lon, last_lat = lon, lat
    if samples[-1] != (coords[-1][0], coords[-1][1]):
        samples.append((coords[-1][0], coords[-1][1]))
    return samples

def search_along_route(geojson: Dict[str,Any]) -> Dict[str, List[Dict[str,Any]]]:
    try:
        coords = geojson["features"][0]["geometry"]["coordinates"]
    except Exception:
        return {"tour": [], "food": [], "all": []}

    # 반경 20km(최대치) × 조밀 샘플링
    radius_m = 20000
    samples = _polyline_samples(coords, interval_km=9.0, max_samples=50)
    log.info("[TourAPI] samples=%d radius=%dm", len(samples), radius_m)

    seen = set()
    tours, foods = [], []

    # 관광(12)
    for lon, lat in samples:
        items = _loc_with_fallback(lon, lat, content_type_id=12, radius_m=radius_m, num_rows=20)
        for raw in items:
            cid = str(raw.get("contentid") or "")
            if not cid or cid in seen: 
                continue
            seen.add(cid)
            norm = _normalize(raw, "tour", lat, lon)
            if norm: tours.append(norm)
        if len(tours) >= 60: break  # 과도 수집 방지

    # 맛집(39)
    for lon, lat in samples:
        items = _loc_with_fallback(lon, lat, content_type_id=39, radius_m=radius_m, num_rows=20)
        for raw in items:
            cid = str(raw.get("contentid") or "")
            if not cid or cid in seen: 
                continue
            seen.add(cid)
            norm = _normalize(raw, "food", lat, lon)
            if norm: foods.append(norm)
        if len(foods) >= 60: break

    log.info("[TourAPI] collected tour=%d food=%d total=%d", len(tours), len(foods), len(tours)+len(foods))
    return {"tour": tours, "food": foods, "all": tours + foods}

# -------------------------
# 라우트
# -------------------------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/route", methods=["POST"])
def route():
    try:
        data = request.get_json(force=True, silent=True) or {}
        start_in = data.get("start")
        end_in = data.get("end")
        if not start_in or not end_in:
            return jsonify({"error": "출발지/도착지를 입력하세요."}), 400

        start = geocode_google(start_in) if isinstance(start_in, str) else tuple(start_in)
        end   = geocode_google(end_in)   if isinstance(end_in, str)   else tuple(end_in)
        if not start or not end:
            return jsonify({"error": "❌ 주소 변환 실패"}), 400

        waypoint = find_best_beach_waypoint(start, end)
        if not waypoint:
            return jsonify({"error": "❌ 경유지 탐색 실패"}), 500

        route_data, status = get_ors_route(start, waypoint, end)
        if status != 200 or "error" in route_data:
            return jsonify({"error": route_data.get("error", f"OpenRouteService 오류({status})")}), status

        # 경로 주변 관광/맛집
        around = search_along_route(route_data)

        wp_addr = reverse_geocode_google(waypoint[1], waypoint[2])

        return jsonify({
            "route": route_data,
            "waypoint": { "name": waypoint[0], "lat": waypoint[1], "lon": waypoint[2], "address": wp_addr },
            "spots": around["all"],
            "restaurants": around["food"],  # 필요 시 사용
        }), 200

    except Exception as e:
        log.exception("route error")
        return jsonify({"error": f"❌ 서버 오류: {str(e)}"}), 500

# (선택) 상세 템플릿
@app.route("/tour_detail/<contentid>")
def tour_detail(contentid):
    # 상세는 KorService1이 가장 호환성이 좋음
    url = "https://apis.data.go.kr/B551011/KorService1/detailCommon1"
    params = {
        "serviceKey": TOURAPI_KEY,
        "MobileOS": "ETC",
        "MobileApp": "CoastalDrive",
        "contentId": contentid,
        "overviewYN": "Y",
        "defaultYN": "Y",
        "firstImageYN": "Y",
        "addrinfoYN": "Y",
        "mapinfoYN": "Y",
        "_type": "json"
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        j = r.json()
        items = j.get("response", {}).get("body", {}).get("items", {}).get("item", [])
        item = items[0] if isinstance(items, list) and items else {}
        return render_template("tour_detail.html", item=item)
    except Exception as e:
        return f"<h2>TourAPI 상세 호출 오류</h2><p>{str(e)}</p>", 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
