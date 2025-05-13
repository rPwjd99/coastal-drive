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
road_points = pd.read_csv(ROAD_CSV_PATH, low_memory=False)

# 해버사인 거리 계산
def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
    return 2 * R * asin(sqrt(a))

# 주소 → 좌표 변환 with 다양한 조건

def geocode_google(address):
    base_url = "https://maps.googleapis.com/maps/api/geocode/json"
    queries = [
        address,
        address + " 도로명주소",
        address + " 지번주소",
        address + " 건물명",
        address + " POI",
        address + " 업체명",
        address + " 대한민국"
    ]
    for q in queries:
        res = requests.get(base_url, params={"address": q, "key": GOOGLE_API_KEY})
        if res.status_code != 200:
            continue
        try:
            location = res.json()["results"][0]["geometry"]["location"]
            print("📍 주소 변환 성공:", q, "→", location)
            return location["lat"], location["lng"]
        except:
            continue
    print("❌ 모든 주소 변환 실패:", address)
    return None

# 방향성 기반 도로점 탐색
def find_directional_road_point(start_lat, start_lon, end_lat, end_lon):
    lat_diff = abs(start_lat - end_lat)
    lon_diff = abs(start_lon - end_lon)
    use_lat = lat_diff > lon_diff

    if use_lat:
        road_points["dir_diff"] = road_points["y"].apply(lambda y: abs(y - start_lat))
    else:
        road_points["dir_diff"] = road_points["x"].apply(lambda x: abs(x - start_lon))

    road_points["dist_to_end"] = road_points.apply(
        lambda row: haversine(row["y"], row["x"], end_lat, end_lon), axis=1
    )

    candidate = road_points.sort_values(["dir_diff", "dist_to_end"]).iloc[0]
    return candidate["y"], candidate["x"]

# 네이버 경로 탐색
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
    print("📡 네이버 응답코드:", res.status_code)
    if res.status_code != 200:
        print("❌ 응답 오류:", res.text)
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

    print("📨 요청 주소:", start_addr, "→", end_addr)

    start = geocode_google(start_addr)
    end = geocode_google(end_addr)
    if not start or not end:
        return jsonify({"error": "❌ 주소 → 좌표 변환 실패"}), 400

    waypoint = find_directional_road_point(start[0], start[1], end[0], end[1])
    print("✅ 선택된 waypoint:", waypoint)

    route_data = get_naver_route(start, waypoint, end)
    if not route_data:
        return jsonify({"error": "❌ 경로 탐색 실패"}), 500

    try:
        coords = route_data["route"]["trafast"][0]["path"]
        print("✅ 경로 좌표 개수:", len(coords))
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
    except Exception as e:
        print("❌ GeoJSON 변환 실패:", e)
        return jsonify({"error": "❌ 경로 데이터 파싱 실패"}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
