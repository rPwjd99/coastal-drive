from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import os
import requests
import geopandas as gpd
from shapely.geometry import Point
from math import radians, cos, sin, asin, sqrt

app = Flask(__name__)
CORS(app)

# 경로 기반 GeoJSON 해안선 로드
COASTLINE_PATH = "coastal_route_result.geojson"
coastline = gpd.read_file(COASTLINE_PATH).to_crs(epsg=4326)
coastline_points = coastline.explode(index_parts=False).geometry

# 좌표 리스트화
coords = []
for geom in coastline_points:
    if geom.geom_type == "LineString":
        coords.extend(list(geom.coords))
    elif geom.geom_type == "MultiLineString":
        for line in geom:
            coords.extend(list(line.coords))

# 거리 계산 함수 (Haversine)
def haversine(coord1, coord2):
    R = 6371
    lat1, lon1 = map(radians, coord1)
    lat2, lon2 = map(radians, coord2)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat / 2)**2 + cos(lat1) * cos(lat2) * sin(dlon / 2)**2
    return R * 2 * asin(sqrt(a))

# 정적 파일 index.html 반환
@app.route("/")
def serve_index():
    return send_from_directory(".", "index.html")

# 경로 계산 API
@app.route("/route", methods=["POST"])
def calculate_route():
    data = request.get_json()
    start = tuple(data["start"])
    end = tuple(data["end"])
    ORS_API_KEY = os.getenv("ORS_API_KEY")

    # 위도 유사 해안선 후보
    lat_candidates = [pt for pt in coords if abs(pt[1] - start[0]) < 0.1 and pt[0] > start[1]]
    lon_candidates = [pt for pt in coords if abs(pt[0] - start[1]) < 0.1 and pt[1] > start[0]]

    nearest_lat = min(lat_candidates, key=lambda x: haversine(start, (x[1], x[0]))) if lat_candidates else None
    nearest_lon = min(lon_candidates, key=lambda x: haversine(start, (x[1], x[0]))) if lon_candidates else None

    waypoint = None
    if nearest_lat and nearest_lon:
        dist_lat = haversine(start, (nearest_lat[1], nearest_lat[0]))
        dist_lon = haversine(start, (nearest_lon[1], nearest_lon[0]))
        waypoint = nearest_lat if dist_lat <= dist_lon else nearest_lon
    elif nearest_lat:
        waypoint = nearest_lat
    elif nearest_lon:
        waypoint = nearest_lon

    if not waypoint:
        return jsonify({"error": "No nearby coastal waypoint found."}), 400

    coords_list = [
        [start[1], start[0]],
        [waypoint[0], waypoint[1]],
        [end[1], end[0]]
    ]

    headers = {
        "Authorization": ORS_API_KEY,
        "Content-Type": "application/json"
    }

    payload = {
        "coordinates": coords_list,
        "format": "geojson"
    }

    response = requests.post("https://api.openrouteservice.org/v2/directions/driving-car/geojson",
                             headers=headers, json=payload)

    if response.status_code == 200:
        return jsonify({
            "route": response.json(),
            "waypoint": {"lat": waypoint[1], "lng": waypoint[0]}
        })
    else:
        return jsonify({"error": "OpenRouteService routing failed", "detail": response.text}), 500

# 주소 → 좌표 변환
@app.route("/geocode", methods=["POST"])
def geocode():
    address = request.get_json().get("address")
    VWORLD_API_KEY = os.getenv("VWORLD_API_KEY")
    url = f"https://api.vworld.kr/req/address?service=address&request=getcoord&format=json&type=road&address={address}&key={VWORLD_API_KEY}"
    res = requests.get(url)
    data = res.json()

    if data.get("response", {}).get("status") == "OK":
        point = data["response"]["result"]["point"]
        return jsonify({"lat": float(point["y"]), "lng": float(point["x"])})
    else:
        return jsonify({"error": "Address not found"}), 400

# 관광지 검색 (TourAPI)
@app.route("/api/tourspot", methods=["POST"])
def tourspot():
    data = request.get_json()
    lat, lng = data["lat"], data["lng"]
    TOURAPI_KEY = os.getenv("TOURAPI_KEY")

    url = f"http://apis.data.go.kr/B551011/KorService1/locationBasedList1"
    params = {
        "MobileOS": "ETC",
        "MobileApp": "SeaDrive",
        "serviceKey": TOURAPI_KEY,
        "mapX": lng,
        "mapY": lat,
        "radius": 5000,
        "arrange": "A",
        "numOfRows": 10,
        "pageNo": 1,
        "_type": "json"
    }

    res = requests.get(url, params=params)
    return jsonify(res.json())

if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", 10000)))
