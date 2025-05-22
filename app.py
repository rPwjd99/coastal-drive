import os
import json
import pandas as pd
import geopandas as gpd
import numpy as np
from flask import Flask, request, jsonify
from flask_cors import CORS
from shapely.geometry import Point, LineString, MultiLineString
from scipy.spatial import KDTree
import requests
from dotenv import load_dotenv

# 환경변수 불러오기 (.env에서)
load_dotenv()

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
NAVER_API_KEY_ID = os.getenv("NAVER_API_KEY_ID")
NAVER_API_KEY_SECRET = os.getenv("NAVER_API_KEY_SECRET")

# Flask 앱 초기화
app = Flask(__name__)
CORS(app)

# 파일 로딩
coastline = gpd.read_file("coastal_route_result.geojson")
coastline = coastline.to_crs(epsg=4326)

road_points = pd.read_csv("road_endpoints_reduced.csv", low_memory=False)
road_points["x"] = pd.to_numeric(road_points["x"], errors="coerce")
road_points["y"] = pd.to_numeric(road_points["y"], errors="coerce")

# KDTree 위한 해안선 좌표 추출
coast_coords = []
for geom in coastline.geometry:
    if isinstance(geom, LineString):
        coast_coords.extend(list(geom.coords))
    elif isinstance(geom, MultiLineString):
        for part in geom.geoms:
            coast_coords.extend(list(part.coords))
coast_coords = np.array(coast_coords)
coast_tree = KDTree(coast_coords)

# 📍 Google 주소 → 좌표 변환
def geocode_google(address):
    url = f"https://maps.googleapis.com/maps/api/geocode/json?address={address}&key={GOOGLE_API_KEY}"
    res = requests.get(url)
    if res.status_code == 200:
        data = res.json()
        if data["results"]:
            loc = data["results"][0]["geometry"]["location"]
            return [loc["lat"], loc["lng"]]
    return None

# 📍 도로 끝점 중 해안과 가까운 웨이포인트 선택
def find_waypoint_near_coast(start, end, radius_km=5):
    min_diff = float("inf")
    best_point = None
    for _, row in road_points.iterrows():
        pt = np.array([row["x"], row["y"]])
        kd_dist, _ = coast_tree.query(pt)
        if kd_dist > radius_km / 111:  # 1도 ≈ 111km
            continue
        # 목적지와의 방향 유사성 계산
        direction = np.array(end)[::-1] - np.array(start)[::-1]
        to_point = pt - np.array(start)[::-1]
        cos_sim = np.dot(direction, to_point) / (np.linalg.norm(direction) * np.linalg.norm(to_point) + 1e-6)
        if cos_sim > 0.85 and cos_sim < min_diff:
            min_diff = cos_sim
            best_point = pt
    if best_point is not None:
        return [best_point[1], best_point[0]]  # 위도, 경도
    return None

# 📍 NAVER Directions API 호출
def get_naver_route(start, waypoint, end):
    headers = {
        "X-NCP-APIGW-API-KEY-ID": NAVER_API_KEY_ID,
        "X-NCP-APIGW-API-KEY": NAVER_API_KEY_SECRET,
        "Content-Type": "application/json"
    }
    coords = [start[::-1], waypoint[::-1], end[::-1]]  # 경도, 위도 순서로
    body = {
        "start": {"x": coords[0][0], "y": coords[0][1], "name": "출발지"},
        "goal": {"x": coords[2][0], "y": coords[2][1], "name": "도착지"},
        "waypoints": [{"x": coords[1][0], "y": coords[1][1], "name": "해안경유지"}]
    }
    res = requests.post("https://naveropenapi.apigw.ntruss.com/map-direction/v1/driving", headers=headers, data=json.dumps(body))
    if res.status_code == 200:
        data = res.json()
        path = data["route"]["traoptimal"][0]["path"]
        coords = [[lon, lat] for lon, lat in path]
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
        return geojson
    return {"error": "❌ NAVER Directions API 실패"}

# 📍 API 엔드포인트
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
        if "error" in route_data:
            return jsonify(route_data), 500

        return jsonify(route_data)
    except Exception as e:
        return jsonify({"error": f"❌ 서버 오류: {str(e)}"}), 500

# 실행
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
