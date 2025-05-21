import os
import json
import requests
import pandas as pd
import geopandas as gpd
import numpy as np
from shapely.geometry import Point, LineString, MultiLineString
from scipy.spatial import KDTree
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)
CORS(app)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
NAVER_API_KEY_ID = os.getenv("NAVER_API_KEY_ID")
NAVER_API_KEY_SECRET = os.getenv("NAVER_API_KEY_SECRET")

# Load road and coastline data
road_points = pd.read_csv("road_endpoints_reduced.csv", low_memory=False)
road_points["x"] = pd.to_numeric(road_points["x"], errors="coerce")
road_points["y"] = pd.to_numeric(road_points["y"], errors="coerce")

print("[✅ 도로 끝점 데이터]")
print(road_points.head())
print("칼럼:", road_points.columns)
print(road_points.dtypes)

coastline = gpd.read_file("coastal_route_result.geojson")
print("[✅ 해안선 데이터]")
print(coastline.head())
print("CRS:", coastline.crs)

# 변환: MULTILINESTRING 해안선 → 포인트 배열
coast_coords = []
for geom in coastline.geometry:
    if isinstance(geom, LineString):
        coast_coords.extend(list(geom.coords))
    elif isinstance(geom, MultiLineString):
        for part in geom.geoms:
            coast_coords.extend(list(part.coords))
coast_coords = np.array(coast_coords)
print("✅ 변환된 coast_coords.shape:", coast_coords.shape)

# KDTree 구축
coast_tree = KDTree(coast_coords)

def geocode_google(address):
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    res = requests.get(url, params={"address": address, "key": GOOGLE_API_KEY})
    try:
        result = res.json()["results"][0]["geometry"]["location"]
        return result["lat"], result["lng"]
    except:
        print("❌ 주소 변환 실패:", address)
        return None

def find_waypoint_near_coast(start, end, radius_km=3):
    start_lat, start_lon = start
    end_lat, end_lon = end
    direction_lat = abs(end_lat - start_lat) > abs(end_lon - start_lon)

    candidates = road_points.copy()
    candidates["dist_to_end"] = np.sqrt((candidates["y"] - end_lat)**2 + (candidates["x"] - end_lon)**2)
    candidates = candidates.sort_values("dist_to_end").head(300)

    nearby = []
    for _, row in candidates.iterrows():
        point = [row["x"], row["y"]]
        dist_deg, _ = coast_tree.query(point)
        dist_km = dist_deg * 111
        if dist_km <= radius_km:
            nearby.append((row["y"], row["x"]))
    if not nearby:
        print("❌ 해안 웨이포인트 없음")
        return None
    selected = sorted(nearby, key=lambda pt: np.sqrt((pt[0] - end_lat)**2 + (pt[1] - end_lon)**2))[0]
    print("✅ 선택된 웨이포인트:", selected)
    return selected

def get_naver_route(start, waypoint, end):
    url = "https://naveropenapi.apigw.ntruss.com/map-direction/v1/driving"
    coords = f"{start[1]},{start[0]}|{waypoint[1]},{waypoint[0]}|{end[1]},{end[0]}"
    headers = {
        "X-NCP-APIGW-API-KEY-ID": NAVER_API_KEY_ID,
        "X-NCP-APIGW-API-KEY": NAVER_API_KEY_SECRET
    }
    res = requests.get(url, headers=headers, params={"start": coords.split('|')[0],
                                                     "goal": coords.split('|')[-1],
                                                     "waypoints": coords.split('|')[1]})
    data = res.json()
    print("[DEBUG] 📡 네이버 응답 JSON:")
    print(json.dumps(data, indent=2, ensure_ascii=False))

    try:
        route = data["route"]["traoptimal"][0]["path"]
        coords = [[pt[0], pt[1]] for pt in route]
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
        print("[DEBUG] ✅ 생성된 GeoJSON 응답 구조:")
        print(json.dumps(geojson, indent=2, ensure_ascii=False))
        return geojson
    except Exception as e:
        return {"error": "❌ GeoJSON 변환 실패", "detail": str(e)}

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
        if "error" in route_data:
            return jsonify(route_data), 500

        return jsonify(route_data)
    except Exception as e:
        print("❌ 서버 오류:", e)
        return jsonify({"error": f"❌ 서버 내부 오류: {str(e)}"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
