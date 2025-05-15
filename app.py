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

# 거리 계산 함수
def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2)**2
    return 2 * R * asin(sqrt(a))

# 주소 → 좌표
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

# 방향성과 해안 유사 좌표 기반 해안도로 경유점 탐색
def find_best_coastal_waypoint(start_lat, start_lon, end_lat, end_lon):
    lat_diff = abs(start_lat - end_lat)
    lon_diff = abs(start_lon - end_lon)
    use_lat = lat_diff > lon_diff

    rounded_lat = round(start_lat, 2)
    rounded_lon = round(start_lon, 2)

    lat_matches = road_points[road_points["y"].round(2) == rounded_lat].copy()
    lon_matches = road_points[road_points["x"].round(2) == rounded_lon].copy()

    candidates = pd.concat([lat_matches, lon_matches]).drop_duplicates()
    if candidates.empty:
        print("❌ 유사 좌표 도로점 없음")
        return None

    # 해안선에 유사한 점 찾기 (소수점 2자리 비교)
    coast_coords = [(round(pt.y, 2), round(pt.x, 2)) for pt in coastline.geometry.representative_point()]

    def is_close_to_coast(row):
        return any(round(row["y"], 2) == coast_lat and round(row["x"], 2) == coast_lon for coast_lat, coast_lon in coast_coords)

    coastal_candidates = candidates[candidates.apply(is_close_to_coast, axis=1)]
    if coastal_candidates.empty:
        print("❌ 해안 유사 좌표 없음, 전체 후보에서 가장 가까운 점 선택")
        coastal_candidates = candidates

    coastal_candidates["dist_to_end"] = coastal_candidates.apply(
        lambda row: haversine(row["y"], row["x"], end_lat, end_lon), axis=1
    )
    selected = coastal_candidates.sort_values("dist_to_end").iloc[0]
    print("📍 선택된 waypoint:", selected["y"], selected["x"])
    return selected["y"], selected["x"]

# ORS API 요청
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

    waypoint = find_best_coastal_waypoint(start[0], start[1], end[0], end[1])
    if not waypoint:
        return jsonify({"error": "❌ 해안 경유지 탐색 실패"}), 500

    route_data, status = get_ors_route(start, waypoint, end)
    if "error" in route_data:
        return jsonify({"error": f"❌ 경로 요청 실패: {route_data.get('error')}" }), status

    return jsonify(route_data)

if __name__ == "__main__":
    PORT = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=PORT)
