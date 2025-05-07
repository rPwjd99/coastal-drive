
import os
import json
import requests
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from dotenv import load_dotenv
import geopandas as gpd
from shapely.geometry import Point
from shapely.ops import nearest_points

load_dotenv()

VWORLD_API_KEY = os.getenv("VWORLD_API_KEY")
ORS_API_KEY = os.getenv("ORS_API_KEY")

app = Flask(__name__)
CORS(app)

coast_gdf = gpd.read_file("coastline.geojson").to_crs(epsg=4326)

def geocode_vworld(address):
    url = f"https://api.vworld.kr/req/address?service=address&request=getcoord&format=json&type=both&key={VWORLD_API_KEY}&address={address}"
    resp = requests.get(url)
    items = resp.json().get("response", {}).get("result", [])
    if not items:
        return None
    point = items[0].get("point", {})
    return float(point["y"]), float(point["x"])  # lat, lon

def get_nearest_coast_point(lat, lon, method="lat"):
    origin = Point(lon, lat)
    if method == "lat":
        subset = coast_gdf[(coast_gdf.geometry.y >= lat - 0.1) & (coast_gdf.geometry.y <= lat + 0.1)]
    else:
        subset = coast_gdf[(coast_gdf.geometry.x >= lon - 0.1) & (coast_gdf.geometry.x <= lon + 0.1)]

    if subset.empty:
        return None

    nearest_geom = nearest_points(origin, subset.unary_union)[1]
    return nearest_geom.y, nearest_geom.x  # lat, lon

def test_ors_route(points):
    url = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
    headers = {"Authorization": ORS_API_KEY, "Content-Type": "application/json"}
    data = {"coordinates": points}
    try:
        res = requests.post(url, json=data, headers=headers)
        if res.status_code == 200:
            return res.json()
    except Exception:
        pass
    return None

def find_buffered_coast(lat, lon):
    point = Point(lon, lat)
    buffer = point.buffer(0.05)  # ~5km in EPSG:4326
    candidates = coast_gdf[coast_gdf.geometry.within(buffer)]
    if candidates.empty:
        return None
    return candidates.geometry.iloc[0].y, candidates.geometry.iloc[0].x

@app.route("/")
def root():
    return send_file("index.html")

@app.route("/api/route")
def route():
    start_addr = request.args.get("start")
    end_addr = request.args.get("end")

    start = geocode_vworld(start_addr)
    end = geocode_vworld(end_addr)
    if not start or not end:
        return jsonify({"error": "Invalid address"}), 400

    coast_lat = get_nearest_coast_point(start[0], start[1], method="lat")
    coast_lon = get_nearest_coast_point(start[0], start[1], method="lon")

    if not coast_lat or not coast_lon:
        return jsonify({"error": "No nearby coast found"}), 404

    dist_lat = ((start[0]-coast_lat[0])**2 + (start[1]-coast_lat[1])**2)**0.5
    dist_lon = ((start[0]-coast_lon[0])**2 + (start[1]-coast_lon[1])**2)**0.5
    coast = coast_lat if dist_lat < dist_lon else coast_lon

    points = [[start[1], start[0]], [coast[1], coast[0]], [end[1], end[0]]]
    route_json = test_ors_route(points)

    if not route_json:
        buffered = find_buffered_coast(coast[0], coast[1])
        if buffered:
            points = [[start[1], start[0]], [buffered[1], buffered[0]], [end[1], end[0]]]
            route_json = test_ors_route(points)

    if not route_json:
        return jsonify({"error": "Failed to find route"}), 500

    return jsonify(route_json)
