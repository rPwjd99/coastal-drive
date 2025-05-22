from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import geopandas as gpd
import pandas as pd
import numpy as np
from shapely.geometry import Point, LineString, MultiLineString
from scipy.spatial import KDTree
import requests
import json
import os

app = Flask(__name__)
CORS(app)

# === 설정 ===
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")  # .env 파일에 GOOGLE_API_KEY 설정 권장
GEOJSON_PATH = "coastal_route_result.geojson"
ROAD_CSV_PATH = "road_endpoints_reduced.csv"

# === 파일 불러오기 ===
print("📦 도로 끝점 로딩 중...")
road_points = pd.read_csv(ROAD_CSV_PATH, low_memory=False)
road_points["x"] = pd.to_numeric(road_points["x"], errors="coerce")
road_points["y"] = pd.to_numeric(road_points["y"], errors="coerce")
road_points = road_points.dropna(subset=["x", "y"])

print("🌊 해안선 GeoJSON 로딩 중...")
coastline = gpd.read_file(GEOJSON_PATH)
coastline = coastline.to_crs(epsg=4326)

# === KDTree 생성 ===
coast_coords = []
for geom in coastline.geometry:
    if isinstance(geom, LineString):
        coast_coords.extend(list(geom.coords))
    elif isinstance(geom, MultiLineString):
        for part in geom.geoms:
            coast_coords.extend(list(part.coords))
coast_coords = np.array(coast_coords)
coast_tree = KDTree(coast_coords)

# === 함수: 주소 → 좌표 변환 ===
def geocode_google(address):
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {"address": address, "key": GOOGLE_API_KEY}
    res = requests.get(url, params=params)
    if res.status_code != 200:
        return None
    data = res.json()
    if data["status"] != "OK" or not data["results"]:
        return None
    location = data["results"][0]["geometry"]["location"]
    return [location["lng"], location["lat"]]  # [x, y]

# === 함수: 웨이포인트 탐색 (도로 끝점 중 해안과 가까운 점) ===
def find_waypoint_near_coast(start, end, max_distance_km=5):
    min_dist = float("inf")
    closest_point = None
    start_x, start_y = start
    end_x, end_y = end

    for _, row in road_points.iterrows():
        x, y = row["x"], row["y"]
        # 해안선 거리 확인
        dist, _ = coast_tree.query([x, y])
        if dist < max_distance_km / 111:  # 약 5km
            # 방향성: 시작점 또는 도착점과 위도·경도 차이가 적은 것 우선
            if abs(y - start_y) < abs(y - end_y):
                if dist < min_dist:
                    min_dist = dist
                    closest_point = [x, y]
    return closest_point

# === 라우팅 ===
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

        # 경로 생성 (단순 LineString)
        coords = [start, waypoint, end]
        geojson = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": coords
                },
                "properties": {}
            }]
        }

        return jsonify(geojson)
    except Exception as e:
        return jsonify({"error": f"❌ 서버 오류: {str(e)}"}), 500

if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=10000)
