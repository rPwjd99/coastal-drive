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
ORS_API_KEY = os.getenv("ORS_API_KEY")

print("\U0001f511 ORS 키 앞:", ORS_API_KEY[:6] if ORS_API_KEY else "❌ 없음", flush=True)

COASTLINE_PATH = os.path.join(os.path.dirname(__file__), "coastal_route_result.geojson")
ROAD_CSV_PATH = os.path.join(os.path.dirname(__file__), "road_endpoints_reduced.csv")

# 데이터 로딩
coastline = gpd.read_file(COASTLINE_PATH).to_crs(epsg=4326)
road_points = pd.read_csv(ROAD_CSV_PATH, low_memory=False)

# 거리 계산 함수
def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * R * asin(sqrt(a))

# 해안선 vertex 추출
def extract_all_coast_vertices(geo_df):
    coords = []
    for geom in geo_df.geometry:
        if geom.geom_type == "LineString":
            coords.extend(list(geom.coords))
        elif geom.geom_type == "MultiLineString":
            for line in geom.geoms:
                coords.extend(list(line.coords))
    return [(lat, lon) for lon, lat in coords]

# 해안과의 거리 계산
def mark_near_coast(road_df, coast_coords):
    def is_within_5km(row):
        for coast_lat, coast_lon in coast_coords:
            if haversine(row['y'], row['x'], coast_lat, coast_lon) <= 5:
                return True
        return False
    road_df["near_coast"] = road_df.apply(is_within_5km, axis=1)

coast_coords_latlon = extract_all_coast_vertices(coastline)
mark_near_coast(road_points, coast_coords_latlon)

# 주소 → 좌표 변환
def geocode_google(address):
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    res = requests.get(url, params={"address": address, "key": GOOGLE_API_KEY})
    try:
        location = res.json()["results"][0]["geometry"]["location"]
        print("\U0001f4cd 주소 변환 성공:", address, "→", location, flush=True)
        return location["lat"], location["lng"]
    except:
        print("❌ 주소 변환 실패:", address, flush=True)
        return None

# 해안 경유지 탐색
def find_best_waypoint(start, end):
    start_lat, start_lon = start
    end_lat, end_lon = end
    use_lat = abs(start_lat - end_lat) > abs(start_lon - end_lon)

    def is_in_direction(row):
        if use_lat:
            return (end_lon - start_lon) * (row['x'] - start_lon) > 0
        else:
            return (end_lat - start_lat) * (row['y'] - start_lat) > 0

    candidates = road_points[(road_points["near_coast"]) & (road_points.apply(is_in_direction, axis=1))].copy()
    if candidates.empty:
        print("❌ 해안 근접 도로 없음", flush=True)
        return None

    candidates["dist_to_end"] = candidates.apply(lambda row: haversine(row["y"], row["x"], end_lat, end_lon), axis=1)
    selected = candidates.sort_values("dist_to_end").iloc[0]
    return selected["y"], selected["x"]

# 경로 요청
def get_ors_route(start, waypoint, end):
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
    print("\U0001f4e1 ORS 응답코드:", res.status_code, flush=True)
    try:
        return res.json(), res.status_code
    except Exception as e:
        return {"error": str(e)}, 500

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

    waypoint = find_best_waypoint(start, end)
    if not waypoint:
        return jsonify({"error": "❌ 해안 경유지 탐색 실패"}), 500

    route_data, status = get_ors_route(start, waypoint, end)
    if "error" in route_data:
        return jsonify({"error": f"❌ 경로 요청 실패: {route_data.get('error')}"}), status

    return jsonify(route_data)

if __name__ == "__main__":
    PORT = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=PORT)
