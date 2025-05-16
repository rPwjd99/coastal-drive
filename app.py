import os
import requests
import pandas as pd
import geopandas as gpd
from flask import Flask, request, jsonify, render_template
from shapely.geometry import Point
from math import radians, cos, sin, asin, sqrt
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
ORS_API_KEY = os.getenv("ORS_API_KEY")

print("🔑 ORS 키 앞:", ORS_API_KEY[:6] if ORS_API_KEY else "❌ 없음", flush=True)

# 파일 경로
ROAD_CSV_PATH = os.path.join(os.path.dirname(__file__), "road_endpoints_reduced.csv")
road_points = pd.read_csv(ROAD_CSV_PATH, encoding="utf-8", low_memory=False)

# 거리 계산 함수
def haversine(lat1, lon1, lat2, lon2):
    R = 6371  # km
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
        print(f"📍 주소 변환 성공: {address} → {location}", flush=True)
        return location["lat"], location["lng"]
    except Exception:
        print(f"❌ 주소 변환 실패: {address}", flush=True)
        return None

# 해안선 위경도 범위만 필터링
def filter_by_coastline(road_df):
    return road_df[
        ((road_df["y"].between(33.0, 35.0)) & (road_df["x"].between(126.0, 129.0))) |  # 남해
        ((road_df["y"].between(34.0, 38.0)) & (road_df["x"].between(124.0, 126.0))) |  # 서해
        ((road_df["y"].between(35.0, 38.0)) & (road_df["x"].between(128.0, 131.0)))    # 동해
    ].copy()

# 방향성 + 거리 기반 최적 웨이포인트
def find_best_coastal_waypoint(start_lat, start_lon, end_lat, end_lon):
    candidates = filter_by_coastline(road_points)

    if candidates.empty:
        print("❌ 해안선 범위 내 후보 없음", flush=True)
        return None

    use_lat = abs(start_lat - end_lat) > abs(start_lon - end_lon)

    if use_lat:
        candidates["dir_diff"] = abs(candidates["y"] - start_lat)
        direction = (end_lon - start_lon)
        candidates = candidates[candidates["x"] - start_lon > 0] if direction > 0 else candidates[candidates["x"] - start_lon < 0]
    else:
        candidates["dir_diff"] = abs(candidates["x"] - start_lon)
        direction = (end_lat - start_lat)
        candidates = candidates[candidates["y"] - start_lat > 0] if direction > 0 else candidates[candidates["y"] - start_lat < 0]

    if candidates.empty:
        print("❌ 방향 일치 후보 없음", flush=True)
        return None

    candidates["dist_to_end"] = candidates.apply(
        lambda row: haversine(row["y"], row["x"], end_lat, end_lon), axis=1
    )

    best = candidates.sort_values(["dir_diff", "dist_to_end"]).iloc[0]
    print("✅ 선택된 waypoint:", best["y"], best["x"], flush=True)
    return best["y"], best["x"]

# ORS 경로 계산
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
        geojson = res.json()
        if "features" not in geojson:
            return {"error": "GeoJSON features 없음"}, 500
        return geojson, res.status_code
    except Exception as e:
        return {"error": str(e)}, 500

# 라우팅
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/route", methods=["POST"])
def route():
    data = request.get_json()
    start_addr = data.get("start")
    end_addr = data.get("end")

    start = geocode_google(start_addr)
    end = geocode_google(end_addr)
    if not start or not end:
        return jsonify({"error": "❌ 주소 변환 실패"}), 400

    waypoint = find_best_coastal_waypoint(start[0], start[1], end[0], end[1])
    if not waypoint:
        return jsonify({"error": "❌ 해안 경유지 탐색 실패"}), 500

    route_data, status = get_ors_route(start, waypoint, end)
    if "error" in route_data:
        return jsonify({"error": f"❌ 경로 요청 실패: {route_data.get('error')}"}), status

    return jsonify(route_data)

if __name__ == "__main__":
    PORT = int(os.environ.get("PORT", 10000))
    print("✅ 실행 포트:", PORT, flush=True)
    app.run(host="0.0.0.0", port=PORT)
