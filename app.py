from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import requests
import json
import geopandas as gpd
from shapely.geometry import Point
import math

app = Flask(__name__)
CORS(app)

# --- API KEYS ---
VWORLD_API_KEY = "9E77283D-954A-3077-B7C8-9BD5ADB33255"
ORS_API_KEY = "5b3ce3597851110001cf62486d543846e80049df9c7a9e10ecef2953"
TOURAPI_KEY = "e1tU33wjMx2nynKjH8yDBm/S4YNne6B8mpCOWtzMH9TSONF71XG/xAwPqyv1fANpgeOvbPY+Le+gM6cYCnWV8w=="

# --- Load Coastline ---
coastline = gpd.read_file("해안선_국가기본도.geojson")
coastline = coastline.to_crs(epsg=4326)
coast_coords = coastline.geometry.apply(lambda g: list(g.coords) if hasattr(g, "coords") else []).explode().tolist()

# --- Utilities ---
def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def geocode_vworld(address):
    url = f"https://api.vworld.kr/req/address?service=address&request=getcoord&format=json&type=both&address={address}&key={VWORLD_API_KEY}"
    r = requests.get(url)
    res = r.json()
    try:
        point = res['response']['result'][0]['point']
        return float(point['y']), float(point['x'])
    except:
        return None

def get_nearest_coast(lat, lon, mode='lat'):
    sorted_points = sorted(coast_coords, key=lambda p: abs(p[1] - lat) if mode == 'lat' else abs(p[0] - lon))
    return sorted_points[0][1], sorted_points[0][0]  # lat, lon

def is_route_possible(coords):
    url = "https://api.openrouteservice.org/v2/directions/driving-car"
    headers = {"Authorization": ORS_API_KEY, "Content-Type": "application/json"}
    body = {"coordinates": coords}
    r = requests.post(url, json=body, headers=headers)
    return r.status_code == 200, r.json() if r.status_code == 200 else None

@app.route("/")
def root():
    return render_template("index.html")

@app.route("/api/route", methods=["POST"])
def route():
    data = request.json
    start_addr, end_addr = data['start'], data['end']
    start = geocode_vworld(start_addr)
    end = geocode_vworld(end_addr)
    if not start or not end:
        return jsonify({"error": "주소 변환 실패"}), 400

    lat_way, lon_way = get_nearest_coast(start[0], start[1], 'lat')
    lat_dist = haversine(start[0], start[1], lat_way, lon_way)
    lon_way2, lat_way2 = get_nearest_coast(start[0], start[1], 'lon')
    lon_dist = haversine(start[0], start[1], lat_way2, lon_way2)

    waypoint = (lat_way, lon_way) if lat_dist < lon_dist else (lat_way2, lon_way2)

    coords = [[start[1], start[0]], [waypoint[1], waypoint[0]], [end[1], end[0]]]
    success, route_data = is_route_possible(coords)
    if not success:
        return jsonify({"error": "경로 계산 실패"}), 500

    return jsonify({"route": route_data})

@app.route("/api/tourspot", methods=["POST"])
def tourspot():
    data = request.json
    lat, lon = data['lat'], data['lon']
    url = f"https://apis.data.go.kr/B551011/KorService1/locationBasedList1?MobileOS=ETC&MobileApp=test&serviceKey={TOURAPI_KEY}&mapX={lon}&mapY={lat}&radius=5000&_type=json"
    r = requests.get(url)
    items = []
    try:
        res = r.json()
        for item in res['response']['body']['items']['item']:
            items.append({
                "title": item.get("title"),
                "addr": item.get("addr1"),
                "image": item.get("firstimage")
            })
    except:
        pass
    return jsonify(items)

if __name__ == '__main__':
    app.run(debug=True)