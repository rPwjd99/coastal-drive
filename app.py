import os
import json
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
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")

COASTLINE_PATH = os.path.join(os.path.dirname(__file__), "coastal_route_result.geojson")
ROAD_CSV_PATH = os.path.join(os.path.dirname(__file__), "road_endpoints_reduced.csv")

coastline = gpd.read_file(COASTLINE_PATH).to_crs(epsg=4326)
road_points = pd.read_csv(ROAD_CSV_PATH)
coastline["centroid"] = coastline.geometry.representative_point()

# 해버사인 거리 계산
def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
    return 2 * R * asin(sqrt(a))

# 주소 → 좌표 변환
def geocode_google(address):
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {"address": address, "key": GOOGLE_API_KEY}
    res = requests.get(url, params=params)
    if res.status_code != 200:
        return None
    try:
        location = res.json()["results"][0]["geometry"]["location"]
        return location["lat"], location["lng"]
    except:
        return None

# 위도/경도 기준 해안선 중심점 선택
def find_coastal_point_by_direction(start_lat, start_lon, end_lat, end_lon):
    lat_diff = abs(start_lat - end_lat)
    lon_diff = abs(start_lon - end_lon)
    use_lat = lat_diff > lon_diff

    if use_lat:
        coastline["dir_diff"] = coastline.centroid.apply(lambda pt: abs(pt.y - start_lat))
    else:
        coastline["dir_diff"] = coastline.centroid.apply(lambda pt: abs(pt.x - start_lon))

    nearest = coastline.sort_values("dir_diff").iloc[0].centroid
    return nearest.y, nearest.x

# 도로 끝점에서 가장 가까운 위치 찾기
def find_nearest_road_point(lat, lon):
    road_points["dist"] = road_points.apply(lambda row: haversine(lat, lon, row["y"], row["x"]), axis=1)
    nearest = road_points.sort_values("dist").iloc[0]
    return nearest["y"], nearest["x"]

# 네이버 경로 계산 API

def get_naver_route(start, waypoint, end):
    url = "https://naveropenapi.apigw.ntruss.com/map-direction/v1/driving"
    headers = {
        "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID,
        "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET,
    }
    params = {
        "start": f"{start[1]},{start[0]}",
        "goal": f"{end[1]},{end[0]}",
        "waypoints": f"{waypoint[1]},{waypoint[0]}",
        "option": "trafast",
        "output": "json"
    }
    res = requests.get(url, headers=headers, params=params)
    if res.status_code != 200:
        return None
    return res.json()

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
        return jsonify({"error": "❌ 주소 → 좌표 변환 실패"}), 400

    coast_lat, coast_lon = find_coastal_point_by_direction(start[0], start[1], end[0], end[1])
    waypoint = find_nearest_road_point(coast_lat, coast_lon)

    route_data = get_naver_route(start, waypoint, end)
    if not route_data:
        return jsonify({"error": "❌ 경로 탐색 실패"}), 500

    # GeoJSON 형태로 반환
    try:
        coords = route_data["route"]["trafast"][0]["path"]
        geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": coords
                    },
                    "properties": {}
                }
            ]
        }
        return jsonify(geojson)
    except:
        return jsonify({"error": "❌ 경로 데이터 파싱 실패"}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
