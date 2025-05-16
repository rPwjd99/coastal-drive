import os
import pandas as pd
import requests
from flask import Flask, request, jsonify, render_template
from math import radians, cos, sin, asin, sqrt
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
ORS_API_KEY = os.getenv("ORS_API_KEY")
PORT = int(os.environ.get("PORT", 10000))

ROAD_CSV_PATH = os.path.join(os.path.dirname(__file__), "road_endpoints_reduced.csv")
road_points = pd.read_csv(ROAD_CSV_PATH, encoding="cp949", low_memory=False)

# ✅ 해안선 범위만 필터링
east_coast = (
    (road_points["y"] >= 35.0) & (road_points["y"] <= 38.0) &
    (road_points["x"] >= 128.0) & (road_points["x"] <= 131.0)
)
south_coast = (
    (road_points["y"] >= 33.0) & (road_points["y"] <= 35.0) &
    (road_points["x"] >= 126.0) & (road_points["x"] <= 129.0)
)
west_coast = (
    (road_points["y"] >= 34.0) & (road_points["y"] <= 38.0) &
    (road_points["x"] >= 124.0) & (road_points["x"] <= 126.0)
)

road_points = road_points[east_coast | south_coast | west_coast].copy()

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

def find_best_waypoint(start, end):
    start_lat, start_lon = start
    end_lat, end_lon = end
    use_lat = abs(start_lat - end_lat) > abs(start_lon - end_lon)

    # 방향성 조건
    def direction_filter(row):
        if use_lat:
            return (end_lon - start_lon) * (row["x"] - start_lon) > 0
        else:
            return (end_lat - start_lat) * (row["y"] - start_lat) > 0

    candidates = road_points.copy()
    candidates = candidates[candidates.apply(direction_filter, axis=1)]

    if candidates.empty:
        print("❌ 해안 경유지 없음")
        return None

    candidates["dist_to_start"] = candidates.apply(
        lambda row: haversine(start_lat, start_lon, row["y"], row["x"]), axis=1
    )
    candidates["dist_to_end"] = candidates.apply(
        lambda row: haversine(end_lat, end_lon, row["y"], row["x"]), axis=1
    )
    selected = candidates.sort_values(["dist_to_start", "dist_to_end"]).iloc[0]
    print("✅ 선택된 waypoint:", selected["y"], selected["x"])
    return selected["y"], selected["x"]

def get_ors_route(start, waypoint, end):
    url = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
    headers = {
        "Authorization": ORS_API_KEY,
        "Content-Type": "application/json"
    }
    body = {
        "coordinates": [
            [start[1], start[0]],
            [waypoint[1], waypoint[0]],
            [end[1], end[0]]
        ]
    }

    res = requests.post(url, headers=headers, json=body)
    print("📡 ORS 응답코드:", res.status_code)
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
        start = geocode_google(data.get("start"))
        end = geocode_google(data.get("end"))
        if not start or not end:
            return jsonify({"error": "❌ 주소 변환 실패"}), 400

        waypoint = find_best_waypoint(start, end)
        if not waypoint:
            return jsonify({"error": "❌ 해안 경유지 탐색 실패"}), 500

        route_data, status = get_ors_route(start, waypoint, end)
        if "error" in route_data:
            return jsonify({"error": f"❌ 경로 요청 실패: {route_data.get('error')}" }), status

        return jsonify(route_data)
    except Exception as e:
        print("❌ 서버 오류:", str(e))
        return jsonify({"error": f"❌ 서버 내부 오류: {str(e)}"}), 500

if __name__ == "__main__":
    print(f"✅ 실행 포트: {PORT}")
    app.run(host="0.0.0.0", port=PORT)
