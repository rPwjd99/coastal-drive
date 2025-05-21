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

road_points = pd.read_csv("road_endpoints_reduced.csv")
coastline = gpd.read_file("coastal_route_result.geojson").to_crs(epsg=4326)

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
        print("📍 주소 변환:", address, "→", location)
        return location["lat"], location["lng"]
    except:
        print("❌ 주소 변환 실패:", address)
        return None

def get_coastal_candidates(start):
    print("🔍 해안선 3km 이내 도로점 탐색")
    nearby = []
    for _, row in road_points.iterrows():
        point = Point(row["x"], row["y"])
        for geom in coastline.geometry:
            try:
                if geom.distance(point) < 0.045:  # 약 5km로 확장 가능
                    nearby.append((row["y"], row["x"]))
                    break
            except:
                continue
    print(f"✅ 후보 수: {len(nearby)}")
    return sorted(nearby, key=lambda c: haversine(start[0], start[1], c[0], c[1]))

def get_naver_route(start, waypoint, end):
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
        res_json = res.json()
        print("📦 NAVER 응답 JSON:", res_json)
        return res_json, res.status_code
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

        candidates = get_coastal_candidates(start)
        if not candidates:
            return jsonify({"error": "❌ 웨이포인트 없음"}), 400

        for waypoint in candidates:
            print("🔁 웨이포인트 시도:", waypoint)
            route_data, status = get_naver_route(start, waypoint, end)
            if "route" in route_data:
                print("✅ 유효한 경로 확보")
                return jsonify(route_data), status

        return jsonify({"error": "❌ 경로 생성 실패 (모든 웨이포인트 실패)"}), 500
    except Exception as e:
        print("❌ 서버 오류:", str(e))
        return jsonify({"error": f"❌ 서버 내부 오류: {str(e)}"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
