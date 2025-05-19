import os
import requests
import pandas as pd
from flask import Flask, request, jsonify, render_template
from math import radians, cos, sin, asin, sqrt
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
NAVER_CLIENT_ID = os.getenv("NAVER_API_KEY_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_API_KEY_SECRET")

# 도로점 데이터 불러오기
road_df = pd.read_csv("road_endpoints_reduced2.csv")

# 해안선 범위 설정
EAST_SEA = (road_df["y"].between(35, 38)) & (road_df["x"].between(128, 131))
WEST_SEA = (road_df["y"].between(34, 38)) & (road_df["x"].between(124, 126))
SOUTH_SEA = (road_df["y"].between(33, 35)) & (road_df["x"].between(126, 129))
COASTAL_FILTER = EAST_SEA | WEST_SEA | SOUTH_SEA
coastal_roads = road_df[COASTAL_FILTER].copy()

def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * R * asin(sqrt(a))

def geocode_google(address):
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    res = requests.get(url, params={"address": address, "key": GOOGLE_API_KEY})
    try:
        location = res.json()["results"][0]["geometry"]["location"]
        print("📍 주소 변환 성공:", address, "→", location, flush=True)
        return location["lat"], location["lng"]
    except Exception:
        print("❌ 주소 변환 실패:", address, flush=True)
        return None

def find_waypoint(start_lat, start_lon, end_lat, end_lon):
    use_lat = abs(start_lat - end_lat) > abs(start_lon - end_lon)
    rounded_lat = round(start_lat, 2)
    rounded_lon = round(start_lon, 2)

    if use_lat:
        filtered = coastal_roads[coastal_roads["y"].round(2) == rounded_lat].copy()
        direction_filter = (end_lon - start_lon) * (filtered["x"] - start_lon) > 0
    else:
        filtered = coastal_roads[coastal_roads["x"].round(2) == rounded_lon].copy()
        direction_filter = (end_lat - start_lat) * (filtered["y"] - start_lat) > 0

    filtered = filtered[direction_filter]
    if filtered.empty:
        print("❌ 방향성 + 거리 조건 만족 도로점 없음", flush=True)
        return None

    filtered["dist"] = filtered.apply(
        lambda row: haversine(start_lat, start_lon, row["y"], row["x"]), axis=1
    )
    selected = filtered.sort_values("dist").iloc[0]
    print("📍 선택된 waypoint:", selected["y"], selected["x"], flush=True)
    return selected["y"], selected["x"]

def get_naver_route(start, waypoint, end):
    url = "https://naveropenapi.apigw.ntruss.com/map-direction/v1/driving"
    headers = {
        "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID,
        "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET
    }
    coords = f"{start[1]},{start[0]}|{waypoint[1]},{waypoint[0]}|{end[1]},{end[0]}"
    params = {"start": f"{start[1]},{start[0]}", "goal": f"{end[1]},{end[0]}", "waypoints": f"{waypoint[1]},{waypoint[0]}", "option": "trafast"}

    res = requests.get(url, headers=headers, params=params)
    print("📡 네이버 응답코드:", res.status_code, flush=True)
    try:
        return res.json(), res.status_code
    except Exception as e:
        return {"error": str(e)}, 500

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/route", methods=["POST"])
def route():
    data = request.get_json()
    start = geocode_google(data.get("start"))
    end = geocode_google(data.get("end"))
    if not start or not end:
        return jsonify({"error": "❌ 주소 변환 실패"}), 400

    waypoint = find_waypoint(start[0], start[1], end[0], end[1])
    if not waypoint:
        return jsonify({"error": "❌ 해안 경유지 탐색 실패"}), 404

    route_data, status = get_naver_route(start, waypoint, end)
    if "error" in route_data:
        return jsonify({"error": f"❌ 경로 요청 실패: {route_data.get('error')}" }), status

    return jsonify(route_data)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
