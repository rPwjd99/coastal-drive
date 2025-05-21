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
ORS_API_KEY = os.getenv("ORS_API_KEY")

print("✅ API 키 로딩 확인")
print("GOOGLE_API_KEY:", bool(GOOGLE_API_KEY))
print("NAVER_API_KEY_ID:", bool(NAVER_API_KEY_ID))
print("NAVER_API_KEY_SECRET:", bool(NAVER_API_KEY_SECRET))
print("ORS_API_KEY:", bool(ORS_API_KEY))

road_points = pd.read_csv("road_endpoints_reduced.csv", low_memory=False)
coastline = gpd.read_file("coastal_route_result.geojson").to_crs(epsg=4326)

def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lat2 - lat1)
    a = sin(dlat / 2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2)**2
    return 2 * R * asin(sqrt(a))

def geocode_google(address):
    print("📍 주소 변환 시도:", address)
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    try:
        res = requests.get(url, params={"address": address, "key": GOOGLE_API_KEY})
        res.raise_for_status()
        location = res.json()["results"][0]["geometry"]["location"]
        print("📍 주소 변환 성공:", location)
        return location["lat"], location["lng"]
    except Exception as e:
        print("❌ 주소 변환 실패:", address, e)
        return None

def get_coastal_candidates(start):
    print("🔍 해안선 3km 이내 도로점 탐색")
    nearby = []
    for _, row in road_points.iterrows():
        point = Point(row["x"], row["y"])
        for geom in coastline.geometry:
            try:
                if geom.distance(point) < 0.027:  # 약 3km
                    nearby.append((row["y"], row["x"]))
                    break
            except:
                continue
    print(f"✅ 후보 수: {len(nearby)}")
    return sorted(nearby, key=lambda c: haversine(start[0], start[1], c[0], c[1]))

def get_naver_route(start, waypoint, end):
    print("📡 NAVER 경로 요청 중...")
    url = "https://naveropenapi.apigw.ntruss.com/map-direction-15/v1/driving"
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
    res = requests.get(url, headers=headers, params=params)
    print("📡 NAVER 응답코드:", res.status_code)
    try:
        data = res.json()
        print("📦 NAVER 응답 JSON 키:", list(data.keys()))
        return data, res.status_code
    except Exception as e:
        return {"error": str(e)}, 500

def get_ors_route(start, waypoint, end):
    print("📡 ORS 경로 요청 중...")
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
        data = res.json()
        print("📦 ORS 응답 JSON 키:", list(data.keys()))
        return data, res.status_code
    except Exception as e:
        return {"error": str(e)}, 500

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/route", methods=["POST"])
def route():
    try:
        print("✅ /route 진입")
        data = request.get_json()
        start = geocode_google(data.get("start"))
        end = geocode_google(data.get("end"))
        if not start or not end:
            print("❌ 출발지 또는 도착지 좌표 없음")
            return jsonify({"error": "❌ 주소 변환 실패"}), 400

        candidates = get_coastal_candidates(start)
        if not candidates:
            print("❌ 웨이포인트 후보 없음")
            return jsonify({"error": "❌ 웨이포인트 없음"}), 400

        for waypoint in candidates:
            print("🔁 웨이포인트 시도:", waypoint)
            naver_data, _ = get_naver_route(start, waypoint, end)
            if "route" in naver_data:
                print("✅ NAVER 경로 성공")
                return jsonify({"source": "naver", "path": naver_data["route"]["traoptimal"][0]["path"]})

            ors_data, _ = get_ors_route(start, waypoint, end)
            if "features" in ors_data:
                print("✅ ORS 경로 성공")
                return jsonify({"source": "ors", "path": ors_data["features"][0]["geometry"]["coordinates"]})

        print("❌ 모든 경로 API 실패")
        return jsonify({"error": "❌ 모든 경로 API 실패"}), 500
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"❌ 서버 내부 오류: {str(e)}"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
