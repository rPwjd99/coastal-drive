import os
import json
import requests
import geopandas as gpd
from shapely.geometry import Point
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

# API KEY
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID", "vsdzf1f4n5")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET", "0gzctO51PUTVv0gUZU025JYNHPTmVzLS9sGbfYBM")

# 해안선 GeoJSON 불러오기
coastline_path = "coastal_route_result.geojson"
coastline = gpd.read_file(coastline_path)

# 대표 좌표(centroid) 계산
if not coastline.geometry.geom_type.isin(['Point']).all():
    coastline['centroid'] = coastline.geometry.centroid
else:
    coastline['centroid'] = coastline.geometry

def geocode_address_google(address):
    try:
        url = f"https://maps.googleapis.com/maps/api/geocode/json?address={address}&key=AIzaSyC9MSD-WhkqK_Og5YdVYfux21xiRjy2q1M"
        response = requests.get(url)
        data = response.json()
        if data["status"] == "OK":
            loc = data["results"][0]["geometry"]["location"]
            return loc["lat"], loc["lng"]
        else:
            print(f"❌ 구글 주소 변환 실패: {address}, 상태: {data['status']}")
            return None
    except Exception as e:
        print("❌ 예외 발생(주소 변환):", e)
        return None

def find_best_waypoint(start_lat, start_lon, end_lat, end_lon):
    print("🔍 해안 경유지 탐색 중...")
    try:
        # centroid 기준 위도, 경도 추출
        coast_centroids = coastline['centroid']

        # 위도 기준 가까운 해안 20개
        lat_sorted = coastline.iloc[(coast_centroids.y - start_lat).abs().argsort()[:20]]
        # 경도 기준 가까운 해안 20개
        lon_sorted = coastline.iloc[(coast_centroids.x - start_lon).abs().argsort()[:20]]

        # 두 후보 중 목적지와 더 가까운 쪽 선택
        lat_pt = lat_sorted.iloc[0].centroid
        lon_pt = lon_sorted.iloc[0].centroid
        dist_lat = Point(end_lon, end_lat).distance(lat_pt)
        dist_lon = Point(end_lon, end_lat).distance(lon_pt)
        best_pt = lat_pt if dist_lat < dist_lon else lon_pt

        print("✅ 선택된 Waypoint:", best_pt.y, best_pt.x)
        return best_pt.y, best_pt.x  # (lat, lon)
    except Exception as e:
        print("❌ 해안 경유지 탐색 실패:", e)
        return None

def get_naver_route(start, waypoint, end):
    headers = {
        "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID,
        "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET,
        "Content-Type": "application/json"
    }
    body = {
        "start": {"x": str(start[1]), "y": str(start[0]), "name": "출발지"},
        "goal": {"x": str(end[1]), "y": str(end[0]), "name": "도착지"},
        "waypoints": [{"x": str(waypoint[1]), "y": str(waypoint[0]), "name": "해안"}],
        "option": "traoptimal"
    }
    try:
        res = requests.post("https://naveropenapi.apigw.ntruss.com/map-direction/v1/driving", headers=headers, data=json.dumps(body))
        data = res.json()
        if res.status_code == 200 and "route" in data:
            path = data["route"]["traoptimal"][0]["path"]
            print("✅ 네이버 경로 계산 성공")
            return path
        else:
            print("❌ 경로 계산 실패:", data)
            return None
    except Exception as e:
        print("❌ 경로 요청 예외:", e)
        return None

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/route", methods=["POST"])
def route():
    try:
        data = request.json
        start_address = data["start"]
        end_address = data["end"]

        start = geocode_address_google(start_address)
        end = geocode_address_google(end_address)

        if not start or not end:
            return jsonify({"error": "❌ 주소 변환 실패"}), 400

        waypoint = find_best_waypoint(start[0], start[1], end[0], end[1])
        if not waypoint:
            return jsonify({"error": "❌ 해안 경유지 탐색 실패"}), 500

        route_path = get_naver_route(start, waypoint, end)
        if not route_path:
            return jsonify({"error": "❌ 경로 탐색 실패"}), 500

        return jsonify({"path": route_path})
    except Exception as e:
        print("❌ 서버 오류:", e)
        return jsonify({"error": f"❌ 서버 오류: {str(e)}"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
