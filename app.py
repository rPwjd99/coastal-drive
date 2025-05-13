import os
import json
import requests
import geopandas as gpd
from flask import Flask, request, jsonify, render_template
from shapely.geometry import Point
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")
COASTLINE_PATH = "coastal_route_result.geojson"

# 1. 주소를 위경도로 변환 (Google)
def geocode_google(address):
    url = f"https://maps.googleapis.com/maps/api/geocode/json"
    params = {"address": address, "key": GOOGLE_API_KEY}
    res = requests.get(url, params=params)
    if res.status_code != 200:
        print("❌ 구글 주소 변환 요청 실패", res.status_code)
        return None
    try:
        data = res.json()
        location = data["results"][0]["geometry"]["location"]
        return location["lat"], location["lng"]
    except Exception as e:
        print("❌ 구글 주소 변환 실패:", e)
        return None

# 2. 해안 경유지 탐색
coastline = gpd.read_file(COASTLINE_PATH)
coastline["centroid"] = coastline.geometry.representative_point()

def find_nearest_waypoint(start_lat, start_lng, end_lat, end_lng):
    coastline["dist"] = coastline.centroid.distance(Point(start_lng, start_lat))
    nearest_point = coastline.sort_values("dist").iloc[0].centroid
    return nearest_point.y, nearest_point.x

# 3. 경로 계산 (Naver)
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
    try:
        return res.json()
    except Exception as e:
        print("❌ 네이버 응답 파싱 실패:", e)
        return None

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/route", methods=["POST"])
def route():
    data = request.get_json()
    start_addr = data.get("start")
    end_addr = data.get("end")

    print("📍 출발지:", start_addr)
    print("📍 목적지:", end_addr)

    start = geocode_google(start_addr)
    end = geocode_google(end_addr)

    if not start or not end:
        return jsonify({"error": "❌ 주소 → 좌표 변환 실패"}), 400

    waypoint = find_nearest_waypoint(start[0], start[1], end[0], end[1])

    if not waypoint:
        return jsonify({"error": "❌ 해안 경유지 탐색 실패"}), 404

    route_data = get_naver_route(start, waypoint, end)
    if not route_data:
        return jsonify({"error": "❌ 경로 탐색 실패"}), 500

    return jsonify(route_data)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
