import os
import requests
import pandas as pd
from flask import Flask, request, jsonify, render_template
from math import radians, cos, sin, asin, sqrt
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)

# 🔑 환경변수 불러오기
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
NAVER_API_KEY_ID = os.getenv("NAVER_API_KEY_ID")
NAVER_API_KEY_SECRET = os.getenv("NAVER_API_KEY_SECRET")

# 📂 데이터 경로
ROAD_CSV_PATH = os.path.join(os.path.dirname(__file__), "road_endpoints_reduced.csv")
road_df = pd.read_csv(ROAD_CSV_PATH, low_memory=False)

# 🧭 거리 계산 함수
def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2)**2
    return 2 * R * asin(sqrt(a))

# 📍 주소 → 좌표 (Google)
def geocode_google(address):
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    res = requests.get(url, params={"address": address, "key": GOOGLE_API_KEY})
    try:
        location = res.json()["results"][0]["geometry"]["location"]
        print("📍 주소 변환 성공:", address, "→", location, flush=True)
        return location["lat"], location["lng"]
    except Exception as e:
        print("❌ 주소 변환 실패:", address, flush=True)
        return None

# 📌 방향성과 거리 기준으로 waypoint 탐색
def find_waypoint(start, end):
    start_lat, start_lon = start
    end_lat, end_lon = end

    use_lat = abs(start_lat - end_lat) > abs(start_lon - end_lon)
    rounded_lat = round(start_lat, 2)
    rounded_lon = round(start_lon, 2)

    if use_lat:
        candidates = road_df[road_df["y"].round(2) == rounded_lat]
        direction_filter = (road_df["x"] - start_lon) * (end_lon - start_lon) > 0
    else:
        candidates = road_df[road_df["x"].round(2) == rounded_lon]
        direction_filter = (road_df["y"] - start_lat) * (end_lat - start_lat) > 0

    filtered = candidates[direction_filter]
    if filtered.empty:
        print("❌ 방향성 + 거리 조건 만족 도로점 없음", flush=True)
        return None

    filtered["dist_to_end"] = filtered.apply(
        lambda row: haversine(row["y"], row["x"], end_lat, end_lon), axis=1
    )
    selected = filtered.sort_values("dist_to_end").iloc[0]
    print("📍 선택된 waypoint:", selected["y"], selected["x"], flush=True)
    return selected["y"], selected["x"]

# 🚘 NAVER Directions API 경로 요청
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
        "option": "traoptimal",
        "format": "json"
    }
    print("📡 NAVER 요청 시작:", flush=True)
    res = requests.get(url, headers=headers, params=params)
    print("📡 NAVER 응답코드:", res.status_code, flush=True)

    try:
        data = res.json()
        if res.status_code != 200:
            return {"error": data}, res.status_code
        return data, 200
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

        waypoint = find_waypoint(start, end)
        if not waypoint:
            return jsonify({"error": "❌ 해안 경유지 탐색 실패"}), 404

        route_data, status = get_naver_route(start, waypoint, end)
        if "error" in route_data:
            return jsonify({"error": f"❌ 경로 요청 실패: {route_data['error']}"}), status

        return jsonify(route_data)

    except Exception as e:
        print("❌ 서버 오류:", str(e), flush=True)
        return jsonify({"error": f"❌ 서버 내부 오류: {str(e)}"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print("✅ 실행 포트:", port, flush=True)
    print("🔑 NAVER KEY ID:", NAVER_API_KEY_ID if NAVER_API_KEY_ID else "❌ 없음", flush=True)
    print("🔑 NAVER KEY SECRET:", "✅ 있음" if NAVER_API_KEY_SECRET else "❌ 없음", flush=True)
    print("🔑 GOOGLE KEY:", "✅ 있음" if GOOGLE_API_KEY else "❌ 없음", flush=True)
    app.run(host="0.0.0.0", port=port)
