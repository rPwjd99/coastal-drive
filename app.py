from flask import Flask, request, jsonify, send_from_directory
import os
import json
import requests
from flask_cors import CORS
from shapely.geometry import Point
import geopandas as gpd
from math import radians, cos, sin, asin, sqrt

app = Flask(__name__)
CORS(app)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
ORS_API_KEY = os.getenv("ORS_API_KEY")
TOURAPI_KEY = os.getenv("TOURAPI_KEY")
VWORLD_API_KEY = os.getenv("VWORLD_API_KEY")

GEOJSON_PATH = "coastal_route_result.geojson"  # 루트에 있는 파일로 경로 수정

def geocode_address(address):
    url = f"https://maps.googleapis.com/maps/api/geocode/json?address={address}&key={GOOGLE_API_KEY}"
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        if data["status"] == "OK":
            location = data["results"][0]["geometry"]["location"]
            return location["lat"], location["lng"]
    return None

def haversine(coord1, coord2):
    R = 6371
    lat1, lon1 = map(radians, coord1)
    lat2, lon2 = map(radians, coord2)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat/2)**2 + cos(lat1)*cos(lat2)*sin(dlon/2)**2
    return R * 2 * asin(sqrt(a))

def find_waypoint(start_coord, end_coord):
    coastline = gpd.read_file(GEOJSON_PATH).to_crs(epsg=4326)
    coastline_points = coastline.explode(index_parts=False).geometry
    coords = []
    for geom in coastline_points:
        if geom.geom_type == "LineString":
            coords.extend(list(geom.coords))
        elif geom.geom_type == "MultiLineString":
            for line in geom:
                coords.extend(list(line.coords))

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
        waypoint = None

    if waypoint:
        return (waypoint[1], waypoint[0])  # (lat, lon)
    else:
        return None

def get_route(coordinates):
    headers = {
        "Authorization": ORS_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "coordinates": coordinates,
        "format": "geojson"
    }
    response = requests.post("https://api.openrouteservice.org/v2/directions/driving-car/geojson",
                             headers=headers, json=payload)
    if response.status_code == 200:
        return response.json()
    else:
        print("❌ ORS 응답 오류:", response.status_code, response.text)
        return None

@app.route("/")
def index():
    return send_from_directory(".", "index.html")

@app.route("/geocode", methods=["POST"])
def geocode():
    data = request.json
    address = data.get("address")
    if not address:
        return jsonify({"error": "No address provided"}), 400
    location = geocode_address(address)
    if location:
        return jsonify({"lat": location[0], "lng": location[1]})
    else:
        return jsonify({"error": "Geocode API returned no results."}), 404

@app.route("/route", methods=["POST"])
def route():
    data = request.json
    start = data.get("start")  # { lat, lng }
    end = data.get("end")      # { lat, lng }
    if not start or not end:
        return jsonify({"error": "Missing start or end coordinates"}), 400

    start_coord = (start["lat"], start["lng"])
    end_coord = (end["lat"], end["lng"])
    waypoint = find_waypoint(start_coord, end_coord)

    if waypoint:
        coords = [[start_coord[1], start_coord[0]],
                  [waypoint[1], waypoint[0]],
                  [end_coord[1], end_coord[0]]]
        route_geojson = get_route(coords)
        if route_geojson:
            return jsonify({"route": route_geojson, "waypoint": waypoint})
        else:
            return jsonify({"error": "Route request failed"}), 500
    else:
        return jsonify({"error": "No valid coastal waypoint found"}), 404

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)), debug=True)
