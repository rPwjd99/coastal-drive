import os
import json
import requests
import pandas as pd
import geopandas as gpd
from flask import Flask, request, jsonify, render_template
from shapely.geometry import Point
from dotenv import load_dotenv
from math import radians, cos, sin, asin, sqrt

load_dotenv()

app = Flask(__name__)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
NAVER_API_KEY_ID = os.getenv("NAVER_API_KEY_ID")
NAVER_API_KEY_SECRET = os.getenv("NAVER_API_KEY_SECRET")

CSV_PATH = "road_endpoints_reduced.csv"
GEOJSON_PATH = "coastal_route_result.geojson"

road_points = pd.read_csv(CSV_PATH, low_memory=False)
coastline = gpd.read_file(GEOJSON_PATH).to_crs(epsg=4326)


def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * R * asin(sqrt(a))


def geocode_google(address):
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {"address": address, "key": GOOGLE_API_KEY}
    res = requests.get(url, params=params)
    try:
        loc = res.json()["results"][0]["geometry"]["location"]
        print("📍 주소 변환 성공:", address, "→", loc)
        return loc["lat"], loc["lng"]
    except:
        print("❌ 주소 변환 실패:", address)
        return None


def filter_points_near_coast():
    print("🌊 해안선 근처 필터링 시작")
    near_coast = []
    for _, row in road_points.iterrows():
        pt = Point(row["x"], row["y"])
        for geom in coastline.geometry:
            if geom.distance(pt) < 0.027:  # 3km
                near_coast.append(row)
                break
    return pd.DataFrame(near_coast)


def pick_best_waypoint(candidates, start, end):
    use_lat = abs(start[0] - end[0]) > abs(start[1] - end[1])
    if use_lat:
        directional = candidates[candidates["y"] > start[0]] if end[0] > start[0] else candidates[candidates["y"] < start[0]]
    else:
        directional = candidates[candidates["x"] > start[1]] if end[1] > start[1] else candidates[candidates["x"] < start[1]]

    directional["dist_to_end"] = directional.apply(lambda row: haversine(row["y"], row["x"], end[0], end[1]), axis=1)
    return directional.sort_values("dist_to_end").head(1)[["y", "x"]].values[0]


def get_naver_route(start, waypoint, end):
    url = "https://naveropenapi.apigw.ntruss.com/map-direction-15/v1/driving"
    headers = {
        "X-NCP-APIGW-API-KEY-ID": NAVER_API_KEY_ID,
        "X-NCP-APIGW-API-KEY": NAVER_API_KEY_SECRET
    }
    params = {
        "start": f"{start[1]},{start[0]}",
        "goal": f"{end[1]},{end[0]}",
        "waypoints": f"{waypoint[1]},{waypoint[0]}",
        "option": "traoptimal"
    }
    res = requests.get(url, headers=headers, params=params)
    print("📦 NAVER 응답 상태코드:", res.status_code)
    try:
        return res.json()
    except:
        return {"error": "Invalid JSON from NAVER"}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/route", methods=["POST"])
def route():
    try:
        data = request.get_json()
        print("📨 입력 데이터:", data)

        start = geocode_google(data.get("start"))
        end = geocode_google(data.get("end"))
        if not start or not end:
            return jsonify({"error": "❌ 주소 변환 실패"}), 400

        candidates = filter_points_near_coast()
        if candidates.empty:
            return jsonify({"error": "❌ 해안 근처 웨이포인트 없음"}), 500

        waypoint = pick_best_waypoint(candidates, start, end)
        print("📍 선택된 waypoint:", waypoint)

        route_data = get_naver_route(start, waypoint, end)
        if "route" not in route_data:
            return jsonify({"error": "❌ NAVER 경로 실패"}), 502

        return jsonify(route_data)

    except Exception as e:
        print("❌ 서버 내부 오류:", str(e))
        return jsonify({"error": f"❌ 서버 내부 오류: {str(e)}"}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print("✅ API 키 로딩 확인")
    print("GOOGLE_API_KEY:", bool(GOOGLE_API_KEY))
    print("NAVER_API_KEY_ID:", bool(NAVER_API_KEY_ID))
    print("NAVER_API_KEY_SECRET:", bool(NAVER_API_KEY_SECRET))
    app.run(host="0.0.0.0", port=port)
