import os
import json
import requests
import pandas as pd
import geopandas as gpd
from flask import Flask, request, jsonify, render_template
from shapely.geometry import Point
from math import radians, cos, sin, asin, sqrt
from dotenv import load_dotenv

# .env 로드 (로컬 실행 시 필요)
load_dotenv()

app = Flask(__name__)

# ✅ 환경변수에서 API 키 불러오기
NAVER_API_KEY_ID = os.getenv("NAVER_API_KEY_ID")
NAVER_API_KEY_SECRET = os.getenv("NAVER_API_KEY_SECRET")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# ✅ Key 확인 로그 출력 (반드시 확인할 것)
print("🔑 KEY ID:", NAVER_API_KEY_ID)
print("🔑 KEY SECRET 앞:", NAVER_API_KEY_SECRET[:6] if NAVER_API_KEY_SECRET else "None")

# ✅ 파일 경로
COASTLINE_PATH = os.path.join(os.path.dirname(__file__), "coastal_route_result.geojson")
ROAD_CSV_PATH = os.path.join(os.path.dirname(__file__), "road_endpoints_reduced.csv")

# ✅ 데이터 불러오기
coastline = gpd.read_file(COASTLINE_PATH).to_crs(epsg=4326)
road_points = pd.read_csv(ROAD_CSV_PATH, low_memory=False)

# ✅ 거리 계산 함수
def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
    return 2 * R * asin(sqrt(a))

# ✅ 주소 → 좌표 변환
def geocode_google(address):
    base_url = "https://maps.googleapis.com/maps/api/geocode/json"
    res = requests.get(base_url, params={"address": address, "key": GOOGLE_API_KEY})
    try:
        location = res.json()["results"][0]["geometry"]["location"]
        print("📍 주소 변환 성공:", address, "→", location)
        return location["lat"], location["lng"]
    except Exception as e:
        print("❌ 주소 변환 실패:", address, "→", str(e))
        return None

# ✅ 해안 도로 경유지 탐색
def find_directional_road_point(start_lat, start_lon, end_lat, end_lon):
    lat_diff = abs(start_lat - end_lat)
    lon_diff = abs(start_lon - end_lon)
    use_lat = lat_diff > lon_diff
    if use_lat:
        road_points["dir_diff"] = road_points["y"].apply(lambda y: abs(y - start_lat))
    else:
        road_points["dir_diff"] = road_points["x"].apply(lambda x: abs(x - start_lon))
    road_points["dist_to_end"] = road_points.apply(
        lambda row: haversine(row["y"], row["x"], end_lat, end_lon), axis=1
    )
    candidate = road_points.sort_values(["dir_diff", "dist_to_end"]).iloc[0]
    print("📍 선택된 waypoint:", candidate["y"], candidate["x"])
    return candidate["y"], candidate["x"]

# ✅ 네이버 경로 탐색
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
        "option": "trafast",
        "output": "json"
    }

    try:
        res = requests.get(url, headers=headers, params=params)
        print("📡 응답코드:", res.status_code)
        res.raise_for_status()
        return res.json(), 200
    except requests.exceptions.RequestException as e:
        print("❌ NAVER API 요청 실패:", str(e))
        return {"api_error": str(e)}, res.status_code if 'res' in locals() else 500

# ✅ 라우팅 엔드포인트
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/route", methods=["POST"])
def route():
    data = request.get_json()
    start_addr = data.get("start")
    end_addr = data.get("end")

    print("📨 요청 주소:", start_addr, "→", end_addr)

    start = geocode_google(start_addr)
    end = geocode_google(end_addr)

    if not start:
        return jsonify({"error": "❌ 출발지 주소 변환 실패"}), 400
    if not end:
        return jsonify({"error": "❌ 목적지 주소 변환 실패"}), 400

    waypoint = find_directional_road_point(start[0], start[1], end[0], end[1])
    if not waypoint:
        return jsonify({"error": "❌ 해안 도로 경유지 탐색 실패"}), 500

    route_data, status = get_naver_route(start, waypoint, end)
    if not route_data or "api_error" in route_data:
        return jsonify({
            "error": f"❌ 네이버 경로 탐색 실패 (HTTP {status}): {route_data.get('api_error')}"
        }), 500

    try:
        coords = route_data.get("route", {}).get("trafast", [{}])[0].get("path")
        if not coords:
            raise ValueError("경로 정보가 비어 있습니다.")
        geojson = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": coords
                },
                "properties": {}
            }]
        }
        return jsonify(geojson)
    except Exception as e:
        print("❌ GeoJSON 파싱 오류:", str(e))
        return jsonify({"error": f"❌ 응답 파싱 실패: {str(e)}"}), 500

# ✅ 서버 실행
if __name__ == "__main__":
    PORT = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=PORT)
