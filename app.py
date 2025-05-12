from flask import Flask, request, jsonify, render_template
import requests
import geopandas as gpd
from shapely.geometry import Point
import os

app = Flask(__name__)

# API Key 환경 변수 또는 직접 입력
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "YOUR_GOOGLE_API_KEY")
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID", "YOUR_NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET", "YOUR_NAVER_CLIENT_SECRET")

# 해안선 파일 로드 (EPSG:4326)
COASTLINE_PATH = "coastal_route_result.geojson"
coastline = gpd.read_file(COASTLINE_PATH)
coastline = coastline[coastline.geometry.type == 'LineString']
coastline["centroid"] = coastline.geometry.centroid

# 주소 → 좌표 변환

def get_coordinates(address):
    url = f"https://maps.googleapis.com/maps/api/geocode/json"
    params = {"address": address, "key": GOOGLE_API_KEY}
    response = requests.get(url, params=params)
    if response.status_code != 200:
        print("❌ 구글 주소 변환 실패:", response.status_code)
        return None
    results = response.json().get("results")
    if not results:
        print("❌ 주소 결과 없음:", address)
        return None
    location = results[0]["geometry"]["location"]
    return location["lat"], location["lng"]

# 가장 가까운 해안선 후보 탐색

def find_nearest_waypoint(start_lat, start_lng, end_lat, end_lng):
    try:
        print("📍 시작 좌표:", start_lat, start_lng)
        print("📍 목적지 좌표:", end_lat, end_lng)

        lat_diff = (coastline["centroid"].y - start_lat).abs()
        lng_diff = (coastline["centroid"].x - start_lng).abs()

        lat_sorted = coastline.loc[lat_diff.nsmallest(10).index]
        lng_sorted = coastline.loc[lng_diff.nsmallest(10).index]

        candidates = lat_sorted.append(lng_sorted).drop_duplicates()
        print("🔍 후보 수:", len(candidates))

        if candidates.empty:
            return None

        # 시작점 기준으로 가장 가까운 후보 선택
        start_point = Point(start_lng, start_lat)
        candidates["dist"] = candidates.centroid.distance(start_point)
        waypoint = candidates.sort_values("dist").iloc[0].centroid
        return waypoint.y, waypoint.x
    except Exception as e:
        print("❌ 해안 경유지 탐색 실패:", e)
        return None

# 네이버 Directions API

def get_route_via_naver(start, waypoint, end):
    try:
        coords = f"{start[1]},{start[0]}|{waypoint[1]},{waypoint[0]}|{end[1]},{end[0]}"
        headers = {
            "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID,
            "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET,
        }
        url = f"https://naveropenapi.apigw.ntruss.com/map-direction/v1/driving?start={coords.split('|')[0]}&goal={coords.split('|')[2]}&waypoints={coords.split('|')[1]}&option=trafast"
        res = requests.get(url, headers=headers)
        if res.status_code != 200:
            print("❌ 네이버 경로 요청 실패:", res.status_code)
            return None
        return res.json()
    except Exception as e:
        print("❌ 네이버 경로 계산 예외:", e)
        return None

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/route", methods=["POST"])
def route():
    try:
        data = request.get_json()
        if not data or "start" not in data or "end" not in data:
            print("❌ 잘못된 요청:", data)
            return jsonify({"error": "❌ 잘못된 요청"}), 400

        start_address = data["start"]
        end_address = data["end"]
        print("🚗 입력 주소:", start_address, "→", end_address)

        start = get_coordinates(start_address)
        end = get_coordinates(end_address)
        if not start or not end:
            return jsonify({"error": "❌ 주소 변환 실패"}), 400

        waypoint = find_nearest_waypoint(start[0], start[1], end[0], end[1])
        if not waypoint:
            return jsonify({"error": "❌ 해안 경유지 탐색 실패"}), 404

        route_data = get_route_via_naver(start, waypoint, end)
        if not route_data:
            return jsonify({"error": "❌ 경로 탐색 실패"}), 500

        return jsonify(route_data)

    except Exception as e:
        print("❌ 전체 처리 오류:", e)
        return jsonify({"error": f"❌ 서버 오류: {str(e)}"}), 500

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=10000)
