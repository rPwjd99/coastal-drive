import os
import requests
import pandas as pd
import geopandas as gpd
from flask import Flask, request, jsonify, render_template
from math import radians, cos, sin, asin, sqrt
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
ORS_API_KEY = os.getenv("ORS_API_KEY")

print("🔑 ORS 키 앞:", ORS_API_KEY[:6] if ORS_API_KEY else "❌ 없음", flush=True)

# 데이터 경로 설정
COASTLINE_PATH = os.path.join(os.path.dirname(__file__), "coastal_route_result.geojson")
ROAD_CSV_PATH = os.path.join(os.path.dirname(__file__), "road_endpoints_reduced.csv")

# 데이터 불러오기
try:
    coastline = gpd.read_file(COASTLINE_PATH).to_crs(epsg=4326)
    road_points = pd.read_csv(ROAD_CSV_PATH, low_memory=False)
except Exception as e:
    print("❌ 파일 로딩 오류:", str(e), flush=True)
    coastline = gpd.GeoDataFrame()
    road_points = pd.DataFrame()

def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
    return 2 * R * asin(sqrt(a))

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

def snap_to_road(lat, lon):
    url = "https://roads.googleapis.com/v1/nearestRoads"
    params = {
        "points": f"{lat},{lon}",
        "key": GOOGLE_API_KEY
    }
    try:
        res = requests.get(url, params=params)
        if res.status_code != 200:
            print("❌ Roads API HTTP 오류:", res.status_code, flush=True)
            return None
        snapped_points = res.json().get("snappedPoints")
        if snapped_points:
            snapped = snapped_points[0]["location"]
            print("📍 Roads 보정:", snapped)
            return snapped["latitude"], snapped["longitude"]
        else:
            print("❌ Roads API 보정 실패: 응답 없음", flush=True)
            return None
    except Exception as e:
        print("❌ Roads API 예외 발생:", str(e), flush=True)
        return None

def find_best_waypoint(start_lat, start_lon, end_lat, end_lon):
    road_points["dist_to_start"] = road_points.apply(
        lambda row: haversine(row["y"], row["x"], start_lat, start_lon), axis=1)
    candidates = road_points[road_points["dist_to_start"] <= 3.0]  # 해안에서 3km 이내
    if candidates.empty:
        print("❌ 해안 근접 도로 없음 (3km 이내)", flush=True)
        return None

    candidates["dist_to_end"] = candidates.apply(
        lambda row: haversine(row["y"], row["x"], end_lat, end_lon), axis=1)
    selected = candidates.sort_values("dist_to_end").iloc[0]
    print("✅ 선택된 waypoint 후보:", selected["y"], selected["x"], flush=True)
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
    try:
        res = requests.post(url, headers=headers, json=body)
        print("📡 ORS 응답코드:", res.status_code, flush=True)
        if res.status_code != 200:
            return {"error": f"ORS 오류 {res.status_code}"}, res.status_code
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

        waypoint = find_best_waypoint(start[0], start[1], end[0], end[1])
        if not waypoint:
            return jsonify({"error": "❌ 해안 경유지 탐색 실패"}), 500

        corrected_waypoint = snap_to_road(*waypoint)
        if not corrected_waypoint:
            return jsonify({"error": "❌ Roads API 보정 실패"}), 500

        route_data, status = get_ors_route(start, corrected_waypoint, end)
        if "error" in route_data:
            return jsonify({"error": f"❌ 경로 요청 실패: {route_data.get('error')}" }), status

        return jsonify(route_data)
    except Exception as e:
        print("❌ 서버 오류:", str(e), flush=True)
        return jsonify({"error": f"❌ 서버 내부 오류: {str(e)}"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print("✅ 실행 포트:", port, flush=True)
    app.run(host="0.0.0.0", port=port)
