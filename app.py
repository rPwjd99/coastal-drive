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

# 해안선 데이터 로드 (예외처리 포함)
try:
    coastline = gpd.read_file(COASTLINE_PATH)
    coastline["centroid"] = coastline.geometry.representative_point()
except Exception as e:
    print(f"❌ 해안선 파일 로드 오류: {e}")
    coastline = None

# 1. 주소를 위경도로 변환 (Google)
def geocode_google(address):
    try:
        url = f"https://maps.googleapis.com/maps/api/geocode/json"
        params = {"address": address, "key": GOOGLE_API_KEY}
        res = requests.get(url, params=params, timeout=10)
        res.raise_for_status()
        data = res.json()
        location = data["results"][0]["geometry"]["location"]
        return location["lat"], location["lng"]
    except Exception as e:
        print("❌ 구글 주소 변환 실패:", e)
        return None

# 2. 해안 경유지 탐색
def find_nearest_waypoint(start_lat, start_lng, end_lat, end_lng):
    try:
        if coastline is None or coastline.empty:
            raise ValueError("해안선 데이터가 없습니다.")
        coastline["dist"] = coastline["centroid"].distance(Point(start_lng, start_lat))
        nearest_point = coastline.sort_values("dist").iloc[0]["centroid"]
        return nearest_point.y, nearest_point.x
    except Exception as e:
        print("❌ 해안 경유지 탐색 실패:", e)
        return None

# 3. 경로 계산 (Naver)
def get_naver_route(start, waypoint, end):
    try:
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
        res = requests.get(url, headers=headers, params=params, timeout=10)
        res.raise_for_status()
        return res.json()
    except Exception as e:
        print("❌ 네이버 경로 요청 실패:", e)
        return None

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/route", methods=["POST"])
def route():
    try:
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

    except Exception as e:
        print("❌ 알 수 없는 오류:", e)
        return jsonify({"error": f"❌ 서버 오류: {str(e)}"}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
