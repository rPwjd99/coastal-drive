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

print("🔑 ORS 키 앞:", ORS_API_KEY[:6])

COASTLINE_PATH = os.path.join(os.path.dirname(__file__), "coastal_route_result.geojson")
ROAD_CSV_PATH = os.path.join(os.path.dirname(__file__), "road_endpoints_reduced.csv")

coastline = gpd.read_file(COASTLINE_PATH).to_crs(epsg=4326)
road_points = pd.read_csv(ROAD_CSV_PATH, low_memory=False)

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

def compute_dist_to_coast():
    coast_points = coastline.geometry.apply(lambda geom: geom.representative_point().coords[0])
    coast_coords = [(pt[1], pt[0]) for pt in coast_points]
    def min_dist_to_coast(row):
        return min(haversine(row['y'], row['x'], lat, lon) for lat, lon in coast_coords)
    road_points["dist_to_coast_km"] = road_points.apply(min_dist_to_coast, axis=1)

if "dist_to_coast_km" not in road_points.columns:
    print("📦 해안거리 계산 중...")
    compute_dist_to_coast()

def find_best_coastal_waypoint(start, end):
    start_lat, start_lon = start
    end_lat, end_lon = end

    use_lat = abs(start_lat - end_lat) > abs(start_lon - end_lon)

    # 방향성 우선 필터링
    def is_in_direction(row):
        if use_lat:
            return (end_lon - start_lon) * (row['x'] - start_lon) > 0
        else:
            return (end_lat - start_lat) * (row['y'] - start_lat) > 0

    filtered = road_points[
        (road_points["dist_to_coast_km"] <= 1.0) &
        (road_points.apply(is_in_direction, axis=1))
    ]

    if filtered.empty:
        print("❌ 조건에 맞는 해안도로 경유지 없음")
        return None

    if use_lat:
        filtered["dir_diff"] = abs(filtered["y"] - start_lat)
        filtered["target_dist"] = abs(filtered["x"] - end_lon)
    else:
        filtered["dir_diff"] = abs(filtered["x"] - start_lon)
        filtered["target_dist"] = abs(filtered["y"] - end_lat)

    filtered["dist_to_end"] = filtered.apply(
        lambda row: haversine(row["y"], row["x"], end_lat, end_lon), axis=1
    )

    candidate = filtered.sort_values(["dir_diff", "target_dist", "dist_to_end"]).iloc[0]
    print("📍 선택된 waypoint:", candidate["y"], candidate["x"])
    return candidate["y"], candidate["x"]

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
    data = request.get_json()
    start_addr = data.get("start")
    end_addr = data.get("end")

    start = geocode_google(start_addr)
    end = geocode_google(end_addr)
    if not start or not end:
        return jsonify({"error": "❌ 주소 변환 실패"}), 400

    waypoint = find_best_coastal_waypoint(start, end)
    if not waypoint:
        return jsonify({"error": "❌ 경유지 탐색 실패 (방향 + 해안 1km 이내 조건 불충족)"}), 500

    route_data, status = get_ors_route(start, waypoint, end)
    if "error" in route_data:
        return jsonify({"error": f"❌ 경로 요청 실패: {route_data.get('error')}" }), status

    return jsonify(route_data)

if __name__ == "__main__":
    PORT = int(os.environ.get("PORT", 5000))
    print(f"✅ 실행 포트: {PORT}")
    app.run(host="0.0.0.0", port=PORT)
