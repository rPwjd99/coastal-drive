import os
import requests
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import geopandas as gpd
import math
from shapely.geometry import Point

app = Flask(__name__)
CORS(app)

# 해안선 로드
coastline = gpd.read_file("coastal_route_result.geojson").to_crs(epsg=4326)
coast_points = []
for geom in coastline.explode(index_parts=False).geometry:
    if geom.geom_type == "LineString":
        coast_points.extend(geom.coords)
    elif geom.geom_type == "MultiLineString":
        for line in geom:
            coast_points.extend(line.coords)

def haversine(coord1, coord2):
    R = 6371
    lat1, lon1 = map(math.radians, coord1)
    lat2, lon2 = map(math.radians, coord2)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))

def find_waypoint(start):
    lat_cands = [pt for pt in coast_points if abs(pt[1] - start[0]) < 0.1 and pt[0] > start[1]]
    lon_cands = [pt for pt in coast_points if abs(pt[0] - start[1]) < 0.1 and pt[1] > start[0]]

    lat_pt = min(lat_cands, key=lambda pt: haversine(start, (pt[1], pt[0]))) if lat_cands else None
    lon_pt = min(lon_cands, key=lambda pt: haversine(start, (pt[1], pt[0]))) if lon_cands else None

    if lat_pt and lon_pt:
        d_lat = haversine(start, (lat_pt[1], lat_pt[0]))
        d_lon = haversine(start, (lon_pt[1], lon_pt[0]))
        return lat_pt if d_lat <= d_lon else lon_pt
    return lat_pt or lon_pt

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/route", methods=["POST"])
def route():
    data = request.get_json()
    start = data["start"]  # [lat, lon]
    end = data["end"]      # [lat, lon]

    waypoint = find_waypoint(tuple(start))
    if not waypoint:
        return jsonify({"error": "경유지 해안 좌표를 찾을 수 없습니다."}), 400

    coords = [[start[1], start[0]], [waypoint[0], waypoint[1]], [end[1], end[0]]]
    headers = {
        "Authorization": os.getenv("ORS_API_KEY"),
        "Content-Type": "application/json"
    }
    payload = {"coordinates": coords, "format": "geojson"}

    response = requests.post(
        "https://api.openrouteservice.org/v2/directions/driving-car/geojson",
        headers=headers, json=payload
    )

    if response.status_code == 200:
        return jsonify(response.json())
    return jsonify({"error": response.text}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)), debug=True)
