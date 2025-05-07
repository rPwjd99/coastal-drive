from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import os
import requests
import geopandas as gpd
from shapely.geometry import Point
import json

app = Flask(__name__)
CORS(app)

# API 키는 Render 환경변수로 설정
VWORLD_KEY = os.environ.get("VWORLD_KEY")
ORS_KEY = os.environ.get("ORS_KEY")
TOURAPI_KEY = os.environ.get("TOURAPI_KEY")

# 해안선 데이터 로드
COASTLINE_PATH = "해안선.geojson"
coastline = gpd.read_file(COASTLINE_PATH)

# 주소 → 좌표 변환 (도로명 주소만 지원)
def geocode(address):
    url = f"https://api.vworld.kr/req/address"
    params = {
        "service": "address",
        "request": "getcoord",
        "format": "json",
        "type": "road",
        "address": address,
        "key": VWORLD_KEY
    }
    response = requests.get(url, params=params)
    data = response.json()
    try:
        point = data['response']['result']['point']
        return float(point['y']), float(point['x'])  # lat, lon
    except:
        return None

# 좌표 간 거리 계산
def haversine(lat1, lon1, lat2, lon2):
    from math import radians, cos, sin, asin, sqrt
    R = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
    return R * 2 * asin(sqrt(a))

# 해안선 중 중간 지점 찾기 (위도/경도 기반)
def find_midpoint(start, end):
    lat1, lon1 = start
    lat2, lon2 = end
    mid_lat = (lat1 + lat2) / 2
    mid_lon = (lon1 + lon2) / 2
    return mid_lat, mid_lon

# 해안과 가장 가까운 점 반환
def nearest_coast_point(mid_lat, mid_lon):
    coast_pts = coastline.geometry.centroid
    coast_coords = [(pt.y, pt.x) for pt in coast_pts]
    distances = [haversine(mid_lat, mid_lon, y, x) for y, x in coast_coords]
    return coast_coords[distances.index(min(distances))]

# 경로 계산
def get_route(coords):
    headers = {"Authorization": ORS_KEY}
    body = {
        "coordinates": coords,
        "format": "geojson"
    }
    response = requests.post(
        "https://api.openrouteservice.org/v2/directions/driving-car/geojson",
        headers=headers, json=body
    )
    return response.json()

# 관광지 조회
def get_tourspots(lat, lon):
    url = "https://apis.data.go.kr/B551011/KorService1/locationBasedList1"
    params = {
        "MobileOS": "ETC",
        "MobileApp": "CoastalDriveApp",
        "serviceKey": TOURAPI_KEY,
        "mapX": lon,
        "mapY": lat,
        "radius": 5000,
        "listYN": "Y",
        "arrange": "A",
        "numOfRows": 10,
        "pageNo": 1,
        "_type": "json"
    }
    response = requests.get(url, params=params)
    items = response.json().get("response", {}).get("body", {}).get("items", {}).get("item", [])
    results = []
    for item in items:
        results.append({
            "title": item.get("title"),
            "addr": item.get("addr1"),
            "image": item.get("firstimage", ""),
            "lat": item.get("mapy"),
            "lon": item.get("mapx")
        })
    return results

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/route', methods=['POST'])
def route():
    data = request.get_json()
    start_addr = data.get("start")
    end_addr = data.get("end")
    
    start = geocode(start_addr)
    end = geocode(end_addr)
    
    if not start or not end:
        return jsonify({"error": "주소 해석 실패"}), 400

    mid_lat, mid_lon = find_midpoint(start, end)
    coast = nearest_coast_point(mid_lat, mid_lon)

    coords = [[start[1], start[0]], [coast[1], coast[0]], [end[1], end[0]]]
    route_geojson = get_route(coords)
    tourspots = get_tourspots(end[0], end[1])

    return jsonify({
        "route": route_geojson,
        "spots": tourspots
    })

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
