import os
import requests
import pandas as pd
from flask import Flask, request, jsonify, render_template
from math import radians, cos, sin, asin, sqrt
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
ORS_API_KEY = os.getenv("ORS_API_KEY")

ROAD_CSV_PATH = os.path.join(os.path.dirname(__file__), "road_endpoints_reduced.csv")
road_points = pd.read_csv(ROAD_CSV_PATH, low_memory=False)

# 거리 계산 (Haversine)
def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2)**2
    return 2 * R * asin(sqrt(a))

# 주소 → 위도/경도 변환
def geocode_google(address):
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    res = requests.get(url, params={"address": address, "key": GOOGLE_API_KEY})
    try:
        location = res.json()["results"][0]["geometry"]["location"]
        print("📍 주소 변환 성공:", address, "→", location, flush=True)
        return location["lat"], location["lng"]
    except Exception as e:
        print("❌ 주소 변환 실패:", address, str(e), flush=True)
        return None

# 최적 해안 경유지 선택 (동해안 위주 범위, 5km 이내, 방향성 포함)
def find_best_coastal_waypoint(start_lat, start_lon, end_lat, end_lon):
    candidates = road_points[
        (road_points["y"] >= 35) & (road_points["y"] <= 38) &
        (road_points["x"] >= 128) & (road_points["x"] <= 131)
    ].copy()

    if candidates.empty:
        print("❌ 동해 해안 범위 내 도로 없음", flush=True)
        return None

    candidates["dist_from_start"] = candidates.apply(
        lambda row: haversine(row["y"], row["x"], start_lat, start_lon), axis=1
    )
    candidates = candidates[candidates["dist_from_start"] <= 5]

    if candidates.empty:
        print("❌ 5km 이내 해안 도로 없음", flush=True)
        return None

    use_lat = abs(start_lat - end_lat) > abs(start_lon - end_lon)
    def same_direction(row):
        if use_lat:
            return (end_lon - start_lon) * (row["x"] - start_lon) > 0
        else:
            return (end_lat - start_lat) * (row["y"] - start_lat) > 0

    candidates = candidates[candidates.apply(same_direction, axis=1)]
    if candidates.empty:
        print("❌ 방향성 맞는 해안 도로 없음", flush=True)
        return None

    candidates["dist_to_end"] = candidates.apply(
        lambda row: haversine(row["y"], row["x"], end_lat, end_lon), axis=1
    )
    selected = candidates.sort_values("dist_to_end").iloc[0]
    print("✅ 선택된 해안 경유지:", selected["y"], selected["x"], flush=True)
    return selected["y"], selected["x"]

# ORS 경로 요청
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
    print("📡 ORS 응답코드:", res.status_code, flush=True)
    try:
        return res.json(), res.status_code
    except Exception as e:
        return {"error": str(e)}, 500

# 메인 라우팅
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

        waypoint = find_best_coastal_waypoint(start[0], start[1], end[0], end[1])
        if not waypoint:
            return jsonify({"error": "❌ 해안 경유지 탐색 실패"}), 500

        route_data, status = get_ors_route(start, waypoint, end)
        if "error" in route_data:
            return jsonify({"error": f"❌ 경로 요청 실패: {route_data.get('error')}" }), status

        return jsonify(route_data)
    except Exception as e:
        print("❌ 서버 오류:", str(e), flush=True)
        return jsonify({"error": f"❌ 서버 내부 오류: {str(e)}"}), 500

# 포트 설정
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print(f"✅ 실행 포트: {port}", flush=True)
    app.run(host="0.0.0.0", port=port)
