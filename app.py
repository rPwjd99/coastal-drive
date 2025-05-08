from flask import Flask, request, jsonify, send_from_directory, render_template
from flask_cors import CORS
from shapely.geometry import Point, LineString
import geopandas as gpd
import requests
import os
from dotenv import load_dotenv
from math import radians, cos, sin, asin, sqrt

load_dotenv()

app = Flask(__name__, static_folder='static', template_folder='templates')
CORS(app)

# 해안선 데이터 로드
coastline = gpd.read_file("coastal_route_result.geojson").to_crs(epsg=4326)
coast_coords = []
for geom in coastline.explode(index_parts=False).geometry:
    if geom.geom_type == "LineString":
        coast_coords.extend(list(geom.coords))
    elif geom.geom_type == "MultiLineString":
        for line in geom:
            coast_coords.extend(list(line.coords))

# 거리 계산 함수
def haversine(coord1, coord2):
    R = 6371  # Earth radius in km
    lat1, lon1 = radians(coord1[0]), radians(coord1[1])
    lat2, lon2 = radians(coord2[0]), radians(coord2[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return R * 2 * asin(sqrt(a))

# 좌표 변환 함수 (VWorld 서버에서)
def geocode_vworld(address):
    key = os.getenv("VWORLD_API_KEY")
    url = f"https://api.vworld.kr/req/address?service=address&request=getcoord&format=json&type=both&key={key}&address={address}"
    response = requests.get(url)
    data = response.json()
    if data["response"]["status"] == "OK":
        result = data["response"]["result"][0]["point"]
        return float(result["y"]), float(result["x"])
    return None

# 도로 연결 확인 함수 (OpenRouteService)
def validate_coords(coord):
    key = os.getenv("ORS_API_KEY")
    headers = {"Authorization": key, "Content-Type": "application/json"}
    payload = {"coordinates": [[coord[1], coord[0]], [coord[1] + 0.001, coord[0] + 0.001]]}
    response = requests.post("https://api.openrouteservice.org/v2/directions/driving-car/geojson", headers=headers, json=payload)
    return response.status_code == 200

# 최적 해안 경유지 탐색 함수
def find_best_waypoint(start, end):
    lat_candidates = [pt for pt in coast_coords if abs(pt[1] - start[0]) < 0.1 and pt[0] > start[1]]
    lon_candidates = [pt for pt in coast_coords if abs(pt[0] - start[1]) < 0.1 and pt[1] > start[0]]

    nearest_lat = min(lat_candidates, key=lambda x: haversine(start, (x[1], x[0]))) if lat_candidates else None
    nearest_lon = min(lon_candidates, key=lambda x: haversine(start, (x[1], x[0]))) if lon_candidates else None

    best = None
    if nearest_lat and validate_coords((nearest_lat[1], nearest_lat[0])):
        best = (nearest_lat[1], nearest_lat[0])
    if nearest_lon and validate_coords((nearest_lon[1], nearest_lon[0])):
        if best is None or haversine(start, (nearest_lon[1], nearest_lon[0])) < haversine(start, best):
            best = (nearest_lon[1], nearest_lon[0])
    return best

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/route", methods=["POST"])
def get_route():
    data = request.json
    start_addr = data.get("start")
    end_addr = data.get("end")

    start = geocode_vworld(start_addr)
    end = geocode_vworld(end_addr)

    if not start or not end:
        return jsonify({"error": "주소 변환 실패"}), 400

    waypoint = find_best_waypoint(start, end)
    if not waypoint:
        return jsonify({"error": "경유지 탐색 실패"}), 500

    key = os.getenv("ORS_API_KEY")
    headers = {"Authorization": key, "Content-Type": "application/json"}
    coords = [[start[1], start[0]], [waypoint[1], waypoint[0]], [end[1], end[0]]]
    payload = {"coordinates": coords}

    response = requests.post("https://api.openrouteservice.org/v2/directions/driving-car/geojson", headers=headers, json=payload)
    if response.status_code == 200:
        return jsonify(response.json())
    return jsonify({"error": "경로 요청 실패", "detail": response.text}), 500

@app.route("/tourspot", methods=["POST"])
def get_tourspot():
    lat = request.json.get("lat")
    lon = request.json.get("lon")
    key = os.getenv("TOUR_API_KEY")
    url = f"https://apis.data.go.kr/B551011/KorService1/locationBasedList1?MobileOS=ETC&MobileApp=testApp&_type=json&mapX={lon}&mapY={lat}&radius=5000&numOfRows=20&pageNo=1&serviceKey={key}"
    r = requests.get(url)
    return jsonify(r.json())

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)), debug=True)
