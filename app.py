from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import os
import requests
import geopandas as gpd
from shapely.geometry import Point
from math import radians, cos, sin, asin, sqrt

# Flask 초기화
app = Flask(__name__, static_folder='.', template_folder='.')
CORS(app)

# 환경변수
VWORLD_API_KEY = os.getenv("VWORLD_API_KEY")
ORS_API_KEY = os.getenv("ORS_API_KEY")

# GeoJSON 해안선 데이터 불러오기
COASTLINE_FILE = "coastal_route_result.geojson"
coastline = gpd.read_file(COASTLINE_FILE).to_crs(epsg=4326)
coastline_points = []
for geom in coastline.explode(index_parts=False).geometry:
    if geom.geom_type == "LineString":
        coastline_points.extend(list(geom.coords))
    elif geom.geom_type == "MultiLineString":
        for line in geom:
            coastline_points.extend(list(line.coords))

# 거리 계산 함수
def haversine(coord1, coord2):
    R = 6371
    lat1, lon1 = map(radians, coord1)
    lat2, lon2 = map(radians, coord2)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat/2)**2 + cos(lat1)*cos(lat2)*sin(dlon/2)**2
    return R * 2 * asin(sqrt(a))

# 주소 → 좌표 변환 (VWorld)
def geocode_vworld(address):
    url = f"https://api.vworld.kr/req/address?service=address&request=getcoord&format=json&type=road&address={address}&key={VWORLD_API_KEY}"
    resp = requests.get(url)
    data = resp.json()
    try:
        point = data['response']['result']['point']
        return float(point['y']), float(point['x'])  # lat, lon
    except:
        return None

# 가장 가까운 해안선 점 추출
def find_best_waypoint(start_coord):
    lat_candidates = [pt for pt in coastline_points if abs(pt[1] - start_coord[0]) < 0.1 and pt[0] > start_coord[1]]
    lon_candidates = [pt for pt in coastline_points if abs(pt[0] - start_coord[1]) < 0.1 and pt[1] > start_coord[0]]

    nearest_lat = min(lat_candidates, key=lambda x: haversine(start_coord, (x[1], x[0]))) if lat_candidates else None
    nearest_lon = min(lon_candidates, key=lambda x: haversine(start_coord, (x[1], x[0]))) if lon_candidates else None

    if nearest_lat and nearest_lon:
        dist_lat = haversine(start_coord, (nearest_lat[1], nearest_lat[0]))
        dist_lon = haversine(start_coord, (nearest_lon[1], nearest_lon[0]))
        return nearest_lat if dist_lat <= dist_lon else nearest_lon
    return nearest_lat or nearest_lon

# OpenRouteService 경로 요청
def get_route(coords_list):
    headers = {
        "Authorization": ORS_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "coordinates": coords_list,
        "format": "geojson"
    }
    response = requests.post("https://api.openrouteservice.org/v2/directions/driving-car/geojson", headers=headers, json=payload)
    if response.status_code == 200:
        return response.json()
    return None

# index.html 반환
@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

# 주소 → 좌표 변환 엔드포인트
@app.route('/geocode', methods=['POST'])
def geocode():
    data = request.json
    address = data.get("address")
    coord = geocode_vworld(address)
    if coord:
        return jsonify({"lat": coord[0], "lon": coord[1]})
    return jsonify({"error": "주소 변환 실패"}), 400

# 경로 계산 엔드포인트
@app.route('/route', methods=['POST'])
def route():
    data = request.json
    start = data.get("start")
    end = data.get("end")
    if not start or not end:
        return jsonify({"error": "좌표 누락"}), 400

    start_coord = (start["lat"], start["lon"])
    end_coord = (end["lat"], end["lon"])
    waypoint = find_best_waypoint(start_coord)

    if waypoint:
        coords_list = [
            [start_coord[1], start_coord[0]],
            [waypoint[0], waypoint[1]],
            [end_coord[1], end_coord[0]],
        ]
    else:
        coords_list = [
            [start_coord[1], start_coord[0]],
            [end_coord[1], end_coord[0]],
        ]

    route_geojson = get_route(coords_list)
    if route_geojson:
        return jsonify(route_geojson)
    return jsonify({"error": "경로 요청 실패"}), 500

# 정적 파일 처리
@app.route('/<path:path>')
def static_proxy(path):
    return send_from_directory('.', path)

if __name__ == '__main__':
    app.run(debug=True, port=10000)
