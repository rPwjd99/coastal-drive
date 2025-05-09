from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import os
import requests
import geopandas as gpd
from shapely.geometry import Point
from math import radians, cos, sin, asin, sqrt
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
ORS_API_KEY = os.getenv("ORS_API_KEY")
TOURAPI_KEY = os.getenv("TOURAPI_KEY")

# Load coastline data from root directory
COASTLINE_PATH = "coastal_route_result.geojson"
coastline_gdf = gpd.read_file(COASTLINE_PATH).to_crs(epsg=4326)

app = Flask(__name__)
CORS(app)

# Haversine distance

def haversine(lon1, lat1, lon2, lat2):
    lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * asin(sqrt(a))
    km = 6371 * c
    return km

# Find nearest coastline point

def find_nearest_coast(lat, lon):
    candidates = coastline_gdf.geometry.apply(lambda geom: geom.interpolate(geom.length / 2) if geom.geom_type == 'LineString' else geom.centroid)
    distances = candidates.apply(lambda p: haversine(lon, lat, p.x, p.y))
    nearest = candidates.iloc[distances.idxmin()]
    return nearest.y, nearest.x

# Geocode address using Google

def geocode_address(address):
    url = f"https://maps.googleapis.com/maps/api/geocode/json?address={address}&key={GOOGLE_API_KEY}"
    response = requests.get(url)
    data = response.json()
    if data['status'] == 'OK':
        location = data['results'][0]['geometry']['location']
        return location['lat'], location['lng']
    else:
        raise Exception("Geocoding failed")

# Get road route from OpenRouteService

def get_route(coords):
    headers = {"Authorization": ORS_API_KEY, "Content-Type": "application/json"}
    body = {"coordinates": coords, "format": "geojson"}
    url = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
    r = requests.post(url, json=body, headers=headers)
    return r.json()

# Get tour spots

def get_tour_spots(lat, lon):
    url = f"https://apis.data.go.kr/B551011/KorService1/locationBasedList1?MobileOS=ETC&MobileApp=app&mapX={lon}&mapY={lat}&radius=5000&listYN=Y&arrange=E&numOfRows=10&pageNo=1&_type=json&serviceKey={TOURAPI_KEY}"
    r = requests.get(url)
    return r.json()

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/route", methods=["POST"])
def route():
    data = request.get_json()
    start_address = data.get("start")
    end_address = data.get("end")

    try:
        start = geocode_address(start_address)
        end = geocode_address(end_address)
        coast = find_nearest_coast(*start)
        coords = [[start[1], start[0]], [coast[1], coast[0]], [end[1], end[0]]]
        route_geojson = get_route(coords)
        tour_data = get_tour_spots(*end)
        return jsonify({"route": route_geojson, "tours": tour_data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=True)
