import os
import json
import requests
import pandas as pd
import geopandas as gpd
from flask import Flask, request, jsonify, render_template
from dotenv import load_dotenv
from shapely.geometry import Point, LineString, MultiLineString
from scipy.spatial import KDTree

load_dotenv()
app = Flask(__name__)

# 환경변수 불러오기
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
NAVER_API_KEY_ID = os.getenv("NAVER_API_KEY_ID")
NAVER_API_KEY_SECRET = os.getenv("NAVER_API_KEY_SECRET")

# 데이터 불러오기
road_points = pd.read_csv("road_endpoints_reduced.csv", low_memory=False)
road_points["x"] = pd.to_numeric(road_points["x"], errors="coerce")
road_points["y"] = pd.to_numeric(road_points["y"], errors="coerce")

coastline = gpd.read_file("coastal_route_result.geojson")
coastline = coastline.to_crs(epsg=4326)

# 유효한 geometry만 추출
valid_geometries = coastline[coastline.geometry.notnull()]
coast_coords = []
for geom in valid_geometries.geometry:
    if isinstance(geom, LineString):
        coast_coords.extend(list(geom.coords))
    elif isinstance(geom, MultiLineString):
        for part in geom.geoms:
            coast_coords.extend(list(part.coords))

coast_coords = [pt for pt in coast_coords if isinstance(pt, tuple) and len(pt) == 2]
coast_coords = pd.DataFrame(coast_coords, columns=["lon", "lat"]).dropna().values
coast_tree = KDTree(coast_coords)

# 주소 → 좌표 변환
def geocode_google(address):
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {"address": address, "key": GOOGLE_API_KEY}
    try:
        res = requests.get(url, params=params)
        location = res.json()["results"][0]["geometry"]["location"]
        return location["lat"], location["lng"]
    except Exception as e:
        print("❌ 주소 변환 실패:", address, e)
        return None

# 웨이포인트 탐색
def find_waypoint_near_coast(start, end, radius_km=10):
    candidates = []
    for _, row in road_points.iterrows():
        px, py = row["x"], row["y"]
        if pd.isna(px) or pd.isna(py):
            continue
        dist, _ = coast_tree.query([px, py])
        if dist < radius_km / 111:  # 약 10km 이내
            candidates.append(((py, px), dist))
    if not candidates:
        print("❌ 해안 웨이포인트 없음")
        return None
    candidates.sort(key=lambda x: x[1])
    return candidates[0][0]  # (lat, lon)

# NAVER Directions API
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
        "format": "json"
    }
    try:
        res = requests.get(url, headers=headers, params=params)
        data = res.json()
        print("✅ NAVER 응답:", json.dumps(data, indent=2))
        return data
    except Exception as e:
        print("❌ NAVER 경로 요청 실패:", e)
        return {"error": str(e)}

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

        waypoint = find_waypoint_near_coast(start, end)
        if not waypoint:
            return jsonify({"error": "❌ 해안 웨이포인트 없음"}), 500

        route_data = get_naver_route(start, waypoint, end)
        if "route" not in route_data or "trafast" not in route_data["route"]:
            return jsonify({"error": "❌ 경로 계산 실패"}), 502

        coords = route_data["route"]["trafast"][0]["path"]
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
        print("❌ 서버 오류:", e)
        return jsonify({"error": f"❌ 서버 오류: {str(e)}"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
