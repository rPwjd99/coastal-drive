from flask import Flask, request, jsonify, send_from_directory
import os
import requests
import json
from shapely.geometry import Point
import geopandas as gpd
from math import radians, cos, sin, asin, sqrt
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

# GeoJSON 경로 (루트 디렉토리에 파일 존재할 경우 수정)
COASTLINE_FILE = "coastal_route_result.geojson"

def haversine(coord1, coord2):
    R = 6371  # km
    lat1, lon1 = map(radians, coord1)
    lat2, lon2 = map(radians, coord2)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return R * 2 * asin(sqrt(a))

def get_coords_from_vworld(address):
    VWORLD_API_KEY = os.getenv("VWORLD_API_KEY")
    url = f"https://api.vworld.kr/req/address?service=address&request=getcoord&format=json&type=road&address={address}&key={VWORLD_API_KEY}"
    res = requests.get(url)
    data = res.json()
    if "response" in data and "result" in data["response"]:
        point = data["response"]["result"]["point"]
        return float(point["y"]), float(point["x"])
    else:
        return None

def get_detour_point(start_coord, end_coord):
    gdf = gpd.read_file(COASTLINE_FILE).to_crs(epsg=4326)
    coastline_points = gdf.explode(index_parts=False).geometry

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
        return (nearest_lat[1], nearest_lat[0]) if dist_lat <= dist_lon else (nearest_lon[1], nearest_lon[0])
    elif nearest_lat:
        return (nearest_lat[1], nearest_lat[0])
    elif nearest_lon:
        return (nearest_lon[1], nearest_lon[0])
    else:
        return None

@app.route("/")
def index():
    return send_from_directory(".", "index.html")

@app.route("/route", methods=["POST"])
def get_route():
    data = request.json
    start_address = data["start"]
    end_address = data["end"]

    start = get_coords_from_vworld(start_address)
    end = get_coords_from_vworld(end_address)

    if not start or not end:
        return jsonify({"error": "Invalid address"}), 400

    waypoint = get_detour_point(start, end)

    if not waypoint:
        return jsonify({"error": "No suitable detour point found"}), 400

    coordinates = [[start[1], start[0]], [waypoint[1], waypoint[0]], [end[1], end[0]]]

    headers = {
        "Authorization": os.getenv("ORS_API_KEY"),
        "Content-Type": "application/json"
    }
    payload = {"coordinates": coordinates, "format": "geojson"}
    response = requests.post("https://api.openrouteservice.org/v2/directions/driving-car/geojson",
                             headers=headers, json=payload)

    if response.status_code == 200:
        return jsonify(response.json())
    else:
        return jsonify({"error": "ORS route request failed", "detail": response.text}), 500

@app.route("/api/tourspot", methods=["POST"])
def tourspot():
    data = request.json
    lat, lng = data["lat"], data["lng"]
    TOURAPI_KEY = os.getenv("TOURAPI_KEY")
    url = f"https://apis.data.go.kr/B551011/KorService1/locationBasedList1"
    params = {
        "ServiceKey": TOURAPI_KEY,
        "numOfRows": 30,
        "pageNo": 1,
        "MobileOS": "ETC",
        "MobileApp": "SeaRoute",
        "mapX": lng,
        "mapY": lat,
        "radius": 5000,
        "_type": "json"
    }
    response = requests.get(url, params=params)
    if response.status_code == 200:
        items = response.json().get("response", {}).get("body", {}).get("items", {}).get("item", [])
        spots = [{
            "title": spot["title"],
            "addr": spot.get("addr1", ""),
            "mapx": float(spot["mapx"]),
            "mapy": float(spot["mapy"]),
            "image": spot.get("firstimage", "")
        } for spot in items]
        return jsonify(spots)
    else:
        return jsonify({"error": "TourAPI request failed", "detail": response.text}), 500

if __name__ == "__main__":
    app.run(port=int(os.environ.get("PORT", 10000)), debug=True)
