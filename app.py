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

print("\U0001f511 ORS 키 앞:", ORS_API_KEY[:6] if ORS_API_KEY else "❌ 없음", flush=True)

COASTLINE_PATH = os.path.join(os.path.dirname(__file__), "coastal_route_result.geojson")
ROAD_CSV_PATH = os.path.join(os.path.dirname(__file__), "road_endpoints_reduced.csv")

coastline = gpd.read_file(COASTLINE_PATH).to_crs(epsg=4326)
road_points = pd.read_csv(ROAD_CSV_PATH, low_memory=False)

# 해안 범위만 필터링
road_points = road_points[
    ((road_points["y"].between(35, 38)) & (road_points["x"].between(128, 131))) |  # 동해안
    ((road_points["y"].between(33, 35)) & (road_points["x"].between(126, 129))) |  # 남해안
    ((road_points["y"].between(34, 38)) & (road_points["x"].between(124, 126)))    # 서해안
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
        print("📍 주소 변환 성공:", address, "→", location, flush=True)
        return location["lat"], location["lng"]
    except Exception:
        print("❌ 주소 변환 실패:", address, flush=True)
        return None


def find_best_waypoint(start_lat, start_lon, end_lat, end_lon):
    rounded_lat = round(start_lat, 2)

    # 위도 기준 해안점 필터링 + 방향성
    candidates = road_points[road_points["y"].round(2) == rounded_lat].copy()
    if candidates.empty:
        print("❌ 위도 기준 해안점 없음", flush=True)
        return None

    # 도착지 방향 필터링
    candidates = candidates[candidates["x"] > start_lon] if end_lon > start_lon else candidates[candidates["x"] < start_lon]
    if candidates.empty:
        print("❌ 도착지 방향 후보 없음", flush=True)
        return None

    candidates["dist_to_end"] = candidates.apply(
        lambda row: haversine(row["y"], row["x"], end_lat, end_lon), axis=1
    )

    selected = candidates.sort_values("dist_to_end").iloc[0]
    print("📍 선택된 waypoint:", selected["y"], selected["x"], flush=True)
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
    print("📡 ORS 응답코드:", res.status_code, flush=True)
    print("📡 ORS 응답 내용:", res.text, flush=True)

    try:
        geojson = res.json()
        if "features" not in geojson:
            return {"error": "GeoJSON features 없음"}, 500
        return geojson, res.status_code
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

    waypoint = find_best_waypoint(start[0], start[1], end[0], end[1])
    if not waypoint:
        return jsonify({"error": "❌ 해안 경유지 탐색 실패"}), 500

    # 거리 확인
    print("📏 출발→waypoint 거리:", haversine(start[0], start[1], waypoint[0], waypoint[1]), flush=True)
    print("📏 waypoint→도착 거리:", haversine(waypoint[0], waypoint[1], end[0], end[1]), flush=True)

    route_data, status = get_ors_route(start, waypoint, end)
    if "error" in route_data:
        return jsonify({"error": f"❌ 경로 요청 실패: {route_data.get('error')}"}), status

    return jsonify(route_data)


if __name__ == "__main__":
    PORT = int(os.environ.get("PORT", 10000))
    print("✅ 실행 포트:", PORT, flush=True)
    app.run(host="0.0.0.0", port=PORT)
