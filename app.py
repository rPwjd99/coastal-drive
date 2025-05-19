import os
import requests
import pandas as pd
from flask import Flask, request, jsonify, render_template
from math import radians, cos, sin, asin, sqrt
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
ORS_API_KEY = os.getenv("ORS_API_KEY")  # ORS 제거할 경우 이 줄도 제거 가능

ROAD_CSV_PATH = os.path.join(os.path.dirname(__file__), "road_endpoints_reduced.csv")
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
        print("\U0001f4cd 주소 변환 성공:", address, "→", location, flush=True)
        return location["lat"], location["lng"]
    except Exception as e:
        print("❌ 주소 변환 실패:", address, flush=True)
        return None

def find_precise_waypoint(start, end):
    start_lat, start_lon = start
    end_lat, end_lon = end
    lat_diff = abs(start_lat - end_lat)
    lon_diff = abs(start_lon - end_lon)
    use_lat = lat_diff > lon_diff

    rounded_lat = round(start_lat, 2)
    rounded_lon = round(start_lon, 2)

    if use_lat:
        candidates = road_points[road_points["y"].round(2) == rounded_lat].copy()
        direction_filter = lambda row: (end_lon - start_lon) * (row["x"] - start_lon) > 0
    else:
        candidates = road_points[road_points["x"].round(2) == rounded_lon].copy()
        direction_filter = lambda row: (end_lat - start_lat) * (row["y"] - start_lat) > 0

    candidates = candidates[candidates.apply(direction_filter, axis=1)]
    candidates["dist_to_start"] = candidates.apply(
        lambda row: haversine(row["y"], row["x"], start_lat, start_lon), axis=1)
    candidates = candidates[candidates["dist_to_start"] <= 5]  # 5km 이내

    if candidates.empty:
        print("❌ 방향성 + 거리 조건 만족 도로점 없음", flush=True)
        return None

    candidates["dist_to_end"] = candidates.apply(
        lambda row: haversine(row["y"], row["x"], end_lat, end_lon), axis=1)

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
    try:
        data = request.get_json()
        start = geocode_google(data.get("start"))
        end = geocode_google(data.get("end"))
        if not start or not end:
            return jsonify({"error": "❌ 주소 변환 실패"}), 400

        waypoint = find_precise_waypoint(start, end)
        if not waypoint:
            return jsonify({"error": "❌ 해안 경유지 탐색 실패"}), 404

        route_data, status = get_ors_route(start, waypoint, end)
        if "error" in route_data:
            return jsonify({"error": f"❌ 경로 요청 실패: {route_data.get('error')}"}), status

        return jsonify(route_data)

    except Exception as e:
        print("❌ 서버 내부 오류:", str(e))
        return jsonify({"error": f"❌ 서버 내부 오류: {str(e)}"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
