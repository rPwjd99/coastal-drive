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

print("🔑 ORS 키 앞:", ORS_API_KEY[:6])

# Geo 데이터 경로
COASTLINE_PATH = os.path.join(os.path.dirname(__file__), "coastal_route_result.geojson")
ROAD_CSV_PATH = os.path.join(os.path.dirname(__file__), "road_endpoints_reduced.csv")

coastline = gpd.read_file(COASTLINE_PATH).to_crs(epsg=4326)
road_points = pd.read_csv(ROAD_CSV_PATH, low_memory=False)

def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2)**2
    return 2 * R * asin(sqrt(a))

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

def find_directional_road_point(start_lat, start_lon, end_lat, end_lon):
    lat_diff = abs(start_lat - end_lat)
    lon_diff = abs(start_lon - end_lon)
    use_lat = lat_diff > lon_diff
    road_points["dir_diff"] = road_points["y"].apply(lambda y: abs(y - start_lat)) if use_lat else road_points["x"].apply(lambda x: abs(x - start_lon))
    road_points["dist_to_end"] = road_points.apply(
        lambda row: haversine(row["y"], row["x"], end_lat, end_lon), axis=1
    )
    candidate = road_points.sort_values(["dir_diff", "dist_to_end"]).iloc[0]
    return candidate["y"], candidate["x"]

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
    print("📡 ORS 응답코드:", res.status_code)
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

    waypoint = find_directional_road_point(start[0], start[1], end[0], end[1])
    if not waypoint:
        return jsonify({"error": "❌ 해안 경유지 탐색 실패"}), 500

    route_data, status = get_ors_route(start, waypoint, end)
    if "error" in route_data:
        return jsonify({"error": f"❌ 경로 요청 실패: {route_data.get('error')}" }), status

    return jsonify(route_data)

if __name__ == "__main__":
    PORT = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=PORT)
