import os
import json
import requests
import geopandas as gpd
from shapely.geometry import Point
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")

app = Flask(__name__)
CORS(app)

@app.route("/")
def index():
    return render_template("index.html")

def geocode_address_google(address):
    url = f"https://maps.googleapis.com/maps/api/geocode/json?address={address}&key={GOOGLE_API_KEY}"
    res = requests.get(url)
    if res.status_code == 200:
        results = res.json().get("results")
        if results:
            loc = results[0]["geometry"]["location"]
            return loc["lat"], loc["lng"]
    return None, None

def find_nearest_waypoint(start_lat, start_lng, end_lat, end_lng):
    print("📂 GeoJSON 경유지 파일 불러오는 중...")
    gdf = gpd.read_file("coastal_route_result.geojson")
    gdf["centroid"] = gdf.geometry.centroid

    def direction_score(row):
        lat_diff = abs(row.centroid.y - start_lat)
        lng_diff = abs(row.centroid.x - start_lng)
        return lat_diff + lng_diff

    gdf["score"] = gdf.apply(direction_score, axis=1)
    waypoint = gdf.loc[gdf["score"].idxmin()].centroid
    print(f"📍 선택된 waypoint: {waypoint.y}, {waypoint.x}")
    return waypoint.y, waypoint.x

def get_naver_directions(start, waypoint, end):
    url = "https://naveropenapi.apigw.ntruss.com/map-direction/v1/driving"
    coords = f"{start[1]},{start[0]};{waypoint[1]},{waypoint[0]};{end[1]},{end[0]}"
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
    if res.status_code == 200:
        print("✅ 경로 탐색 성공 (Naver Directions)")
        return res.json()
    else:
        print("❌ 경로 탐색 실패", res.text)
        return None

@app.route("/route", methods=["POST"])
def route():
    try:
        data = request.get_json()
        start_address = data["start"]
        end_address = data["end"]

        print(f"📍 출발지 주소: {start_address}")
        print(f"📍 목적지 주소: {end_address}")

        start_lat, start_lng = geocode_address_google(start_address)
        end_lat, end_lng = geocode_address_google(end_address)

        if not all([start_lat, start_lng, end_lat, end_lng]):
            return jsonify({"error": "❌ 주소 → 좌표 변환 실패"}), 400

        print(f"✅ 출발 좌표: {start_lat}, {start_lng}")
        print(f"✅ 도착 좌표: {end_lat}, {end_lng}")

        waypoint_lat, waypoint_lng = find_nearest_waypoint(start_lat, start_lng, end_lat, end_lng)

        route_data = get_naver_directions(
            start=(start_lat, start_lng),
            waypoint=(waypoint_lat, waypoint_lng),
            end=(end_lat, end_lng)
        )

        if not route_data:
            return jsonify({"error": "❌ 경로 탐색 실패"}), 500

        return jsonify(route_data)

    except Exception as e:
        print("❌ 예외 발생:", e)
        return jsonify({"error": f"❌ 서버 오류: {str(e)}"}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
