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
NAVER_API_KEY_ID = os.getenv("NAVER_API_KEY_ID")
NAVER_API_KEY_SECRET = os.getenv("NAVER_API_KEY_SECRET")

# 파일 경로 (절대 경로)
BASE_DIR = Path(__file__).resolve().parent
CSV_PATH = BASE_DIR / "road_endpoints_reduced.csv"
GEOJSON_PATH = BASE_DIR / "coastal_route_result.geojson"

road_points = pd.read_csv(CSV_PATH)
coastline = gpd.read_file(GEOJSON_PATH).to_crs(epsg=4326)

def haversine(lat1, lon1, lat2, lon2):
    R = 6371  # km
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2)**2
    return 2 * R * asin(sqrt(a))

def geocode_google(address):
    if not GOOGLE_API_KEY:
        print("⚠️ GOOGLE_API_KEY 환경변수 없음")
        return None
    try:
        url = "https://maps.googleapis.com/maps/api/geocode/json"
        res = requests.get(url, params={"address": address, "key": GOOGLE_API_KEY})
        data = res.json()
        location = data["results"][0]["geometry"]["location"]
        print(f"✅ 주소 변환 성공: {address} → {location}")
        return location["lat"], location["lng"]
    except Exception as e:
        print(f"❌ 주소 변환 실패: {address}", e)
        return None

def get_nearby_coastal_waypoints():
    print("🔍 해안선 3km 이내 웨이포인트 탐색 시작")
    nearby = []
    if coastline.empty or road_points.empty:
        print("❌ coastline 또는 road_points 비어 있음")
        return []
    for _, row in road_points.iterrows():
        point = Point(row["x"], row["y"])
        try:
            for geom in coastline.geometry:
                if geom.distance(point) < 0.027:  # 약 3km
                    nearby.append((row["y"], row["x"]))  # 위도, 경도 순서
                    break
        except Exception as e:
            print("❌ 거리 계산 오류:", e)
    print(f"✅ 웨이포인트 후보 개수: {len(nearby)}")
    return nearby

def get_naver_route(start, waypoint, end):
    headers = {
        "X-NCP-APIGW-API-KEY-ID": NAVER_API_KEY_ID,
        "X-NCP-APIGW-API-KEY": NAVER_API_KEY_SECRET,
    }
    params = {
        "start": f"{start[1]},{start[0]}",
        "goal": f"{end[1]},{end[0]}",
        "waypoints": f"{waypoint[1]},{waypoint[0]}",
        "option": "traoptimal"
    }
    url = "https://naveropenapi.apigw.ntruss.com/map-direction-15/v1/driving"
    res = requests.get(url, headers=headers, params=params)
    print("📡 네이버 Directions 응답코드:", res.status_code)
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

        waypoint_candidates = get_nearby_coastal_waypoints()
        if not waypoint_candidates:
            return jsonify({"error": "❌ 웨이포인트 없음"}), 400

        selected = sorted(waypoint_candidates, key=lambda wp: haversine(start[0], start[1], wp[0], wp[1]))[0]
        print(f"📍 선택된 웨이포인트: {selected}")

        route_data, status = get_naver_route(start, selected, end)
        return jsonify(route_data), status
    except Exception as e:
        print("❌ 서버 오류:", str(e))
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
