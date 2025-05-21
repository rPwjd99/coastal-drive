import os
import json
import requests
import pandas as pd
import geopandas as gpd
from flask import Flask, request, jsonify, render_template
from dotenv import load_dotenv
from shapely.geometry import Point
from scipy.spatial import KDTree

load_dotenv()
app = Flask(__name__)

# 환경변수 불러오기
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
NAVER_API_KEY_ID = os.getenv("NAVER_API_KEY_ID")
NAVER_API_KEY_SECRET = os.getenv("NAVER_API_KEY_SECRET")

# 데이터 불러오기
road_points = pd.read_csv("road_endpoints_reduced.csv", low_memory=False)
coastline = gpd.read_file("coastal_route_result.geojson").to_crs(epsg=4326)

# 해안 좌표 추출
coast_coords = []
for geom in coastline.geometry:
    if geom.geom_type == "MultiLineString":
        for part in geom.geoms:
            coast_coords.extend(list(part.coords))
    elif geom.geom_type == "LineString":
        coast_coords.extend(list(geom.coords))
coast_tree = KDTree(coast_coords)

def geocode_google(address):
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {"address": address, "key": GOOGLE_API_KEY}
    res = requests.get(url, params=params)
    try:
        loc = res.json()["results"][0]["geometry"]["location"]
        return loc["lat"], loc["lng"]
    except:
        print("❌ 주소 변환 실패:", address)
        return None

def find_waypoint(start, end, radius_km=5):
    start_lat, start_lon = start
    end_lat, end_lon = end
    direction = "lat" if abs(start_lat - end_lat) > abs(start_lon - end_lon) else "lon"
    rounded = round(start_lat if direction == "lat" else start_lon, 2)
    filtered = road_points[road_points[("y" if direction == "lat" else "x")].round(2) == rounded]
    filtered["dist"] = filtered.apply(lambda row: (end_lat - row["y"])**2 + (end_lon - row["x"])**2, axis=1)
    candidates = filtered.sort_values("dist").head(50)

    waypoints = []
    for _, row in candidates.iterrows():
        pt = [row["x"], row["y"]]
        dist, _ = coast_tree.query(pt)
        if dist < radius_km / 111:  # 5km
            waypoints.append((row["y"], row["x"]))

    return waypoints[0] if waypoints else None

def get_naver_route(start, waypoint, end):
    url = "https://naveropenapi.apigw.ntruss.com/map-direction/v1/driving"
    headers = {
        "X-NCP-APIGW-API-KEY-ID": NAVER_API_KEY_ID,
        "X-NCP-APIGW-API-KEY": NAVER_API_KEY_SECRET
    }
    coords = [f"{start[1]},{start[0]}", f"{waypoint[1]},{waypoint[0]}", f"{end[1]},{end[0]}"]
    waypoints_param = f"waypoints={coords[1]}"
    params = f"start={coords[0]}&goal={coords[2]}&{waypoints_param}&option=trafast"
    res = requests.get(url + "?" + params, headers=headers)
    print("📡 NAVER 응답 코드:", res.status_code)
    try:
        return res.json()
    except Exception as e:
        return {"error": str(e)}

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/route", methods=["POST"])
def route():
    data = request.get_json()
    start_addr = data.get("start")
    end_addr = data.get("end")

    start = geocode_google(start_addr)
    end = geocode_google(end_addr)
    if not start or not end:
        return jsonify({"error": "❌ 주소 변환 실패"}), 400

    waypoint = find_waypoint(start, end)
    if not waypoint:
        return jsonify({"error": "❌ 해안 웨이포인트 없음"}), 500

    route_result = get_naver_route(start, waypoint, end)
    if "route" not in route_result:
        return jsonify({"error": "❌ 경로 계산 실패"}), 502

    coords = route_result["route"]["trafast"][0]["path"]
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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
