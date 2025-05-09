import os
import json
import requests
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import geopandas as gpd
from shapely.geometry import Point
from math import radians, cos, sin, asin, sqrt
from dotenv import load_dotenv

load_dotenv()

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
ORS_API_KEY = os.getenv("ORS_API_KEY")
TOURAPI_KEY = os.getenv("TOURAPI_KEY")

app = Flask(__name__)
CORS(app)

COASTLINE_PATH = "coastal_route_result.geojson"
coastline_gdf = gpd.read_file(COASTLINE_PATH)


def geocode_address(address):
    url = f"https://maps.googleapis.com/maps/api/geocode/json?address={address}&key={GOOGLE_API_KEY}"
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        if data['status'] == 'OK':
            location = data['results'][0]['geometry']['location']
            return location['lat'], location['lng']
    return None


def haversine(lon1, lat1, lon2, lat2):
    lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * asin(sqrt(a))
    r = 6371
    return c * r


def find_nearest_coast(lat, lon):
    distances = coastline_gdf.geometry.apply(lambda p: haversine(lon, lat, p.centroid.x, p.centroid.y))
    nearest_idx = distances.idxmin()
    nearest_point = coastline_gdf.geometry.iloc[nearest_idx].centroid
    return nearest_point.y, nearest_point.x


def get_route(coords):
    url = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
    headers = {
        "Authorization": ORS_API_KEY,
        "Content-Type": "application/json"
    }
    body = {
        "coordinates": coords
    }
    response = requests.post(url, headers=headers, json=body)
    if response.status_code == 200:
        return response.json()
    return None


def search_tour_spots(lat, lon):
    url = "https://apis.data.go.kr/B551011/KorService1/locationBasedList1"
    params = {
        "MobileOS": "ETC",
        "MobileApp": "CoastalDrive",
        "serviceKey": TOURAPI_KEY,
        "mapX": lon,
        "mapY": lat,
        "radius": 5000,
        "arrange": "E",
        "numOfRows": 10,
        "_type": "json"
    }
    response = requests.get(url, params=params)
    if response.status_code == 200:
        items = response.json().get("response", {}).get("body", {}).get("items", {}).get("item", [])
        return [
            {
                "title": item.get("title"),
                "addr": item.get("addr1"),
                "mapx": item.get("mapx"),
                "mapy": item.get("mapy"),
                "img": item.get("firstimage")
            } for item in items if item.get("mapx") and item.get("mapy")
        ]
    return []


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/route", methods=["POST"])
def route():
    data = request.get_json()
    start_address = data.get("start")
    end_address = data.get("end")

    start = geocode_address(start_address)
    end = geocode_address(end_address)

    if not start or not end:
        return jsonify({"error": "주소를 찾을 수 없습니다."}), 400

    coast_lat, coast_lon = find_nearest_coast(*start)

    coords = [
        [start[1], start[0]],
        [coast_lon, coast_lat],
        [end[1], end[0]]
    ]
    route_geojson = get_route(coords)
    tourspots = search_tour_spots(end[0], end[1])

    return jsonify({
        "route": route_geojson,
        "tourspots": tourspots
    })


if __name__ == "__main__":
    app.run(debug=True, port=10000)
