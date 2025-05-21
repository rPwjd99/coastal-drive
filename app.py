import os
import requests
import pandas as pd
import numpy as np
from flask import Flask, request, jsonify, render_template
from shapely.geometry import Point, LineString, MultiLineString
from scipy.spatial import KDTree
import geopandas as gpd
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# Load API keys
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# Load road endpoint data
road_points = pd.read_csv("road_endpoints_reduced.csv", low_memory=False)
print("[✅ 도로 끝점 데이터]")
print(road_points.head())
print("칼럼:", road_points.columns)
print(road_points.dtypes)

# Ensure coordinate types are numeric
road_points["x"] = pd.to_numeric(road_points["x"], errors="coerce")
road_points["y"] = pd.to_numeric(road_points["y"], errors="coerce")

# Load coastal geojson
coastline = gpd.read_file("coastal_route_result.geojson")
print("[✅ 해안선 데이터]")
print(coastline.head())
print("CRS:", coastline.crs)

# Convert coastline geometry to coordinate array
coast_coords = []
for geom in coastline.geometry:
    if isinstance(geom, LineString):
        coast_coords.extend(list(geom.coords))
    elif isinstance(geom, MultiLineString):
        for part in geom.geoms:
            coast_coords.extend(list(part.coords))

coast_coords = np.array(coast_coords)
print(f"✅ 변환된 coast_coords.shape: {coast_coords.shape}")
coast_tree = KDTree(coast_coords)


def geocode_google(address):
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    res = requests.get(url, params={"address": address, "key": GOOGLE_API_KEY})
    try:
        location = res.json()["results"][0]["geometry"]["location"]
        print("📍 주소 변환 성공:", address, "→", location)
        return location["lat"], location["lng"]
    except:
        print("❌ 주소 변환 실패:", address)
        return None


def find_coastal_waypoint(start, end):
    lat1, lon1 = start
    lat2, lon2 = end
    use_lat = abs(lat1 - lat2) > abs(lon1 - lon2)

    rounded = round(lat1, 2) if use_lat else round(lon1, 2)
    candidates = road_points[road_points["y"].round(2) == rounded] if use_lat else road_points[road_points["x"].round(2) == rounded]

    # 해안과의 거리 조건 추가
    nearby = []
    for _, row in candidates.iterrows():
        pt = np.array([row["x"], row["y"]])
        dist, _ = coast_tree.query(pt)
        if dist < 0.027:  # 약 3km
            nearby.append((row["y"], row["x"]))

    if not nearby:
        print("❌ 해안 웨이포인트 없음")
        return None

    def haversine(lat1, lon1, lat2, lon2):
        R = 6371
        dlat = np.radians(lat2 - lat1)
        dlon = np.radians(lon2 - lon1)
        a = np.sin(dlat / 2)**2 + np.cos(np.radians(lat1)) * np.cos(np.radians(lat2)) * np.sin(dlon / 2)**2
        return 2 * R * np.arcsin(np.sqrt(a))

    waypoint = sorted(nearby, key=lambda pt: haversine(pt[0], pt[1], lat2, lon2))[0]
    print("✅ 선택된 waypoint:", waypoint)
    return waypoint


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

        waypoint = find_coastal_waypoint(start, end)
        if not waypoint:
            return jsonify({"error": "❌ 해안 경유지 없음"}), 500

        return jsonify({
            "start": start,
            "waypoint": waypoint,
            "end": end
        })

    except Exception as e:
        print("❌ 서버 오류:", str(e))
        return jsonify({"error": f"❌ 서버 내부 오류: {str(e)}"}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
