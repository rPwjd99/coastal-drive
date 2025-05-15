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

print("\U0001f511 ORS 키 앞:", ORS_API_KEY[:6] if ORS_API_KEY else "❌ 없음", flush=True)

# 파일 경로
ROAD_CSV_PATH = os.path.join(os.path.dirname(__file__), "road_endpoints_reduced.csv")

# 로드 끝점 파일 불러오기
try:
    road_points = pd.read_csv(ROAD_CSV_PATH, low_memory=False)
    road_points["x"] = pd.to_numeric(road_points["x"], errors="coerce")
    road_points["y"] = pd.to_numeric(road_points["y"], errors="coerce")
except Exception as e:
    print("❌ CSV 로딩 오류:", str(e), flush=True)
    road_points = pd.DataFrame()

# 거리 계산
def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
    return 2 * R * asin(sqrt(a))

# 주소 → 좌표 변환
def geocode_google(address):
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    res = requests.get(url, params={"address": address, "key": GOOGLE_API_KEY})
    try:
        location = res.json()["results"][0]["geometry"]["location"]
        print("\U0001f4cd 주소 변환 성공:", address, "→", location, flush=True)
        return location["lat"], location["lng"]
    except:
        print("❌ 주소 변환 실패:", address, flush=True)
        return None

# waypoint 탐색
def find_waypoint(start_lat, start_lon, end_lat, end_lon):
    if road_points.empty:
        return None

    lat_diff = abs(start_lat - end_lat)
    lon_diff = abs(start_lon - end_lon)
    use_lat = lat_diff > lon_diff

    rounded_lat = round(start_lat, 2)
    rounded_lon = round(start_lon, 2)

    filtered = road_points[
        road_points["y"].round(2).eq(rounded_lat) if use_lat else road_points["x"].round(2).eq(rounded_lon)
    ].copy()

    if filtered.empty:
        print("❌ 유사 좌표 도로점 없음", flush=True)
        return None

    filtered["dist_to_end"] = filtered.apply(
        lambda row: haversine(row["y"], row["x"], end_lat, end_lon), axis=1
    )

    selected = filtered.sort_values("dist_to_end").iloc[0]
    print("📍 선택된 waypoint:", selected["y"], selected["x"], flush=True)
    return selected["y"], selected["x"]

# ORS API 호출
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

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/route", methods=["POST"])
def route():
    try:
        data = request.get_json()
        start_addr = data.get("start")
        end_addr = data.get("end")

        start = geocode_google(start_addr)
        end = geocode_google(end_addr)
        if not start or not end:
            return jsonify({"error": "❌ 주소 변환 실패"}), 400

        waypoint = find_waypoint(start[0], start[1], end[0], end[1])
        if not waypoint:
            return jsonify({"error": "❌ 해안 경유지 탐색 실패"}), 500

        route_data, status = get_ors_route(start, waypoint, end)
        if "error" in route_data:
            return jsonify({"error": f"❌ 경로 요청 실패: {route_data.get('error')}"}), status

        return jsonify(route_data)
    except Exception as e:
        print("❌ 서버 오류:", str(e), flush=True)
        return jsonify({"error": f"❌ 서버 내부 오류: {str(e)}"}), 500

if __name__ == "__main__":
    PORT = int(os.environ.get("PORT", 10000))
    print(f"🚀 실행 포트: {PORT}", flush=True)
    app.run(host="0.0.0.0", port=PORT)
