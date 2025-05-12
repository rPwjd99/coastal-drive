import os
import json
import requests
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import geopandas as gpd
from shapely.geometry import LineString, Point

NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")
TOURAPI_KEY = os.getenv("TOURAPI_KEY")

app = Flask(__name__)
CORS(app)

# 해안선 GeoJSON 로드 및 대표 포인트 추출
coastline_path = "coastal_route_result.geojson"
coastline = gpd.read_file(coastline_path)

# 해안선의 중간 지점을 대표 포인트로 추출
def extract_midpoint(geom):
    if geom.geom_type == "LineString":
        return geom.interpolate(0.5, normalized=True)
    elif geom.geom_type == "MultiLineString":
        return geom[0].interpolate(0.5, normalized=True)
    elif geom.geom_type == "Point":
        return geom
    else:
        return geom.representative_point()

coastline["midpoint"] = coastline.geometry.apply(extract_midpoint)

# 주소 → 좌표 (VWorld 또는 Google Maps 가능)
def geocode_address(address):
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {
        "address": address,
        "key": os.getenv("GOOGLE_MAPS_API_KEY")
    }
    res = requests.get(url, params=params)
    data = res.json()
    if data["status"] == "OK":
        location = data["results"][0]["geometry"]["location"]
        return location["lat"], location["lng"]
    return None

# 가장 가까운 해안선 포인트 선택
def find_nearest_waypoint(start_lat, start_lon):
    from shapely.ops import nearest_points
    start_point = Point(start_lon, start_lat)
    # 거리 계산
    distances = coastline["midpoint"].distance(start_point)
    closest_idx = distances.idxmin()
    if closest_idx is None:
        return None
    pt = coastline.loc[closest_idx, "midpoint"]
    return pt.y, pt.x  # 위도, 경도 순

# 네이버 길찾기
def get_route_via_naver(start, waypoint, end):
    headers = {
        "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID,
        "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET,
        "Content-Type": "application/json"
    }
    coords = [start[::-1]] + [waypoint[::-1]] + [end[::-1]]  # 경도, 위도 순
    body = {
        "start": {"x": coords[0][0], "y": coords[0][1], "name": "출발지"},
        "goal": {"x": coords[2][0], "y": coords[2][1], "name": "도착지"},
        "waypoints": [{"x": coords[1][0], "y": coords[1][1], "name": "해안"}]
    }
    res = requests.post(
        "https://naveropenapi.apigw.ntruss.com/map-direction/v1/driving",
        headers=headers,
        data=json.dumps(body)
    )
    if res.status_code == 200:
        return res.json()
    return None

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/route", methods=["POST"])
def route():
    try:
        data = request.get_json()
        start_address = data.get("start")
        end_address = data.get("end")

        start = geocode_address(start_address)
        end = geocode_address(end_address)

        if not start or not end:
            return jsonify({"error": "❌ 주소 인식 실패"}), 400

        waypoint = find_nearest_waypoint(*start)
        if not waypoint:
            return jsonify({"error": "❌ 해안 경유지 탐색 실패"}), 404

        route_result = get_route_via_naver(start, waypoint, end)
        if not route_result:
            return jsonify({"error": "❌ 경로 탐색 실패"}), 500

        return jsonify(route_result)

    except Exception as e:
        return jsonify({"error": f"❌ 서버 오류: {str(e)}"}), 500

@app.route("/tourspot", methods=["GET"])
def tourspot():
    lat = request.args.get("lat")
    lon = request.args.get("lon")
    url = f"https://apis.data.go.kr/B551011/KorService1/locationBasedList1"
    params = {
        "MobileOS": "ETC",
        "MobileApp": "CoastalDrive",
        "mapX": lon,
        "mapY": lat,
        "radius": 5000,
        "numOfRows": 20,
        "pageNo": 1,
        "arrange": "Q",
        "contentTypeId": 12,
        "serviceKey": TOURAPI_KEY,
        "_type": "json"
    }
    res = requests.get(url, params=params)
    return jsonify(res.json())

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
