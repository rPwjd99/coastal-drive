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

ROAD_CSV_PATH = "road_endpoints_reduced.csv"  # 직접 지정
try:
    road_points = pd.read_csv(ROAD_CSV_PATH, encoding="utf-8")
except UnicodeDecodeError:
    road_points = pd.read_csv(ROAD_CSV_PATH, encoding="cp949")

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
        print("📍 주소 변환 성공:", address, "→", location)
        return location["lat"], location["lng"]
    except Exception as e:
        print("❌ 주소 변환 실패:", address, str(e))
        return None

def is_coastal(y, x):
    return (
        (35 <= y <= 38 and 128 <= x <= 131) or  # 동해안
        (33 <= y <= 35 and 126 <= x <= 129) or  # 남해안
        (34 <= y <= 38 and 124 <= x <= 126)     # 서해안
    )

def find_best_waypoint(start, end):
    start_lat, start_lon = start
    end_lat, end_lon = end

    df = road_points.copy()
    df = df[df.apply(lambda row: is_coastal(row["y"], row["x"]), axis=1)]

    lat_diff = abs(start_lat - end_lat)
    lon_diff = abs(start_lon - end_lon)
    use_lat = lat_diff > lon_diff

    if use_lat:
        filtered = df[df["y"].round(2) == round(start_lat, 2)]
        filtered = filtered[(end_lon - start_lon) * (filtered["x"] - start_lon) > 0]
    else:
        filtered = df[df["x"].round(2) == round(start_lon, 2)]
        filtered = filtered[(end_lat - start_lat) * (filtered["y"] - start_lat) > 0]

    if filtered.empty:
        print("❌ 해안 경유지 탐색 실패")
        return None

    filtered["dist_to_end"] = filtered.apply(
        lambda row: haversine(row["y"], row["x"], end_lat, end_lon), axis=1
    )
    selected = filtered.sort_values("dist_to_end").iloc[0]
    print("✅ 선택된 waypoint:", selected["y"], selected["x"])
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
    try:
        data = request.get_json()
        start = geocode_google(data.get("start"))
        end = geocode_google(data.get("end"))
        if not start or not end:
            return jsonify({"error": "❌ 주소 변환 실패"}), 400

        waypoint = find_best_waypoint(start, end)
        if not waypoint:
            return jsonify({"error": "❌ 해안 경유지 탐색 실패"}), 500

        route_data, status = get_ors_route(start, waypoint, end)
        if "error" in route_data:
            return jsonify({"error": f"❌ 경로 요청 실패: {route_data.get('error')}" }), status

        return jsonify(route_data)
    except Exception as e:
        print("❌ 서버 오류:", str(e))
        return jsonify({"error": f"❌ 서버 내부 오류: {str(e)}"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print("✅ 실행 포트:", port)
    app.run(host="0.0.0.0", port=port)
