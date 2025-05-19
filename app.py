import os
import requests
import pandas as pd
from flask import Flask, request, jsonify, render_template
from math import radians, cos, sin, asin, sqrt
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")

ROAD_CSV_PATH = os.path.join(os.path.dirname(__file__), "road_endpoints_reduced.csv")
road_df = pd.read_csv(ROAD_CSV_PATH, low_memory=False)

# 해안선 범위 필터 (동해/남해/서해)
coastal_df = road_df[
    ((road_df["y"] >= 35) & (road_df["y"] <= 38) & (road_df["x"] >= 128) & (road_df["x"] <= 131)) |  # 동해
    ((road_df["y"] >= 33) & (road_df["y"] <= 35) & (road_df["x"] >= 126) & (road_df["x"] <= 129)) |  # 남해
    ((road_df["y"] >= 34) & (road_df["y"] <= 38) & (road_df["x"] >= 124) & (road_df["x"] <= 126))    # 서해
]

def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2)**2
    return 2 * R * asin(sqrt(a))

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

def find_waypoint(start, end):
    start_lat, start_lon = start
    end_lat, end_lon = end
    use_lat = abs(start_lat - end_lat) > abs(start_lon - end_lon)

    rounded_lat = round(start_lat, 2)
    rounded_lon = round(start_lon, 2)

    if use_lat:
        candidates = coastal_df[coastal_df["y"].round(2) == rounded_lat].copy()
        direction_filter = (end_lon - start_lon) * (candidates["x"] - start_lon) > 0
    else:
        candidates = coastal_df[coastal_df["x"].round(2) == rounded_lon].copy()
        direction_filter = (end_lat - start_lat) * (candidates["y"] - start_lat) > 0

    filtered = candidates[direction_filter]

    if filtered.empty:
        print("❌ 방향성 + 거리 조건 만족 도로점 없음")
        return None

    filtered["dist_to_end"] = filtered.apply(
        lambda row: haversine(row["y"], row["x"], end_lat, end_lon), axis=1
    )
    selected = filtered.sort_values("dist_to_end").iloc[0]
    print("📍 선택된 waypoint:", selected["y"], selected["x"])
    return selected["y"], selected["x"]

def get_naver_route(start, waypoint, end):
    url = "https://naveropenapi.apigw.ntruss.com/map-direction/v1/driving"
    headers = {
        "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID,
        "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET
    }
    params = {
        "start": f"{start[1]},{start[0]}",
        "goal": f"{end[1]},{end[0]}",
        "waypoints": f"{waypoint[1]},{waypoint[0]}",
        "option": "traoptimal",
        "format": "json"
    }
    res = requests.get(url, headers=headers, params=params)
    print("📡 NAVER 응답코드:", res.status_code)
    try:
        return res.json(), res.status_code
    except Exception as e:
        return {"error": str(e)}, 500

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/route", methods=["POST"])
def route():
    try:
        data = request.get_json()
        start_addr = data.get("start", "").strip()
        end_addr = data.get("end", "").strip()

        if not start_addr or not end_addr:
            return jsonify({"error": "❌ 출발지 또는 도착지 주소가 비었습니다"}), 400

        start = geocode_google(start_addr)
        end = geocode_google(end_addr)
        if not start or not end:
            return jsonify({"error": "❌ 주소 변환 실패"}), 400

        waypoint = find_waypoint(start, end)
        if not waypoint:
            return jsonify({"error": "❌ 해안 경유지 탐색 실패"}), 404

        route_data, status = get_naver_route(start, waypoint, end)
        if "error" in route_data:
            return jsonify({"error": f"❌ 경로 요청 실패: {route_data.get('error')}" }), status

        return jsonify(route_data)
    except Exception as e:
        print("❌ 서버 오류:", str(e))
        return jsonify({"error": f"❌ 서버 내부 오류: {str(e)}"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
