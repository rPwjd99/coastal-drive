from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import requests
import os
import geopandas as gpd
from shapely.geometry import Point
import math

app = Flask(__name__)
CORS(app)

# GeoJSON 경로 설정
GEOJSON_PATH = os.path.join(os.path.dirname(__file__), "coastal-drive", "coastal_route_result.geojson")
coastline = gpd.read_file(GEOJSON_PATH).to_crs(epsg=4326)
coastline_points = coastline.explode(index_parts=False).geometry

# 해안 좌표 추출
coords = []
for geom in coastline_points:
    if geom.geom_type == "LineString":
        coords.extend(list(geom.coords))
    elif geom.geom_type == "MultiLineString":
        for line in geom:
            coords.extend(list(line.coords))

# 거리 계산 함수
def haversine(coord1, coord2):
    R = 6371
    lat1, lon1 = map(math.radians, coord1)
    lat2, lon2 = map(math.radians, coord2)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2)**2
    return R * 2 * math.asin(math.sqrt(a))

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/geocode", methods=["POST"])
def geocode():
    data = request.get_json()
    address = data.get("address")
    GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
    if not GOOGLE_API_KEY:
        return jsonify({"error": "Google API Key not set"}), 500

    url = f"https://maps.googleapis.com/maps/api/geocode/json?address={address}&key={GOOGLE_API_KEY}"
    res = requests.get(url)
    result = res.json()

    if result["status"] != "OK":
        return jsonify({"error": "주소를 찾을 수 없습니다."}), 400

    location = result["results"][0]["geometry"]["location"]
    return jsonify({"lat": location["lat"], "lng": location["lng"]})

@app.route("/route", methods=["POST"])
def route():
    data = request.get_json()
    start = data.get("start")
    end = data.get("end")
    if not start or not end:
        return jsonify({"error": "출발지 또는 도착지 좌표가 없습니다."}), 400

    start_coord = (start["lat"], start["lng"])
    end_coord = (end["lat"], end["lng"])

    # 후보 좌표: 위도 기준
    lat_candidates = [pt for pt in coords if abs(pt[1] - start_coord[0]) < 0.1 and pt[0] > start_coord[1]]
    lon_candidates = [pt for pt in coords if abs(pt[0] - start_coord[1]) < 0.1 and pt[1] > start_coord[0]]

    nearest_lat = min(lat_candidates, key=lambda x: haversine(start_coord, (x[1], x[0]))) if lat_candidates else None
    nearest_lon = min(lon_candidates, key=lambda x: haversine(start_coord, (x[1], x[0]))) if lon_candidates else None

    if nearest_lat and nearest_lon:
        dist_lat = haversine(start_coord, (nearest_lat[1], nearest_lat[0]))
        dist_lon = haversine(start_coord, (nearest_lon[1], nearest_lon[0]))
        waypoint = nearest_lat if dist_lat <= dist_lon else nearest_lon
    elif nearest_lat:
        waypoint = nearest_lat
    elif nearest_lon:
        waypoint = nearest_lon
    else:
        return jsonify({"error": "해안 경유지를 찾을 수 없습니다."}), 500

    ORS_API_KEY = os.getenv("ORS_API_KEY")
    if not ORS_API_KEY:
        return jsonify({"error": "ORS API Key not set"}), 500

    coords_list = [
        [start_coord[1], start_coord[0]],
        [waypoint[0], waypoint[1]],
        [end_coord[1], end_coord[0]],
    ]

    headers = {
        "Authorization": ORS_API_KEY,
        "Content-Type": "application/json"
    }

    payload = {"coordinates": coords_list, "format": "geojson"}
    response = requests.post(
        "https://api.openrouteservice.org/v2/directions/driving-car/geojson",
        headers=headers, json=payload)

    if response.status_code != 200:
        return jsonify({"error": "경로 요청 실패", "details": response.text}), 500

    return jsonify(response.json())

if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", 10000)))
