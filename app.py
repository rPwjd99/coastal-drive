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

print("🔑 ORS 키 앞:", ORS_API_KEY[:6] if ORS_API_KEY else "❌ 없음", flush=True)

# 데이터 불러오기
ROAD_CSV_PATH = os.path.join(os.path.dirname(__file__), "road_endpoints_reduced.csv")
road_points = pd.read_csv(ROAD_CSV_PATH, low_memory=False)

# 거리 계산 함수
def haversine(lat1, lon1, lat2, lon2):
    R = 6371  # 지구 반지름(km)
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
    return 2 * R * asin(sqrt(a))

# 주소 → 좌표
def geocode_google(address):
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    res = requests.get(url, params={"address": address, "key": GOOGLE_API_KEY})
    try:
        location = res.json()["results"][0]["geometry"]["location"]
        print(f"📍 주소 변환 성공: {address} → {location}", flush=True)
        return location["lat"], location["lng"]
    except:
        print(f"❌ 주소 변환 실패: {address}", flush=True)
        return None

# 최적 waypoint 찾기
def find_optimal_waypoint(start, end):
    start_lat, start_lon = start
    end_lat, end_lon = end

    # 위도·경도 소수점 둘째자리 유사 조건
    rounded_lat = round(start_lat, 2)
    rounded_lon = round(start_lon, 2)

    candidate_points = road_points[
        (round(road_points['y'], 2) == rounded_lat) |
        (round(road_points['x'], 2) == rounded_lon)
    ].copy()

    if candidate_points.empty:
        print("❌ 유사 방향 도로점 없음", flush=True)
        return None

    # 해안 근접 거리 계산 (좌표 기준점: 출발지)
    candidate_points["dist_to_start"] = candidate_points.apply(
        lambda row: haversine(start_lat, start_lon, row["y"], row["x"]), axis=1
    )

    # 목적지 거리도 계산
    candidate_points["dist_to_end"] = candidate_points.apply(
        lambda row: haversine(end_lat, end_lon, row["y"], row["x"]), axis=1
    )

    # 해안과 가까우면서 목적지와 가까운 점 선택
    filtered = candidate_points[candidate_points["dist_to_start"] <= 1.0]
    if filtered.empty:
        print("❌ 해안 근접 도로 없음 (1km 이내)", flush=True)
        return None

    best = filtered.sort_values(["dist_to_end"]).iloc[0]
    print(f"📍 선택된 waypoint: ({best['y']}, {best['x']})", flush=True)
    return best["y"], best["x"]

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

# 라우팅
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

    waypoint = find_optimal_waypoint(start, end)
    if not waypoint:
        return jsonify({"error": "❌ 해안 경유지 탐색 실패"}), 500

    route_data, status = get_ors_route(start, waypoint, end)
    if "error" in route_data:
        return jsonify({"error": f"❌ 경로 요청 실패: {route_data['error']}" }), status

    return jsonify(route_data)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
