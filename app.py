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

ROAD_CSV_PATH = os.path.join(os.path.dirname(__file__), "road_endpoints_reduced.csv")
road_points = pd.read_csv(ROAD_CSV_PATH, low_memory=False)

# 대한민국 해안 범위 필터링
coastal_bounds = (
    ((road_points["y"] >= 35.0) & (road_points["y"] <= 38.0) & (road_points["x"] >= 128.0) & (road_points["x"] <= 131.0)) |  # 동해
    ((road_points["y"] >= 33.0) & (road_points["y"] <= 35.0) & (road_points["x"] >= 126.0) & (road_points["x"] <= 129.0)) |  # 남해
    ((road_points["y"] >= 34.0) & (road_points["y"] <= 38.0) & (road_points["x"] >= 124.0) & (road_points["x"] <= 126.0))    # 서해
)
road_points = road_points[coastal_bounds].copy()


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


def find_waypoint(start, end):
    start_lat, start_lon = start
    end_lat, end_lon = end

    # 위도 기준 후보
    lat_candidates = road_points[(road_points["y"].round(2) == round(start_lat, 2)) &
                                 ((end_lon - start_lon) * (road_points["x"] - start_lon) > 0)].copy()
    lat_candidates["dist"] = lat_candidates.apply(lambda row: haversine(row["y"], row["x"], end_lat, end_lon), axis=1)

    # 경도 기준 후보
    lon_candidates = road_points[(road_points["x"].round(2) == round(start_lon, 2)) &
                                 ((end_lat - start_lat) * (road_points["y"] - start_lat) > 0)].copy()
    lon_candidates["dist"] = lon_candidates.apply(lambda row: haversine(row["y"], row["x"], end_lat, end_lon), axis=1)

    all_candidates = pd.concat([lat_candidates, lon_candidates], ignore_index=True)
    if all_candidates.empty:
        print("❌ 해안 방향 후보 없음", flush=True)
        return None

    best = all_candidates.sort_values("dist").iloc[0]
    print("📍 선택된 waypoint:", best["y"], best["x"], flush=True)
    return best["y"], best["x"]


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

    waypoint = find_waypoint(start, end)
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
