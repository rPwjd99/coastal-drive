import os
import requests
import pandas as pd
from flask import Flask, request, jsonify, render_template
from dotenv import load_dotenv
from math import radians, cos, sin, asin, sqrt

load_dotenv()
app = Flask(__name__)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
NAVER_API_KEY_ID = os.getenv("NAVER_API_KEY_ID")
NAVER_API_KEY_SECRET = os.getenv("NAVER_API_KEY_SECRET")

ROAD_CSV_PATH = os.path.join(os.path.dirname(__file__), "road_endpoints_reduced.csv")
road_points = pd.read_csv(ROAD_CSV_PATH, low_memory=False)

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
        print("❌ 주소 변환 실패:", address)
        return None

def find_best_waypoint(start, end):
    start_lat, start_lon = start
    end_lat, end_lon = end

    lat_diff = abs(start_lat - end_lat)
    lon_diff = abs(start_lon - end_lon)
    use_lat = lat_diff > lon_diff

    filtered = road_points.copy()
    if use_lat:
        filtered = filtered[road_points["y"].round(2) == round(start_lat, 2)]
        direction_filter = (end_lon - start_lon) * (filtered["x"] - start_lon) > 0
    else:
        filtered = filtered[road_points["x"].round(2) == round(start_lon, 2)]
        direction_filter = (end_lat - start_lat) * (filtered["y"] - start_lat) > 0

    filtered = filtered[direction_filter]
    filtered["dist_to_end"] = filtered.apply(
        lambda row: haversine(row["y"], row["x"], end_lat, end_lon), axis=1)

    if filtered.empty:
        print("❌ 방향성 + 거리 조건 만족 도로점 없음")
        return None

    best = filtered.sort_values("dist_to_end").iloc[0]
    print("📍 선택된 waypoint:", best["y"], best["x"])
    return best["y"], best["x"]

def get_naver_route(start, waypoint, end):
    url = "https://naveropenapi.apigw.ntruss.com/map-direction/v1/driving"
    headers = {
        "X-NCP-APIGW-API-KEY-ID": NAVER_API_KEY_ID,
        "X-NCP-APIGW-API-KEY": NAVER_API_KEY_SECRET
    }
    params = {
        "start": f"{start[1]},{start[0]}",
        "goal": f"{end[1]},{end[0]}",
        "waypoints": f"{waypoint[1]},{waypoint[0]}",
        "option": "trafast"
    }
    res = requests.get(url, headers=headers, params=params)
    print("📡 NAVER 응답코드:", res.status_code)

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
            return jsonify({"error": "❌ 해안 경유지 탐색 실패"}), 404

        route_data, status = get_naver_route(start, waypoint, end)
        if "route" not in route_data:
            return jsonify({"error": f"❌ 경로 요청 실패: {route_data.get('errorMessage', '알 수 없음')}"}), status

        return jsonify(route_data)
    except Exception as e:
        print("❌ 서버 내부 오류:", str(e))
        return jsonify({"error": f"❌ 서버 오류: {str(e)}"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
