import os
import requests
import pandas as pd
import geopandas as gpd
from flask import Flask, request, jsonify, render_template
from shapely.geometry import LineString, MultiLineString
from math import radians, cos, sin, asin, sqrt
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
ORS_API_KEY = os.getenv("ORS_API_KEY")

print("🔑 ORS 키 앞:", ORS_API_KEY[:6] if ORS_API_KEY else "❌ 없음", flush=True)

COASTLINE_PATH = os.path.join(os.path.dirname(__file__), "coastal_route_result.geojson")
ROAD_CSV_PATH = os.path.join(os.path.dirname(__file__), "road_endpoints_reduced.csv")

try:
    coastline_gdf = gpd.read_file(COASTLINE_PATH).to_crs(epsg=4326)
    road_points = pd.read_csv(ROAD_CSV_PATH)
except Exception as e:
    print("❌ 파일 로딩 실패:", e)
    coastline_gdf = gpd.GeoDataFrame()
    road_points = pd.DataFrame()

def haversine(lat1, lon1, lat2, lon2):
    R = 6371  # km
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
    return 2 * R * asin(sqrt(a))

def extract_all_vertices(gdf):
    vertices = []
    for geom in gdf.geometry:
        if isinstance(geom, LineString):
            vertices.extend(list(geom.coords))
        elif isinstance(geom, MultiLineString):
            for part in geom.geoms:
                vertices.extend(list(part.coords))
    return [(round(lat, 6), round(lon, 6)) for lon, lat in vertices]

all_coast_points = extract_all_vertices(coastline_gdf)

def geocode_google(address):
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    res = requests.get(url, params={"address": address, "key": GOOGLE_API_KEY})
    try:
        location = res.json()["results"][0]["geometry"]["location"]
        return location["lat"], location["lng"]
    except:
        print(f"❌ 주소 변환 실패: {address}")
        return None

def find_waypoint(start_lat, start_lon, end_lat, end_lon):
    use_lat = abs(start_lat - end_lat) > abs(start_lon - end_lon)
    road_candidates = []

    for _, row in road_points.iterrows():
        ry, rx = row["y"], row["x"]
        for cy, cx in all_coast_points:
            if haversine(ry, rx, cy, cx) <= 1:
                if use_lat:
                    if round(ry, 2) == round(start_lat, 2):
                        road_candidates.append((ry, rx))
                else:
                    if round(rx, 2) == round(start_lon, 2):
                        road_candidates.append((ry, rx))
                break

    if not road_candidates:
        return None

    def score(pt):
        return haversine(pt[0], pt[1], end_lat, end_lon)

    road_candidates.sort(key=score)
    return road_candidates[0]

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
    try:
        return res.json(), res.status_code
    except:
        return {"error": "❌ 응답 파싱 오류"}, 500

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

    waypoint = find_waypoint(start[0], start[1], end[0], end[1])
    if not waypoint:
        return jsonify({"error": "❌ 해안도로 경유지 탐색 실패"}), 500

    route_data, status = get_ors_route(start, waypoint, end)
    if "error" in route_data:
        return jsonify({"error": route_data["error"]}), status

    return jsonify(route_data)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
