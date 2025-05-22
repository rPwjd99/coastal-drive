import os
import requests
import pandas as pd
import geopandas as gpd
from flask import Flask, request, jsonify, render_template
from shapely.geometry import Point
from shapely.errors import ShapelyDeprecationWarning
import warnings

warnings.filterwarnings("ignore", category=ShapelyDeprecationWarning)

app = Flask(__name__)

# API keys
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# File paths
BASE_DIR = os.path.dirname(__file__)
CSV_PATH = os.path.join(BASE_DIR, "road_endpoints_reduced.csv")
GEOJSON_PATH = os.path.join(BASE_DIR, "coastal_route_result.geojson")

# Load road endpoints
try:
    road_points = pd.read_csv(CSV_PATH, low_memory=False)
    assert {'x', 'y'}.issubset(road_points.columns)
    print("✅ CSV 파일 로딩 성공: road_endpoints_reduced.csv")
except Exception as e:
    print("❌ CSV 로딩 오류:", str(e))
    road_points = pd.DataFrame(columns=['x', 'y'])

# Load coastline geojson
try:
    coast_gdf = gpd.read_file(GEOJSON_PATH).to_crs(epsg=5181)
    print("✅ GeoJSON 파일 로딩 성공: coastal_route_result.geojson")
except Exception as e:
    print("❌ GeoJSON 파일 로딩 오류:", str(e))
    coast_gdf = gpd.GeoDataFrame()

poi_aliases = {
    "세종시청": "세종특별자치시 한누리대로 2130",
    "속초시청": "강원도 속초시 중앙로 183",
    "서울역": "서울특별시 중구 한강대로 405",
    "대전역": "대전광역시 동구 중앙로 215"
}

def haversine(lat1, lon1, lat2, lon2):
    from math import radians, cos, sin, asin, sqrt
    R = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2)**2
    return 2 * R * asin(sqrt(a))

def geocode_naver(address):
    address = poi_aliases.get(address, address)
    url = "https://naveropenapi.apigw.ntruss.com/map-geocode/v2/geocode"
    headers = {
        "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID,
        "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET
    }
    params = {"query": address}
    res = requests.get(url, headers=headers, params=params)
    try:
        item = res.json()["addresses"][0]
        lat = float(item["y"])
        lon = float(item["x"])
        print("📍 NAVER 주소 변환 성공:", address, "→", lat, lon)
        return lat, lon
    except:
        print("❌ NAVER 주소 변환 실패:", address)
        return None

def geocode_google(address):
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    res = requests.get(url, params={"address": address, "key": GOOGLE_API_KEY})
    try:
        location = res.json()["results"][0]["geometry"]["location"]
        print("📍 Google 주소 변환 성공:", address, "→", location)
        return location["lat"], location["lng"]
    except:
        print("❌ Google 주소 변환 실패:", address)
        return None

def geocode(address):
    result = geocode_naver(address)
    if result:
        return result
    print("➡️ NAVER 실패, Google 시도 중...")
    return geocode_google(address)

def is_within_3km_of_coast(lat, lon):
    point = gpd.GeoSeries([Point(lon, lat)], crs="EPSG:4326").to_crs(epsg=5181)
    return coast_gdf.buffer(3000).contains(point.iloc[0]).any()

def find_nearest_road_point(start, end):
    start_lat, start_lon = start
    end_lat, end_lon = end
    use_lat = abs(start_lat - end_lat) > abs(start_lon - end_lon)
    rounded_lat = round(start_lat, 2)
    rounded_lon = round(start_lon, 2)

    if use_lat:
        candidates = road_points[road_points["y"].round(2) == rounded_lat]
        direction = lambda row: (end_lon - start_lon) * (row["x"] - start_lon) > 0
    else:
        candidates = road_points[road_points["x"].round(2) == rounded_lon]
        direction = lambda row: (end_lat - start_lat) * (row["y"] - start_lat) > 0

    candidates = candidates[candidates.apply(direction, axis=1)]
    candidates["dist_to_end"] = candidates.apply(
        lambda row: haversine(row["y"], row["x"], end_lat, end_lon), axis=1
    )

    candidates = candidates.sort_values("dist_to_end")
    for _, row in candidates.iterrows():
        if is_within_3km_of_coast(row["y"], row["x"]):
            print("📍 선택된 waypoint:", row["y"], row["x"])
            return row["y"], row["x"]

    print("❌ 조건 만족하는 waypoint 없음")
    return None

def get_naver_route(start, waypoint, end):
    return {"message": "경로 계산은 구현 필요"}, 501

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/route", methods=["POST"])
def route():
    try:
        data = request.get_json()
        start = geocode(data.get("start"))
        end = geocode(data.get("end"))
        if not start or not end:
            return jsonify({"error": "❌ 주소 변환 실패"}), 400

        waypoint = find_nearest_road_point(start, end)
        if not waypoint:
            return jsonify({"error": "❌ 경유지 탐색 실패"}), 500

        route_data, status = get_naver_route(start, waypoint, end)
        if "error" in route_data:
            return jsonify({"error": f"❌ 경로 요청 실패: {route_data.get('error')}" }), status

        return jsonify(route_data)
    except Exception as e:
        print("❌ 서버 오류:", str(e))
        return jsonify({"error": f"❌ 서버 내부 오류: {str(e)}"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
