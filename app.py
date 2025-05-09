import os
import json
import requests
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from dotenv import load_dotenv
import geopandas as gpd
from shapely.geometry import Point
from math import radians, cos, sin, asin, sqrt

# Load environment variables
load_dotenv()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
ORS_API_KEY = os.getenv("ORS_API_KEY")
TOURAPI_KEY = os.getenv("TOURAPI_KEY")

# Flask app
app = Flask(__name__)
CORS(app)

# Load coastline GeoJSON (from root directory)
COASTLINE_PATH = "coastal_route_result.geojson"
coastline_gdf = gpd.read_file(COASTLINE_PATH)

# Haversine distance

def haversine(lon1, lat1, lon2, lat2):
    lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    return 6371 * 2 * asin(sqrt(a))

# Geocoding

def geocode(address):
    url = f"https://maps.googleapis.com/maps/api/geocode/json?address={address}&key={GOOGLE_API_KEY}"
    response = requests.get(url)
    data = response.json()
    if data['status'] == 'OK':
        location = data['results'][0]['geometry']['location']
        return location['lat'], location['lng']
    else:
        return None

# Route via ORS

def get_route(coords):
    url = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
    headers = {"Authorization": ORS_API_KEY, "Content-Type": "application/json"}
    body = {"coordinates": coords}
    r = requests.post(url, headers=headers, json=body)
    if r.status_code == 200:
        return r.json()
    return None

# Find closest coast point

def find_nearest_coast(lat, lon):
    distances = coastline_gdf.geometry.apply(lambda p: haversine(lon, lat, p.x, p.y))
    nearest = coastline_gdf.iloc[distances.idxmin()].geometry
    return nearest.y, nearest.x

# Tourist spots

def get_tourspots(lat, lon):
    url = f"http://apis.data.go.kr/B551011/KorService1/locationBasedList1?serviceKey={TOURAPI_KEY}&mapX={lon}&mapY={lat}&radius=5000&MobileOS=ETC&MobileApp=CoastalDrive&_type=json"
    res = requests.get(url)
    items = res.json().get("response", {}).get("body", {}).get("items", {}).get("item", [])
    return [{"title": i.get("title"), "addr": i.get("addr1"), "img": i.get("firstimage", "")} for i in items]

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/route", methods=["POST"])
def route():
    data = request.get_json()
    start_addr = data.get("start")
    end_addr = data.get("end")

    start = geocode(start_addr)
    end = geocode(end_addr)

    if not start or not end:
        return jsonify({"error": "Invalid address"}), 400

    coast_lat, coast_lon = find_nearest_coast(*start)

    coords = [[start[1], start[0]], [coast_lon, coast_lat], [end[1], end[0]]]
    route = get_route(coords)
    if not route:
        return jsonify({"error": "Route not found"}), 500

    spots = get_tourspots(*end)

    return jsonify({"route": route, "spots": spots})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)), debug=True)
