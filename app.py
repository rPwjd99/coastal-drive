import json
import math
import random
import requests
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from shapely.geometry import shape, Point
import geopandas as gpd

app = Flask(__name__)
CORS(app)

# API KEY 직접 삽입
VWORLD_API_KEY = "9E77283D-954A-3077-B7C8-9BD5ADB33255"
ORS_API_KEY = "5b3ce3597851110001cf62486d543846e80049df9c7a9e10ecef2953"
TOURAPI_KEY = "e1tU33wjMx2nynKjH8yDBm/S4YNne6B8mpCOWtzMH9TSONF71XG/xAwPqyv1fANpgeOvbPY+Le+gM6cYCnWV8w=="

# 해안선 GeoJSON 로드
coastline = gpd.read_file("해안선_국가기본도.geojson")

def geocode(address):
    url = f"https://api.vworld.kr/req/address?service=address&request=getcoord&format=json&type=both&address={address}&key={VWORLD_API_KEY}"
    response = requests.get(url)
    try:
        coords = response.json()['response']['result']['point']
        return float(coords['y']), float(coords['x'])  # lat, lon
    except:
        return None

def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    d_lat, d_lon = math.radians(lat2-lat1), math.radians(lon2-lon1)
    a = math.sin(d_lat/2)**2 + math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(d_lon/2)**2
    return R * 2 * math.asin(math.sqrt(a))

def find_similar_coast_point(lat, lon, mode="lat", buffer_km=5):
    if mode == "lat":
        candidates = coastline[abs(coastline.geometry.y - lat) < 0.05]
    else:
        candidates = coastline[abs(coastline.geometry.x - lon) < 0.05]
    if candidates.empty:
        return None

    # 거리 기반 최적 후보 선택
    candidates["dist"] = candidates.geometry.apply(lambda p: haversine(lat, lon, p.y, p.x))
    best = candidates.sort_values("dist").iloc[0].geometry
    return best.y, best.x  # lat, lon

def route_by_ors(coords):
    headers = {"Authorization": ORS_API_KEY, "Content-Type": "application/json"}
    body = {
        "coordinates": coords,
        "format": "geojson"
    }
    try:
        r = requests.post("https://api.openrouteservice.org/v2/directions/driving-car/geojson", json=body, headers=headers)
        if r.status_code == 200:
            return r.json()
        return None
    except:
        return None

def search_tourspots(lat, lon):
    url = f"http://apis.data.go.kr/B551011/KorService1/locationBasedList1"
    params = {
        "MobileOS": "ETC",
        "MobileApp": "AppTest",
        "mapX": lon,
        "mapY": lat,
        "radius": 5000,
        "arrange": "A",
        "numOfRows": 10,
        "pageNo": 1,
        "serviceKey": TOURAPI_KEY,
        "_type": "json"
    }
    r = requests.get(url, params=params)
    try:
        items = r.json()["response"]["body"]["items"]["item"]
        return [{
            "title": i["title"],
            "addr": i.get("addr1", ""),
            "mapx": float(i["mapx"]),
            "mapy": float(i["mapy"]),
            "image": i.get("firstimage", "")
        } for i in items]
    except:
        return []

@app.route("/")
def root():
    return send_file("index.html")

@app.route("/api/route", methods=["POST"])
def get_route():
    data = request.json
    start_addr = data["start"]
    end_addr = data["end"]

    start = geocode(start_addr)
    end = geocode(end_addr)
    if not start or not end:
        return jsonify({"error": "주소 좌표 변환 실패"}), 400

    # 위도 기반, 경도 기반 해안점 비교
    lat_coast = find_similar_coast_point(*start, mode="lat")
    lon_coast = find_similar_coast_point(*start, mode="lon")

    d_lat = haversine(*start, *lat_coast) if lat_coast else float('inf')
    d_lon = haversine(*start, *lon_coast) if lon_coast else float('inf')
    coast = lat_coast if d_lat < d_lon else lon_coast

    # 해안점 유효성 테스트
    base_route = route_by_ors([[start[1], start[0]], [coast[1], coast[0]], [end[1], end[0]]])
    if base_route:
        tour = search_tourspots(*end)
        return jsonify({"route": base_route, "tourspots": tour})

    # 실패 시 해안점 주변 5km 버퍼 내 무작위 후보
    for _ in range(5):
        jitter = lambda: random.uniform(-0.05, 0.05)
        trial = (coast[0] + jitter(), coast[1] + jitter())
        retry_route = route_by_ors([[start[1], start[0]], [trial[1], trial[0]], [end[1], end[0]]])
        if retry_route:
            tour = search_tourspots(*end)
            return jsonify({"route": retry_route, "tourspots": tour})

    return jsonify({"error": "경로 생성 실패"}), 500

if __name__ == "__main__":
    app.run(debug=True)
