import os
import pandas as pd
import geopandas as gpd
import numpy as np
from flask import Flask, request, jsonify, render_template
from shapely.geometry import Point, LineString
from scipy.spatial import KDTree
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)

# API Keys
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
NAVER_API_KEY_ID = os.getenv("NAVER_API_KEY_ID")
NAVER_API_KEY_SECRET = os.getenv("NAVER_API_KEY_SECRET")

# Load data
coastline = gpd.read_file("coastal_route_result.geojson")
print("[✅ 해안선 데이터]")
print(coastline.head())
print("CRS:", coastline.crs)

road_points = pd.read_csv("road_endpoints_reduced.csv")
print("[✅ 도로 끝점 데이터]")
print(road_points.head())
print("칼럼:", road_points.columns)

# Prepare KDTree
coast_coords = []
for geom in coastline.geometry:
    if isinstance(geom, LineString):
        coast_coords.extend(list(geom.coords))
coast_tree = KDTree(np.array(coast_coords))

# 거리 검증용 함수
def validate_kdtree_distance(point, coastline):
    pt = Point(point)
    min_distance = float("inf")
    for geom in coastline.geometry:
        if isinstance(geom, LineString):
            dist = geom.distance(pt)
            min_distance = min(min_distance, dist)
    return min_distance

# Google Geocoding
import requests
def geocode_google(address):
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {"address": address, "key": GOOGLE_API_KEY}
    try:
        res = requests.get(url, params=params)
        location = res.json()["results"][0]["geometry"]["location"]
        return location["lat"], location["lng"]
    except Exception as e:
        print("❌ 주소 변환 실패:", address, e)
        return None

# 웨이포인트 탐색
def find_waypoint_near_coast(start, end, radius_km=10):
    start_lat, start_lon = start
    end_lat, end_lon = end
    candidates = []

    for _, row in road_points.iterrows():
        px, py = row["x"], row["y"]
        # KDTree 거리 (위경도 1도 = 약 111km)
        kd_dist, _ = coast_tree.query([px, py])
        if kd_dist < radius_km / 111:  # 약 10km 이내
            # Shapely 거리로 재검증
            shapely_dist = validate_kdtree_distance((px, py), coastline)
            if shapely_dist < radius_km / 111:
                candidates.append(((py, px), shapely_dist))

    if not candidates:
        print("❌ 해안 웨이포인트 없음")
        return None

    candidates.sort(key=lambda x: x[1])
    return candidates[0][0]  # (lat, lon)

# NAVER Directions
def get_naver_route(start, waypoint, end):
    url = "https://naveropenapi.apigw.ntruss.com/map-direction/v1/driving"
    headers = {
        "X-NCP-APIGW-API-KEY-ID": NAVER_API_KEY_ID,
        "X-NCP-APIGW-API-KEY": NAVER_API_KEY_SECRET
    }
    params = {
        "start": f"{start[1]},{start[0]}",
        "goal": f"{end[1]},{end[0]}",
        "waypoints": f"{waypoint[1]},{waypoint[0]}",
        "option": "trafast",
        "format": "json"
    }
    res = requests.get(url, headers=headers, params=params)
    try:
        data = res.json()
        print("✅ NAVER 응답 전체 JSON:", data)
        return data
    except Exception as e:
        print("❌ NAVER 경로 요청 실패:", e)
        return {"error": str(e)}

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/route", methods=["POST"])
def route():
    try:
        data = request.get_json()
        start = geocode_google(data.get("start"))
        end = geocode_google(data.get("end"))
        if not start or not end:
            return jsonify({"error": "❌ 주소 변환 실패"}), 400

        waypoint = find_waypoint_near_coast(start, end)
        if not waypoint:
            return jsonify({"error": "❌ 해안 웨이포인트 없음"}), 500

        route_data = get_naver_route(start, waypoint, end)
        return jsonify(route_data)
    except Exception as e:
        return jsonify({"error": f"❌ 서버 오류: {str(e)}"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
