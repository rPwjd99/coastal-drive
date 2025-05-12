import os
import json
import requests
import geopandas as gpd
from flask import Flask, request, jsonify, render_template
from shapely.geometry import Point
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# 환경변수
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")
TOUR_API_KEY = os.getenv("TOUR_API_KEY")

# 해안선 데이터 로드
coastline = gpd.read_file("coastal_route_result.geojson")
coastline.crs = "EPSG:4326"

def geocode_google(address):
    url = f"https://maps.googleapis.com/maps/api/geocode/json"
    params = {"address": address, "key": GOOGLE_API_KEY}
    res = requests.get(url, params=params).json()
    if res['status'] == "OK":
        loc = res["results"][0]["geometry"]["location"]
        return loc["lat"], loc["lng"]
    return None

def find_nearest_waypoint(start_lat, start_lon, end_lat, end_lon):
    # 거리 계산
    def haversine(p1, p2):
        from math import radians, sin, cos, sqrt, atan2
        R = 6371
        lat1, lon1, lat2, lon2 = map(radians, [p1[0], p1[1], p2[0], p2[1]])
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
        return R * 2 * atan2(sqrt(a), sqrt(1-a))

    # 위도/경도 기준 후보 추출
    lat_sorted = coastline.iloc[(coastline.geometry.y - start_lat).abs().argsort()[:20]]
    lon_sorted = coastline.iloc[(coastline.geometry.x - start_lon).abs().argsort()[:20]]

    # 두 방향 중 가까운 해안선 판단
    lat_dist = haversine((start_lat, start_lon), (lat_sorted.geometry.y.iloc[0], lat_sorted.geometry.x.iloc[0]))
    lon_dist = haversine((start_lat, start_lon), (lon_sorted.geometry.y.iloc[0], lon_sorted.geometry.x.iloc[0]))

    selected = lat_sorted if lat_dist < lon_dist else lon_sorted

    # 가장 먼저 경로 계산 가능한 좌표 반환
    for idx, row in selected.iterrows():
        waypoint = (row.geometry.y, row.geometry.x)
        if test_route(start_lat, start_lon, waypoint[0], waypoint[1]) and test_route(waypoint[0], waypoint[1], end_lat, end_lon):
            return waypoint
    return None

def test_route(lat1, lon1, lat2, lon2):
    url = "https://naveropenapi.apigw.ntruss.com/map-direction/v1/driving"
    headers = {
        "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID,
        "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET,
    }
    params = {
        "start": f"{lon1},{lat1}",
        "goal": f"{lon2},{lat2}",
        "option": "trafast"
    }
    res = requests.get(url, headers=headers, params=params)
    return res.status_code == 200

def get_final_route(start, waypoint, end):
    url = "https://naveropenapi.apigw.ntruss.com/map-direction/v1/driving"
    headers = {
        "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID,
        "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET,
    }
    params = {
        "start": f"{start[1]},{start[0]}",
        "goal": f"{end[1]},{end[0]}",
        "waypoints": f"{waypoint[1]},{waypoint[0]}",
        "option": "trafast"
    }
    res = requests.get(url, headers=headers, params=params)
    return res.json() if res.status_code == 200 else None

def search_tour_spots(lat, lon, radius=5000):
    url = "http://apis.data.go.kr/B551011/KorService1/locationBasedList1"
    params = {
        "MobileOS": "ETC",
        "MobileApp": "CoastalDrive",
        "serviceKey": TOUR_API_KEY,
        "mapX": lon,
        "mapY": lat,
        "radius": radius,
        "_type": "json"
    }
    res = requests.get(url, params=params)
    if res.status_code == 200:
        return res.json().get("response", {}).get("body", {}).get("items", {}).get("item", [])
    return []

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/route", methods=["POST"])
def route():
    data = request.get_json()
    start_addr = data.get("start")
    end_addr = data.get("end")

    start = geocode_google(start_addr)
    end = geocode_google(end_addr)

    if not start or not end:
        return jsonify({"error": "❌ 주소 변환 실패"}), 400

    waypoint = find_nearest_waypoint(start[0], start[1], end[0], end[1])
    if not waypoint:
        return jsonify({"error": "❌ 해안 경유지 탐색 실패"}), 500

    route_data = get_final_route(start, waypoint, end)
    if not route_data:
        return jsonify({"error": "❌ 경로 계산 실패"}), 500

    spots = search_tour_spots(end[0], end[1])

    return jsonify({
        "route": route_data,
        "waypoint": {"lat": waypoint[0], "lon": waypoint[1]},
        "spots": spots
    })

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=10000)
