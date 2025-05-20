import os
import json
import requests
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point
from flask import Flask, request, jsonify, render_template
from math import radians, cos, sin, asin, sqrt

app = Flask(__name__)

# API 키
GOOGLE_API_KEY = "YOUR_GOOGLE_API_KEY"  # 여기에 본인의 Google 키 입력
NAVER_ID = "4etplzn46c"
NAVER_SECRET = "mHHltk1um0D09kTbRbbdJLN0MDpA0SXLboPlHx1F"

# 데이터 로딩
ROAD_CSV_PATH = os.path.join(os.path.dirname(__file__), "road_endpoints_reduced.csv")
COASTLINE_GEOJSON_PATH = os.path.join(os.path.dirname(__file__), "해안선_국가기본도.geojson")
road_points = pd.read_csv(ROAD_CSV_PATH, low_memory=False)
coastline = gpd.read_file(COASTLINE_GEOJSON_PATH).to_crs(epsg=4326)

# 거리 계산 함수 (km 단위)
def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
    return 2 * R * asin(sqrt(a))

# 주소 → 좌표 (Google)
def geocode_google(address):
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {"address": address, "key": GOOGLE_API_KEY}
    print(f"📤 Google 지오코딩 요청: {address}")
    try:
        res = requests.get(url, params=params)
        data = res.json()
        if data["results"]:
            loc = data["results"][0]["geometry"]["location"]
            print(f"✅ 주소 변환 성공: {address} → {loc}")
            return loc["lat"], loc["lng"]
        else:
            print(f"❌ 주소 변환 실패 (결과 없음): {address}")
    except Exception as e:
        print(f"❌ 지오코딩 예외: {e}")
    return None

# 도로 꼭점 중 해안선 3km 이내 호분 필터링
def get_nearby_coastal_waypoints():
    nearby = []
    for idx, row in road_points.iterrows():
        px, py = row["x"], row["y"]
        point = Point(px, py)
        for line in coastline.geometry:
            if line.distance(point) < 0.027:  # 대량 3km (4326 좌표계에서 1도 ≈ 111km 기준)
                nearby.append((py, px))
                break
    print(f"✅ 해안선 3km 이내 waypoint 호분 수: {len(nearby)}")
    return nearby

# 출발지-동적지 방향성과 일치하는 가장 가까운 waypoint 선택
def select_best_waypoint(start, end, candidates):
    if not candidates:
        return None
    use_lat = abs(start[0] - end[0]) > abs(start[1] - end[1])
    direction_filter = []
    for lat, lon in candidates:
        if use_lat:
            if (end[1] - start[1]) * (lon - start[1]) > 0:
                direction_filter.append((lat, lon))
        else:
            if (end[0] - start[0]) * (lat - start[0]) > 0:
                direction_filter.append((lat, lon))
    if not direction_filter:
        return None
    direction_filter.sort(key=lambda coord: haversine(coord[0], coord[1], end[0], end[1]))
    print(f"📍 선택된 waypoint: {direction_filter[0]}")
    return direction_filter[0]

# NAVER Directions API 요청
def get_naver_route(start, waypoint, end):
    url = "https://naveropenapi.apigw.ntruss.com/map-direction-15/v1/driving"
    headers = {
        "X-NCP-APIGW-API-KEY-ID": NAVER_ID,
        "X-NCP-APIGW-API-KEY": NAVER_SECRET
    }
    params = {
        "start": f"{start[1]},{start[0]}",
        "goal": f"{end[1]},{end[0]}",
        "option": "trafast",
        "cartype": 1,
        "fueltype": "gasoline",
        "mileage": 14,
        "lang": "ko"
    }
    if waypoint:
        params["waypoints"] = f"{waypoint[1]},{waypoint[0]}"
    print("📦 NAVER 경로 요청 파라미터:", params)
    res = requests.get(url, headers=headers, params=params)
    print("🛁 NAVER Directions 응답 코드:", res.status_code)
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
        start = geocode_google(data.get("start", "").strip())
        end = geocode_google(data.get("end", "").strip())
        if not start or not end:
            return jsonify({"error": "❌ 주소 변환 실패"}), 400

        candidates = get_nearby_coastal_waypoints()
        waypoint = select_best_waypoint(start, end, candidates)
        if not waypoint:
            return jsonify({"error": "❌ 적절한 해안 waypoint 없음"}), 500

        route_data, status = get_naver_route(start, waypoint, end)
        return jsonify(route_data), status

    except Exception as e:
        print("❌ 서버 오류:", str(e))
        return jsonify({"error": f"서버 오류: {str(e)}"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
