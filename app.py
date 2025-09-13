# app.py
# 변경점 요약:
# 1) '/'는 루트의 index.html을 그대로 서빙(send_from_directory) → templates 폴더 없이도 동작
# 2) '/route'와 '/api/route' 둘 다 POST 지원(+ GET은 '/'로 리다이렉트) → 404 방지
# 3) JSON/폼/쿼리 모두 파싱 → 400 방지
# 4) waypoint 없을 경우 start→end 직통 경로로 자동 폴백
# 5) TourAPI HTTPS 사용, 에러시에도 빈 리스트 반환

import os
import logging
from typing import Any, Dict, List, Optional, Tuple

import requests
from flask import Flask, request, jsonify, send_from_directory, redirect

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

try:
    # /beaches_coordinates.py 에 dict 형태의 beach_coords 가 있어야 합니다.
    # 예: beach_coords = {"속초해변": (128.593, 38.207), ...}  # (lon, lat)
    from beaches_coordinates import beach_coords
except Exception:
    beach_coords = {}

app = Flask(__name__, static_folder="static")

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("coastal-drive")

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
ORS_API_KEY = os.getenv("ORS_API_KEY")
TOURAPI_KEY = os.getenv("TOURAPI_KEY") or "e1tU33wjMx2nynKjH8yDBm/S4YNne6B8mpCOWtzMH9TSONF71XG/xAwPqyv1fANpgeOvbPY+Le+gM6cYCnWV8w=="

def haversine(lat1, lon1, lat2, lon2):
    from math import radians, cos, sin, asin, sqrt
    R = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2.0)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2.0)**2
    return 2.0 * R * asin(sqrt(a))

def geocode_google(address: str) -> Optional[Tuple[float, float]]:
    if not GOOGLE_API_KEY:
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

def is_in_coastal_bounds(lat: float, lon: float) -> bool:
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
        # 위도 정렬 후보
        if abs(lat - start_lat) < 0.2 and (end_lon - start_lon) * (lon - start_lon) > 0:
            lat_candidates.append((name, lat, lon, haversine(end_lat, end_lon, lat, lon)))
        # 경도 정렬 후보
        if abs(lon - start_lon) < 0.2 and (end_lat - start_lat) * (lat - start_lat) > 0:
            lon_candidates.append((name, lat, lon, haversine(end_lat, end_lon, lat, lon)))
    best_lat = min(lat_candidates, key=lambda x: x[3]) if lat_candidates else None
    best_lon = min(lon_candidates, key=lambda x: x[3]) if lon_candidates else None
    if best_lat and best_lon:
        return best_lat if best_lat[3] <= best_lon[3] else best_lon
    return best_lat or best_lon

def get_ors_route(start: Tuple[float, float],
                  end: Tuple[float, float],
                  waypoint: Optional[Tuple[str, float, float]] = None) -> Tuple[Dict[str, Any], int]:
    if not ORS_API_KEY:
        return {"error": "ORS_API_KEY is missing"}, 500
    coords: List[List[float]] = [[start[1], start[0]]]
    if waypoint:
        coords.append([waypoint[2], waypoint[1]])
    coords.append([end[1], end[0]])
    try:
        r = requests.post(
            "https://api.openrouteservice.org/v2/directions/driving-car/geojson",
            headers={"Authorization": ORS_API_KEY, "Content-Type": "application/json"},
            json={"coordinates": coords},
            timeout=25,
        )
        return r.json(), r.status_code
    except Exception as e:
        return {"error": str(e)}, 500

def search_tour_spots_along_route(geojson: Dict[str, Any]) -> List[Dict[str, Any]]:
    try:
        coords = geojson["features"][0]["geometry"]["coordinates"]
    except Exception:
        return []
    spots: List[Dict[str, Any]] = []
    seen: set = set()
    for idx in range(0, len(coords), 10):
        try:
            lon, lat = coords[idx]
            r = requests.get(
                "https://apis.data.go.kr/B551011/KorService1/locationBasedList1",
                params={
                    "serviceKey": TOURAPI_KEY,
                    "mapX": lon,
                    "mapY": lat,
                    "radius": 5000,
                    "listYN": "Y",
                    "arrange": "E",
                    "numOfRows": 10,
                    "pageNo": 1,
                    "MobileOS": "ETC",
                    "MobileApp": "SeaRoute",
                    "_type": "json",
                },
                timeout=20,
            )
            items = r.json().get("response", {}).get("body", {}).get("items", {}).get("item", []) or []
            for it in items:
                cid = it.get("contentid")
                if cid and cid not in seen:
                    seen.add(cid)
                    spots.append({
                        "title": it.get("title"),
                        "addr1": it.get("addr1"),
                        "mapx": it.get("mapx"),
                        "mapy": it.get("mapy"),
                        "firstimage": it.get("firstimage"),
                        "homepage": it.get("homepage") or "",
                    })
        except Exception:
            continue
    return spots

def _coerce_json() -> Dict[str, Any]:
    j = request.get_json(silent=True, force=True)
    if isinstance(j, dict):
        return j
    if request.form:
        return {k: request.form.get(k) for k in request.form}
    if request.args:
        return {k: request.args.get(k) for k in request.args}
    return {}

@app.route("/")
def index():
    # 루트에 있는 index.html 그대로 서빙
    return send_from_directory(".", "index.html")

@app.route("/favicon.ico")
def favicon():
    return "", 204

def _route_handler():
    data = _coerce_json()
    start_in = data.get("start") or data.get("origin") or data.get("from")
    end_in = data.get("end") or data.get("destination") or data.get("to")
    if not start_in or not end_in:
        return jsonify({"error": "start/end 누락"}), 400

    # 주소 → 좌표
    start = geocode_google(start_in) if isinstance(start_in, str) else start_in
    end = geocode_google(end_in) if isinstance(end_in, str) else end_in
    if not start or not end:
        return jsonify({"error": "주소 변환 실패"}), 400

    # waypoint 탐색(없으면 직통 경로)
    waypoint = find_best_beach_waypoint(start, end) if beach_coords else None

    # ORS 경로 호출
    route_data, status = get_ors_route(start, end, waypoint)
    if status != 200 or "error" in route_data:
        return jsonify({"error": route_data.get("error", f"OpenRouteService 실패({status})")}), status

    # 경로 주변 관광지
    spots = search_tour_spots_along_route(route_data)

    return jsonify({
        "route": route_data,
        "waypoint": None if not waypoint else {
            "name": waypoint[0], "lat": waypoint[1], "lon": waypoint[2],
            "address": reverse_geocode_google(waypoint[1], waypoint[2]) or ""
        },
        "spots": spots,
    }), 200

@app.route("/route", methods=["POST", "GET"])
def route():
    if request.method == "GET":
        # 실수로 GET 요청 시 메인으로 보내 404 방지
        return redirect("/")
    return _route_handler()

@app.route("/api/route", methods=["POST"])
def api_route():
    return _route_handler()

@app.route("/healthz")
def healthz():
    return jsonify({"ok": True})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
