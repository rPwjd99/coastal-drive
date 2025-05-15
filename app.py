import os
import requests
import pandas as pd
import geopandas as gpd
from flask import Flask, request, jsonify, render_template
from shapely.geometry import LineString
from math import radians, cos, sin, asin, sqrt
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
ORS_API_KEY = os.getenv("ORS_API_KEY")
print("🔑 ORS 키 앞:", ORS_API_KEY[:6] if ORS_API_KEY else "❌ 없음", flush=True)

# 파일 경로
COASTLINE_PATH = os.path.join(os.path.dirname(__file__), "coastal_route_result.geojson")
ROAD_CSV_PATH = os.path.join(os.path.dirname(__file__), "road_endpoints_reduced.csv")

# 데이터 불러오기
coastline = gpd.read_file(COASTLINE_PATH).to_crs(epsg=4326)
road_points = pd.read_csv(ROAD_CSV_PATH)

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
        loc = res.json()["results"][0]["geometry"]["location"]
        print(f"📍 주소 변환 성공: {address} → {loc}", flush=True)
        return loc["lat"], loc["lng"]
    except:
        print(f"❌ 주소 변환 실패: {address}", flush=True)
        return None

def extract_all_coast_vertices(coastline_gdf):
    coords = []
    for geom in coastline_gdf.geometry:
        if geom.geom_type == "LineString":
            coords.extend(list(geom.coords))
        elif geom.geom_type == "MultiLineString":
            for line in geom:
                coords.extend(list(line.coords))
    return [(pt[1], pt[0]) for pt in coords]  # (lat, lon)

def find_waypoint_near_coast(start, end):
    start_lat, start_lon = start
    end_lat, end_lon = end
    use_lat = abs(start_lat - end_lat) > abs(start_lon - end_lon)

    coast_vertices = extract_all_coast_vertices(coastline)
    filtered = []

    for _, row in road_points.iterrows():
        road_lat, road_lon = row['y'], row['x']
        # 소수점 둘째자리 기준 유사 방향
        if use_lat and round(road_lat, 2) != round(start_lat, 2):
            continue
        if not use_lat and round(road_lon, 2) != round(start_lon, 2):
            continue
        for clat, clon in coast_vertices:
            dist = haversine(road_lat, road_lon, clat, clon)
            if dist <= 1.0:
                filtered.append((road_lat, road_lon))
                break

    if not filtered:
        print("❌ 1km 이내 해안도로점 없음", flush=True)
        return None

    best = min(filtered, key=lambda p: haversine(p[0], p[1], end_lat, end_lon))
    print(f"📍 선택된 waypoint: {best}", flush=True)
    return best

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
        start = geocode_google(data.get("start"))
        end = geocode_google(data.get("end"))
        if not start or not end:
            return jsonify({"error": "❌ 주소 변환 실패"}), 400

        waypoint = find_waypoint_near_coast(start, end)
        if not waypoint:
            return jsonify({"error": "❌ 해안도로 경유지 없음"}), 500

        route_data, status = get_ors_route(start, waypoint, end)
        if "error" in route_data:
            return jsonify({"error": f"❌ 경로 요청 실패: {route_data.get('error')}" }), status

        return jsonify(route_data)
    except Exception as e:
        print("❌ 서버 오류:", str(e), flush=True)
        return jsonify({"error": f"❌ 서버 내부 오류: {str(e)}"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
