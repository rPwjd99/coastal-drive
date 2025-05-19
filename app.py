import os
import pandas as pd
from flask import Flask, request, jsonify, render_template
from math import radians, cos, sin, asin, sqrt
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")

# ✅ 파일명 정확히 반영
road_path = "road_endpoints_reduced.csv"
road_points = pd.read_csv(road_path)

def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
    return 2 * R * asin(sqrt(a))

def geocode_google(address):
    import requests
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {"address": address, "key": GOOGLE_API_KEY}
    res = requests.get(url, params=params)
    try:
        location = res.json()["results"][0]["geometry"]["location"]
        print("📍 주소 변환 성공:", address, "→", location)
        return location["lat"], location["lng"]
    except Exception as e:
        print("❌ 주소 변환 실패:", address, e)
        return None

def find_waypoint(start, end):
    start_lat, start_lon = start
    end_lat, end_lon = end

    lat_diff = abs(start_lat - end_lat)
    lon_diff = abs(start_lon - end_lon)
    use_lat = lat_diff > lon_diff

    if use_lat:
        filtered = road_points[road_points["y"].round(2) == round(start_lat, 2)]
        direction_filter = (road_points["x"] - start_lon) * (end_lon - start_lon) > 0
    else:
        filtered = road_points[road_points["x"].round(2) == round(start_lon, 2)]
        direction_filter = (road_points["y"] - start_lat) * (end_lat - start_lat) > 0

    filtered = filtered[direction_filter]

    if filtered.empty:
        print("❌ 방향성 + 거리 조건 만족 도로점 없음")
        return None

    filtered["dist"] = filtered.apply(
        lambda row: haversine(start_lat, start_lon, row["y"], row["x"]), axis=1
    )
    candidate = filtered.sort_values("dist").iloc[0]
    print("📍 선택된 waypoint:", candidate["y"], candidate["x"])
    return candidate["y"], candidate["x"]

def get_naver_route(start, waypoint, end):
    import requests
    url = "https://naveropenapi.apigw.ntruss.com/map-direction/v1/driving"
    headers = {
        "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID,
        "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET
    }
    waypoints = f"{waypoint[1]},{waypoint[0]}"
    params = {
        "start": f"{start[1]},{start[0]}",
        "goal": f"{end[1]},{end[0]}",
        "waypoints": waypoints,
        "option": "traoptimal",
        "output": "json"
    }
    response = requests.get(url, headers=headers, params=params)
    try:
        return response.json(), response.status_code
    except Exception as e:
        return {"error": str(e)}, 500

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

    waypoint = find_waypoint(start, end)
    if not waypoint:
        return jsonify({"error": "❌ 해안 경유지 탐색 실패"}), 404

    route_data, status = get_naver_route(start, waypoint, end)
    if "error" in route_data:
        return jsonify({"error": f"❌ 경로 요청 실패: {route_data.get('error')}"}), status

    return jsonify(route_data)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
