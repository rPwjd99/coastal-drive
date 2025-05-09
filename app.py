from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import os
import requests
from shapely.geometry import Point, LineString
import geopandas as gpd
from math import radians, cos, sin, asin, sqrt
from dotenv import load_dotenv

app = Flask(__name__)
CORS(app)

# .env 파일에서 API 키 불러오기
load_dotenv()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
ORS_API_KEY = os.getenv("ORS_API_KEY")
TOURAPI_KEY = os.getenv("TOURAPI_KEY")

# 해안선 파일은 루트 디렉터리에 있다고 가정
COASTLINE_PATH = "coastal_route_result.geojson"
coastline_gdf = gpd.read_file(COASTLINE_PATH)

# 거리 계산 함수
def haversine(lon1, lat1, lon2, lat2):
    R = 6371
    lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = sin(dlat/2)**2 + cos(lat1)*cos(lat2)*sin(dlon/2)**2
    c = 2 * asin(sqrt(a))
    return R * c

# 가장 가까운 해안선의 좌표 반환
def find_nearest_coast(lat, lon):
    points = coastline_gdf.geometry.apply(
        lambda geom: geom.interpolate(geom.length / 2) if isinstance(geom, LineString) else None
    ).dropna()
    distances = points.apply(lambda p: haversine(lon, lat, p.x, p.y))
    nearest_point = points.iloc[distances.idxmin()]
    return nearest_point.y, nearest_point.x

# 주소 → 좌표 변환 (Google Geocoding API)
def geocode(address):
    url = f"https://maps.googleapis.com/maps/api/geocode/json"
    params = {"address": address, "key": GOOGLE_API_KEY}
    res = requests.get(url, params=params)
    data = res.json()
    if data["status"] == "OK":
        loc = data["results"][0]["geometry"]["location"]
        return loc["lat"], loc["lng"]
    else:
        raise ValueError(f"Geocoding 실패: {data['status']}")

# 도로 경로 계산 (OpenRouteService)
def get_route(coords):
    url = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
    headers = {"Authorization": ORS_API_KEY, "Content-Type": "application/json"}
    body = {"coordinates": coords}
    res = requests.post(url, headers=headers, json=body)
    if res.status_code == 200:
        return res.json()
    else:
        raise ValueError("경로 계산 실패")

# 관광지 검색 (TourAPI)
def get_tourspots(lat, lon):
    url = "https://apis.data.go.kr/B551011/KorService1/locationBasedList1"
    params = {
        "serviceKey": TOURAPI_KEY,
        "MobileOS": "ETC",
        "MobileApp": "CoastalDrive",
        "mapX": lon,
        "mapY": lat,
        "radius": 5000,
        "numOfRows": 10,
        "pageNo": 1,
        "_type": "json"
    }
    res = requests.get(url, params=params)
    try:
        items = res.json()["response"]["body"]["items"]["item"]
        return [{"title": i["title"], "mapx": float(i["mapx"]), "mapy": float(i["mapy"])} for i in items]
    except:
        return []

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/route", methods=["POST"])
def route():
    try:
        data = request.get_json()
        start = geocode(data["start"])
        end = geocode(data["end"])

        coast_lat, coast_lon = find_nearest_coast(*start)

        # 도로 연결 가능한 지점만 경로 계산 시도
        route_geojson = get_route([
            [start[1], start[0]],
            [coast_lon, coast_lat],
            [end[1], end[0]]
        ])

        tourspots = get_tourspots(end[0], end[1])

        return jsonify({
            "route": route_geojson["features"][0],
            "tourspots": tourspots
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True)
