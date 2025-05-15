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

# 파일 경로 설정
BASE_DIR = os.getcwd()
COASTLINE_PATH = os.path.join(BASE_DIR, "coastal_route_result.geojson")
ROAD_CSV_PATH = os.path.join(BASE_DIR, "road_endpoints_reduced.csv")

# 데이터 불러오기
try:
    coastline_gdf = gpd.read_file(COASTLINE_PATH).to_crs(epsg=4326)
    road_points = pd.read_csv(ROAD_CSV_PATH)
except Exception as e:
    print("❌ 파일 로딩 오류:", str(e), flush=True)
    coastline_gdf = gpd.GeoDataFrame()
    road_points = pd.DataFrame()

def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
    return 2 * R * asin(sqrt(a))

def geocode_google(address):
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    res = requests.get(url, params={"address": address, "key": GOOGLE_API_KEY})
    try:
        location = res.json()["results"][0]["geometry"]["location"]
        print(f"📍 주소 변환 성공: {address} → {location}", flush=True)
        return location["lat"], location["lng"]
    except:
        print(f"❌ 주소 변환 실패: {address}", flush=True)
        return None

def extract_vertices(gdf):
    coords = []
    for geom in gdf.geometry:
        if isinstance(geom, LineString):
            coords.extend(list(geom.coords))
        elif isinstance(geom, MultiLineString):
            for line in geom.geoms:
                coords.extend(list(line.coords))
    return [(lat, lon) for lon, lat in coords]

def find_best_waypoint(start, end):
    if road_points.empty or coastline_gdf.empty:
        return None

    start_lat, start_lon = start
    end_lat, end_lon = end

    coast_points = extract_vertices(coastline_gdf)

    def is_near_coast(row):
        return any(haversine(row['y'], row['x'], lat, lon) <= 1.0 for lat, lon in coast_points)

    def is_similar_lat_or_lon(row):
        return round(row['y'], 2) == round(start_lat, 2) or round(row['x'], 2) == round(start_lon, 2)

    candidates = road_points[
        road_points.apply(is_near_coast, axis=1) & road_points.apply(is_similar_lat_or_lon, axis=1)
    ]

    if candidates.empty:
        print("❌ 경유지 후보 없음", flush=True)
        return None

    candidates["dist_to_end"] = candidates.apply(
        lambda row: haversine(row["y"], row["x"], end_lat, end_lon), axis=1
    )
    best = candidates.sort_values("dist_to_end").iloc[0]
    print("📍 선택된 waypoint:", best["y"], best["x"], flush=True)
    return best["y"], best["x"]

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
    print("📡 ORS 응답코드:", res.status_code, flush=True)
    try:
        return res.json(), res.status_code
    except Exception as e:
        return {"error": str(e)}, 500

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/route", methods=["POST"])
def route():
    try:
        data = request.get_json()
        start_addr = data.get("start")
        end_addr = data.get("end")
        start = geocode_google(start_addr)
        end = geocode_google(end_addr)
        if not start or not end:
            return jsonify({"error": "❌ 주소 변환 실패"}), 400

        waypoint = find_best_waypoint(start, end)
        if not waypoint:
            return jsonify({"error": "❌ 경유지 없음"}), 500

        route_data, status = get_ors_route(start, waypoint, end)
        if "error" in route_data:
            return jsonify({"error": f"❌ 경로 요청 실패: {route_data['error']}" }), status

        return jsonify(route_data)
    except Exception as e:
        print("❌ 서버 내부 오류:", str(e), flush=True)
        return jsonify({"error": f"❌ 서버 내부 오류: {str(e)}"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
