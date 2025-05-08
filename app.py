from flask import Flask, request, jsonify, render_template
import os
import json
import requests
from flask_cors import CORS
from shapely.geometry import Point
import geopandas as gpd
from math import radians, cos, sin, asin, sqrt

app = Flask(__name__)
CORS(app)

# API 키 환경 변수 불러오기
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
ORS_API_KEY = os.getenv("ORS_API_KEY")

# 해안선 GeoJSON 파일 불러오기
COASTLINE_PATH = "coastal_route_result.geojson"
coastline = gpd.read_file(COASTLINE_PATH).to_crs(epsg=4326)

# GeoJSON 점 좌표 추출
coastline_points = coastline.explode(index_parts=False).geometry
coords = []
for geom in coastline_points:
    if geom.geom_type == "LineString":
        coords.extend(list(geom.coords))
    elif geom.geom_type == "MultiLineString":
        for line in geom:
            coords.extend(list(line.coords))

def haversine(coord1, coord2):
    R = 6371
    lat1, lon1 = map(radians, coord1)
    lat2, lon2 = map(radians, coord2)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat/2)**2 + cos(lat1)*cos(lat2)*sin(dlon/2)**2
    return R * 2 * asin(sqrt(a))

def geocode_address(address):
    url = f"https://maps.googleapis.com/maps/api/geocode/json?address={address}&key={GOOGLE_API_KEY}"
    response = requests.get(url)
    if response.status_code != 200:
        return None
    data = response.json()
    if data["status"] == "OK":
        location = data["results"][0]["geometry"]["location"]
        return (location["lat"], location["lng"])
    return None

@app.route('/')
def home():
    return render_template("index.html")

@app.route('/route', methods=['POST'])
def get_route():
    data = request.json
    start_addr = data.get("start")
    end_addr = data.get("end")

    start = geocode_address(start_addr)
    end = geocode_address(end_addr)

    if not start or not end:
        return jsonify({"error": "주소를 변환할 수 없습니다."}), 400

    # 해안 경유지 선택
    lat_candidates = [pt for pt in coords if abs(pt[1] - start[0]) < 0.1 and pt[0] > start[1]]
    lon_candidates = [pt for pt in coords if abs(pt[0] - start[1]) < 0.1 and pt[1] > start[0]]

    nearest_lat = min(lat_candidates, key=lambda x: haversine(start, (x[1], x[0]))) if lat_candidates else None
    nearest_lon = min(lon_candidates, key=lambda x: haversine(start, (x[1], x[0]))) if lon_candidates else None

    if nearest_lat and nearest_lon:
        dist_lat = haversine(start, (nearest_lat[1], nearest_lat[0]))
        dist_lon = haversine(start, (nearest_lon[1], nearest_lon[0]))
        waypoint = nearest_lat if dist_lat <= dist_lon else nearest_lon
    elif nearest_lat:
        waypoint = nearest_lat
    elif nearest_lon:
        waypoint = nearest_lon
    else:
        waypoint = None

    if waypoint:
        coords_list = [
            [start[1], start[0]],
            [waypoint[0], waypoint[1]],
            [end[1], end[0]]
        ]
    else:
        coords_list = [
            [start[1], start[0]],
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

    if response.status_code != 200:
        return jsonify({"error": "경로 요청 실패", "detail": response.text}), 500

    route_geojson = response.json()
    return jsonify({
        "route": route_geojson,
        "start": {"lat": start[0], "lng": start[1]},
        "end": {"lat": end[0], "lng": end[1]},
        "waypoint": {"lat": waypoint[1], "lng": waypoint[0]} if waypoint else None
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)), debug=True)
