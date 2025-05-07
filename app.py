from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import requests
import json
import geopandas as gpd
from shapely.geometry import Point
import math

app = Flask(__name__)
CORS(app)

# NAVER API Key
NAVER_CLIENT_ID = "vsdzf1f4n5"
NAVER_CLIENT_SECRET = "0gzctO51PUTVv0gUZU025JYNHPTmVzLS9sGbfYBM"

# OpenRouteService API Key
ORS_API_KEY = "5b3ce3597851110001cf62486d543846e80049df9c7a9e10ecef2953"

# TourAPI Key
TOURAPI_KEY = "e1tU33wjMx2nynKjH8yDBm/S4YNne6B8mpCOWtzMH9TSONF71XG/xAwPqyv1fANpgeOvbPY+Le+gM6cYCnWV8w=="

# Load coastline GeoJSON
coastline = gpd.read_file("해안선.geojson")

def get_coords_from_address(address):
    url = f"https://naveropenapi.apigw.ntruss.com/map-geocode/v2/geocode"
    headers = {
        "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID,
        "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET
    }
    params = {"query": address}
    response = requests.get(url, headers=headers, params=params)
    data = response.json()
    if data["addresses"]:
        x = float(data["addresses"][0]["x"])
        y = float(data["addresses"][0]["y"])
        return (y, x)
    else:
        return None

def haversine(coord1, coord2):
    R = 6371
    lat1, lon1 = coord1
    lat2, lon2 = coord2
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def find_nearest_coast_point(coord, direction='lat'):
    coast_coords = [(pt.y, pt.x) for pt in coastline.geometry.centroid]
    if direction == 'lat':
        candidates = sorted(coast_coords, key=lambda c: abs(c[0] - coord[0]))
    else:
        candidates = sorted(coast_coords, key=lambda c: abs(c[1] - coord[1]))
    for cand in candidates:
        if is_road_accessible(cand):
            return cand
    return coord  # fallback

def is_road_accessible(point):
    coords = [[point[1], point[0]], [point[1] + 0.01, point[0] + 0.01]]
    url = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
    headers = {"Authorization": ORS_API_KEY, "Content-Type": "application/json"}
    payload = {"coordinates": coords}
    try:
        response = requests.post(url, headers=headers, json=payload)
        return response.status_code == 200
    except:
        return False

def get_route(start, via, end):
    url = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
    headers = {"Authorization": ORS_API_KEY, "Content-Type": "application/json"}
    payload = {"coordinates": [[start[1], start[0]], [via[1], via[0]], [end[1], end[0]]]}
    response = requests.post(url, headers=headers, json=payload)
    return response.json()

def get_tourist_spots(lat, lon):
    url = f"http://apis.data.go.kr/B551011/KorService1/locationBasedList1"
    params = {
        "MobileOS": "ETC",
        "MobileApp": "CoastalDrive",
        "mapX": lon,
        "mapY": lat,
        "radius": 5000,
        "ServiceKey": TOURAPI_KEY,
        "_type": "json"
    }
    response = requests.get(url, params=params)
    return response.json()

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/api/route", methods=["POST"])
def api_route():
    data = request.json
    start_addr = data["start"]
    end_addr = data["end"]

    start = get_coords_from_address(start_addr)
    end = get_coords_from_address(end_addr)
    if not start or not end:
        return jsonify({"error": "주소 해석 실패"}), 400

    lat_based = find_nearest_coast_point(start, 'lat')
    lon_based = find_nearest_coast_point(start, 'lon')
    lat_dist = haversine(start, lat_based)
    lon_dist = haversine(start, lon_based)
    via = lat_based if lat_dist < lon_dist else lon_based

    route = get_route(start, via, end)
    spots = get_tourist_spots(end[0], end[1])
    return jsonify({"route": route, "spots": spots})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
