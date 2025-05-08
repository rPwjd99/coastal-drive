from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import os
import requests
from dotenv import load_dotenv
import geopandas as gpd
from shapely.geometry import Point
from math import radians, cos, sin, asin, sqrt

# Load environment variables from .env
load_dotenv()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
ORS_API_KEY = os.getenv("ORS_API_KEY")
TOURAPI_KEY = os.getenv("TOURAPI_KEY")

# Load coastline GeoJSON
COASTLINE_PATH = "coastal-drive/coastal_route_result.geojson"
coastline_gdf = gpd.read_file(COASTLINE_PATH)
coastline_gdf = coastline_gdf.to_crs(epsg=4326)

app = Flask(__name__, template_folder='templates', static_folder='static')
CORS(app)

def geocode(address):
    url = f"https://maps.googleapis.com/maps/api/geocode/json?address={address}&key={GOOGLE_API_KEY}"
    resp = requests.get(url)
    if resp.status_code != 200:
        return None
    data = resp.json()
    if data.get("status") != "OK":
        return None
    loc = data["results"][0]["geometry"]["location"]
    return loc["lat"], loc["lng"]

def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2)**2
    return 2 * R * asin(sqrt(a))

def find_nearest_coast(lat, lon):
    min_dist = float("inf")
    nearest = None
    for _, row in coastline_gdf.iterrows():
        geom = row.geometry
        if geom.geom_type == "Point":
            c_lat, c_lon = geom.y, geom.x
            dist = haversine(lat, lon, c_lat, c_lon)
            if dist < min_dist:
                min_dist = dist
                nearest = (c_lat, c_lon)
    return nearest

def get_route(coords):
    url = "https://api.openrouteservice.org/v2/directions/driving-car"
    headers = {
        "Authorization": ORS_API_KEY,
        "Content-Type": "application/json"
    }
    data = {
        "coordinates": coords
    }
    resp = requests.post(url, headers=headers, json=data)
    if resp.status_code != 200:
        return None
    return resp.json()

def get_tour_spots(lat, lon):
    url = f"https://apis.data.go.kr/B551011/KorService1/locationBasedList1?ServiceKey={TOURAPI_KEY}&mapX={lon}&mapY={lat}&radius=5000&MobileOS=ETC&MobileApp=test&_type=json"
    resp = requests.get(url)
    if resp.status_code != 200:
        return []
    data = resp.json()
    try:
        items = data['response']['body']['items']['item']
        return items
    except:
        return []

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/api/route', methods=['POST'])
def route():
    data = request.get_json()
    start = data.get('start')
    end = data.get('end')

    start_coord = geocode(start)
    end_coord = geocode(end)

    if not start_coord or not end_coord:
        return jsonify({"error": "주소 변환 실패"}), 400

    coast_coord = find_nearest_coast(*start_coord)

    route_data = get_route([
        [start_coord[1], start_coord[0]],
        [coast_coord[1], coast_coord[0]],
        [end_coord[1], end_coord[0]]
    ])

    if route_data is None:
        return jsonify({"error": "경로 계산 실패"}), 500

    spots = get_tour_spots(*end_coord)

    return jsonify({
        "route": route_data,
        "tour_spots": spots
    })

if __name__ == '__main__':
    app.run(port=10000, debug=True)
