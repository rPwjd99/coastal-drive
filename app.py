import os
import json
import requests
import geopandas as gpd
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from shapely.geometry import Point
from shapely.ops import nearest_points

app = Flask(__name__)
CORS(app)

NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID", "vsdzf1f4n5")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET", "0gzctO51PUTVv0gUZU025JYNHPTmVzLS9sGbfYBM")

coastline_gdf = gpd.read_file("coastal_route_result.geojson")
coastline_gdf = coastline_gdf.to_crs(epsg=4326)

# fallback 방식: 해안선에서 여러 후보 좌표 생성 후 도로 연결 성공한 좌표 반환
def get_fallback_waypoint(start, end):
    coast_candidates = coastline_gdf.copy()
    coast_candidates["dist"] = coast_candidates.geometry.centroid.distance(Point(start))
    coast_candidates = coast_candidates.sort_values("dist").head(20)

    for idx, row in coast_candidates.iterrows():
        point = row.geometry.centroid
        lon, lat = point.x, point.y
        print(f"\U0001F4CD 테스트 후보 좌표: {lat:.6f}, {lon:.6f}")
        if test_route_with_waypoint(start, (lat, lon), end):
            print("✅ 사용 가능한 waypoint 발견")
            return lat, lon
    print("❌ 모든 후보 waypoint 실패")
    return None

def test_route_with_waypoint(start, waypoint, end):
    headers = {
        "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID,
        "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET,
        "Content-Type": "application/json"
    }
    body = {
        "start": {"x": str(start[1]), "y": str(start[0]), "name": "출발지"},
        "goal": {"x": str(end[1]), "y": str(end[0]), "name": "도착지"},
        "waypoints": [{"x": str(waypoint[1]), "y": str(waypoint[0]), "name": "해안경유지"}]
    }
    try:
        res = requests.post("https://naveropenapi.apigw.ntruss.com/map-direction/v1/driving", 
                             headers=headers, json=body)
        if res.status_code == 200 and res.json().get("route"):
            return True
    except Exception as e:
        print("❌ 테스트 요청 예외:", e)
    return False

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/route", methods=["POST"])
def route():
    data = request.json
    start_addr = data.get("start")
    end_addr = data.get("end")

    def geocode(addr):
        url = "https://maps.googleapis.com/maps/api/geocode/json"
        params = {"address": addr, "key": os.getenv("GOOGLE_API_KEY")}
        res = requests.get(url, params=params)
        geo = res.json()
        if geo["status"] == "OK":
            loc = geo["results"][0]["geometry"]["location"]
            return loc["lat"], loc["lng"]
        return None

    start = geocode(start_addr)
    end = geocode(end_addr)

    if not start or not end:
        print("❌ 주소 geocoding 실패")
        return jsonify({"error": "❌ 주소 인식 실패"}), 500

    print("\U0001F4CD 출발지 좌표:", start)
    print("\U0001F4CD 도착지 좌표:", end)

    waypoint = get_fallback_waypoint(start, end)
    if not waypoint:
        return jsonify({"error": "❌ 사용할 수 있는 해안 waypoint 없음"}), 500

    headers = {
        "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID,
        "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET,
        "Content-Type": "application/json"
    }
    body = {
        "start": {"x": str(start[1]), "y": str(start[0]), "name": "출발지"},
        "goal": {"x": str(end[1]), "y": str(end[0]), "name": "도착지"},
        "waypoints": [{"x": str(waypoint[1]), "y": str(waypoint[0]), "name": "해안경유지"}]
    }

    try:
        res = requests.post("https://naveropenapi.apigw.ntruss.com/map-direction/v1/driving", 
                            headers=headers, json=body)
        if res.status_code != 200:
            print("❌ 네이버 Directions API 요청 실패", res.text)
            return jsonify({"error": "❌ 경로 계산 실패"}), 500

        result = res.json()
        print("\U0001F4C8 경로 계산 성공")
        return jsonify({
            "start_corrected": start_addr,
            "end_corrected": end_addr,
            "route": result
        })
    except Exception as e:
        print("❌ 예외 발생:", e)
        return jsonify({"error": "❌ 서버 예외 발생"}), 500

if __name__ == '__main__':
    app.run(debug=True, port=10000)
