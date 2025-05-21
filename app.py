import os
import json
import requests
import pandas as pd
import geopandas as gpd
import numpy as np
from flask import Flask, request, jsonify, render_template
from shapely.geometry import Point
from shapely.ops import unary_union
from scipy.spatial import KDTree
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)

# API Keys
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
NAVER_API_KEY_ID = os.getenv("NAVER_API_KEY_ID")
NAVER_API_KEY_SECRET = os.getenv("NAVER_API_KEY_SECRET")

# Load data
road_points = pd.read_csv("road_endpoints_reduced.csv", low_memory=False)
coastline = gpd.read_file("coastal_route_result.geojson").to_crs(epsg=4326)

# Build KDTree
coast_coords = np.array([(pt.x, pt.y) for geom in coastline.geometry for pt in geom.coords])
coast_tree = KDTree(coast_coords)

road_coords = np.array(list(zip(road_points["x"], road_points["y"])))
road_tree = KDTree(road_coords)


def geocode_google(address):
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    res = requests.get(url, params={"address": address, "key": GOOGLE_API_KEY})
    try:
        location = res.json()["results"][0]["geometry"]["location"]
        print("📍 주소 변환 성공:", address, "→", location)
        return location["lat"], location["lng"]
    except:
        print("❌ 주소 변환 실패:", address)
        return None


def find_coastal_waypoints():
    near_points = []
    for idx, row in road_points.iterrows():
        pt = np.array([row["x"], row["y"]])
        dist = coast_tree.query(pt)[0]
        if dist < 3 / 111:  # 3km 내 (1° = 111km)
            near_points.append(row)
    return pd.DataFrame(near_points)


def pick_directional_waypoint(start, end, candidates):
    start_lat, start_lon = start
    end_lat, end_lon = end
    use_lat = abs(start_lat - end_lat) > abs(start_lon - end_lon)

    if use_lat:
        candidates["dist"] = (candidates["y"] - start_lat).abs()
    else:
        candidates["dist"] = (candidates["x"] - start_lon).abs()

    sorted_candidates = candidates.sort_values("dist")
    for _, row in sorted_candidates.iterrows():
        return row["y"], row["x"]  # 위도, 경도
    return None


def get_naver_route(start, waypoint, end):
    url = "https://naveropenapi.apigw.ntruss.com/map-direction-15/v1/driving"
    headers = {
        "X-NCP-APIGW-API-KEY-ID": NAVER_API_KEY_ID,
        "X-NCP-APIGW-API-KEY": NAVER_API_KEY_SECRET,
    }
    params = {
        "start": f"{start[1]},{start[0]}",
        "goal": f"{end[1]},{end[0]}",
        "waypoints": f"{waypoint[1]},{waypoint[0]}",
        "option": "traoptimal",
    }
    res = requests.get(url, headers=headers, params=params)
    print("📡 NAVER 응답코드:", res.status_code)
    if res.status_code == 200:
        return res.json()
    else:
        return {"error": res.text}


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

        candidates = find_coastal_waypoints()
        waypoint = pick_directional_waypoint(start, end, candidates)
        if not waypoint:
            return jsonify({"error": "❌ 웨이포인트 없음"}), 400

        route_data = get_naver_route(start, waypoint, end)
        if "route" in route_data:
            return jsonify(route_data)
        else:
            return jsonify({"error": f"❌ 경로 요청 실패: {route_data.get('error')}"}), 500

    except Exception as e:
        print("❌ 서버 오류:", str(e))
        return jsonify({"error": f"❌ 서버 내부 오류: {str(e)}"}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print("✅ API 키 로딩 확인")
    print("GOOGLE_API_KEY:", bool(GOOGLE_API_KEY))
    print("NAVER_API_KEY_ID:", bool(NAVER_API_KEY_ID))
    print("NAVER_API_KEY_SECRET:", bool(NAVER_API_KEY_SECRET))
    app.run(host="0.0.0.0", port=port)
