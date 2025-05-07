from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import requests
import geopandas as gpd
from shapely.geometry import Point
import math

app = Flask(__name__)
CORS(app)

# API 키 직접 삽입
VWORLD_KEY = "9E77283D-954A-3077-B7C8-9BD5ADB33255"
ORS_KEY = "5b3ce3597851110001cf62486d543846e80049df9c7a9e10ecef2953"
TOURAPI_KEY = "e1tU33wjMx2nynKjH8yDBm/S4YNne6B8mpCOWtzMH9TSONF71XG/xAwPqyv1fANpgeOvbPY+Le+gM6cYCnWV8w=="

# 해안선 불러오기
coastline = gpd.read_file("해안선.geojson")
coastline = coastline.to_crs(epsg=4326)

def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    d_lat, d_lon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(d_lat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def get_coordinates_from_vworld(address):
    # 주소 검색
    addr_url = "https://api.vworld.kr/req/address"
    addr_params = {
        "service": "address",
        "request": "getcoord",
        "format": "json",
        "type": "both",
        "address": address,
        "key": VWORLD_KEY,
    }
    res = requests.get(addr_url, params=addr_params)
    try:
        data = res.json()
        items = data["response"]["result"]
        if items:
            point = items[0]["point"]
            return float(point["y"]), float(point["x"])
    except:
        pass

    # POI fallback
    poi_url = "https://api.vworld.kr/req/search"
    poi_params = {
        "service": "search",
        "request": "search",
        "format": "json",
        "query": address,
        "key": VWORLD_KEY,
    }
    res = requests.get(poi_url, params=poi_params)
    try:
        data = res.json()
        items = data["response"]["result"]
        if items:
            point = items[0]["point"]
            return float(point["y"]), float(point["x"])
    except:
        return None
    return None

def find_nearest_coast_point(lat, lon):
    point = Point(lon, lat)
    coastline_points = coastline.geometry.apply(lambda geom: geom.interpolate(0.5, normalized=True) if not geom.is_empty else None)
    coastline['center'] = coastline_points
    coastline['distance'] = coastline['center'].apply(lambda p: haversine(lat, lon, p.y, p.x) if p else float('inf'))
    nearest = coastline.sort_values("distance").iloc[0]
    return nearest['center'].y, nearest['center'].x

def get_route(coords):
    url = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
    headers = {
        "Authorization": ORS_KEY,
        "Content-Type": "application/json"
    }
    body = {
        "coordinates": coords
    }
    res = requests.post(url, headers=headers, json=body)
    return res.json()

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/api/route", methods=["POST"])
def route():
    data = request.get_json()
    start_addr = data["start"]
    end_addr = data["end"]

    start = get_coordinates_from_vworld(start_addr)
    end = get_coordinates_from_vworld(end_addr)
    if not start or not end:
        return jsonify({"error": "주소 변환 실패"}), 400

    coast = find_nearest_coast_point(*start)

    coords = [[start[1], start[0]], [coast[1], coast[0]], [end[1], end[0]]]
    try:
        route_geojson = get_route(coords)
    except:
        return jsonify({"error": "경로 계산 실패"}), 500

    return jsonify(route_geojson)

@app.route("/api/tourspot", methods=["POST"])
def tourspot():
    data = request.get_json()
    lat, lon = data["lat"], data["lon"]
    url = "http://apis.data.go.kr/B551011/KorService1/locationBasedList1"
    params = {
        "serviceKey": TOURAPI_KEY,
        "MobileOS": "ETC",
        "MobileApp": "SeaRoute",
        "mapX": lon,
        "mapY": lat,
        "radius": 5000,
        "arrange": "E",
        "numOfRows": 30,
        "_type": "json"
    }
    res = requests.get(url, params=params)
    try:
        items = res.json()["response"]["body"]["items"]["item"]
        spots = [{
            "title": item["title"],
            "addr": item.get("addr1", ""),
            "lat": item["mapy"],
            "lon": item["mapx"],
            "image": item.get("firstimage", "")
        } for item in items]
        return jsonify(spots)
    except:
        return jsonify([])

if __name__ == "__main__":
    app.run(debug=True)
