import os
import json
import requests
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point
from flask import Flask, request, jsonify, render_template
from math import radians, cos, sin, asin, sqrt
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)

# API 키 불러오기
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")

# 해안선 GeoJSON 로드
COASTLINE_PATH = "coastal_route_result.geojson"
coastline = gpd.read_file(COASTLINE_PATH)
coastline = coastline.to_crs(epsg=4326)
coastline["centroid"] = coastline.geometry.representative_point()

# 도로 끝점 CSV 로드
road_points = pd.read_csv("road_endpoints_reduced.csv")

# 해버사인 거리 계산 함수
def haversine(lat1, lon1, lat2, lon2):
    R = 6371  # km
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2)**2
    return 2 * R * asin(sqrt(a))

# 경로 계산 함수
def get_naver_route(start, waypoint, end):
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
    if res.status_code != 200:
        print("❌ 네이버 경로 API 실패:", res.status_code)
        return None
    return res.json()

# 주소 → 좌표 변환 (Google)
def geocode_google(address):
    GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
    url = f"https://maps.googleapis.com/maps/api/geocode/json"
    params = {"address": address, "key": GOOGLE_API_KEY}
    res = requests.get(url, params=params)
    if res.status_code != 200:
        print("❌ 구글 주소 변환 실패:", res.status_code)
        return None
    try:
        location = res.json()["results"][0]["geometry"]["location"]
        return location["lat"], location["lng"]
    except:
        return None

# 가장 가까운 도로점 선택
def find_nearest_road_point(lat, lon):
    road_points["distance"] = road_points.apply(lambda row:
        haversine(lat, lon, row["y"], row["x"]), axis=1)
    nearest = road_points.sort_values("distance").iloc[0]
    return nearest["y"], nearest["x"]

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/route", methods=["POST"])
def route():
    data = request.get_json()
    start_addr = data.get("start")
    end_addr = data.get("end")

    start = geocode_google(start_addr)
    end = geocode_google(end_addr)
    if not start or not end:
        return jsonify({"error": "❌ 주소 → 좌표 변환 실패"}), 400

    # 해안선 중심점 찾기
    coastline["dist"] = coastline.centroid.distance(Point(start[1], start[0]))
    coast_point = coastline.sort_values("dist").iloc[0].centroid
    coast_lat, coast_lon = coast_point.y, coast_point.x

    # 중심점 기준 가장 가까운 도로 좌표 찾기
    waypoint = find_nearest_road_point(coast_lat, coast_lon)

    # 경로 계산
    route_data = get_naver_route(start, waypoint, end)
    if not route_data:
        return jsonify({"error": "❌ 경로 탐색 실패"}), 500

    return jsonify(route_data)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
