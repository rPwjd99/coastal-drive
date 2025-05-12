import os
import json
import requests
import geopandas as gpd
import pandas as pd
from flask import Flask, request, jsonify, render_template
from shapely.geometry import Point
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# 네이버 Directions API 정보
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID", "vsdzf1f4n5")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET", "0gzctO51PUTVv0gUZU025JYNHPTmVzLS9sGbfYBM")

# GeoJSON 경로
COASTLINE_PATH = "coastal_route_result.geojson"

# 구글 주소 → 좌표 변환 (VWorld 또는 Google API로 대체 가능)
def geocode_address(address):
    url = f"https://maps.googleapis.com/maps/api/geocode/json"
    params = {
        "address": address,
        "key": os.getenv("GOOGLE_API_KEY")
    }
    response = requests.get(url, params=params)
    data = response.json()
    if data['status'] == 'OK':
        loc = data['results'][0]['geometry']['location']
        return loc['lat'], loc['lng']
    else:
        return None

# LineString을 Point로 추출
def extract_waypoints_from_lines(coastline_gdf, interval=1000):
    waypoints = []
    for line in coastline_gdf.geometry:
        if line.geom_type == 'LineString':
            for i in range(0, int(line.length), interval):
                point = line.interpolate(i)
                waypoints.append(Point(point.x, point.y))
    return gpd.GeoDataFrame(geometry=waypoints, crs=coastline_gdf.crs)

# 해안 경유지 탐색
def find_nearest_waypoint(start_lat, start_lon, end_lat, end_lon):
    print("📌 해안 경유지 후보 추출 중...")
    coastline = gpd.read_file(COASTLINE_PATH)

    if coastline.crs is None:
        coastline.set_crs(epsg=4326, inplace=True)

    # Point 후보 추출
    coastline_points = extract_waypoints_from_lines(coastline)

    lat_sorted = coastline_points.iloc[(coastline_points.geometry.y - start_lat).abs().argsort()[:20]]
    lon_sorted = coastline_points.iloc[(coastline_points.geometry.x - start_lon).abs().argsort()[:20]]
    candidates = pd.concat([lat_sorted, lon_sorted]).drop_duplicates()

    def distance(p): return ((p.y - start_lat)**2 + (p.x - start_lon)**2)**0.5
    candidates['dist'] = candidates.geometry.apply(distance)

    nearest = candidates.sort_values('dist').iloc[0]
    print("✅ 선택된 경유지:", nearest.geometry.y, nearest.geometry.x)
    return (nearest.geometry.y, nearest.geometry.x)

# 네이버 Directions API 경로 요청
def get_naver_route(start, waypoint, end):
    url = "https://naveropenapi.apigw.ntruss.com/map-direction/v1/driving"
    headers = {
        "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID,
        "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET
    }
    coords = f"{start[1]},{start[0]}|{waypoint[1]},{waypoint[0]}|{end[1]},{end[0]}"
    params = {
        "start": f"{start[1]},{start[0]}",
        "goal": f"{end[1]},{end[0]}",
        "waypoints": f"{waypoint[1]},{waypoint[0]}",
        "option": "trafast",
        "coordType": "wgs84"
    }
    res = requests.get(url, headers=headers, params=params)
    data = res.json()
    if res.status_code == 200 and 'route' in data:
        return data
    else:
        raise Exception("경로 계산 실패: " + json.dumps(data))

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/route', methods=['POST'])
def route():
    try:
        data = request.get_json()
        start_address = data['start']
        end_address = data['end']

        print("📍 출발지:", start_address)
        print("📍 도착지:", end_address)

        start = geocode_address(start_address)
        end = geocode_address(end_address)
        if not start or not end:
            return jsonify({"error": "❌ 주소 인식 실패"}), 400

        waypoint = find_nearest_waypoint(start[0], start[1], end[0], end[1])
        route_data = get_naver_route(start, waypoint, end)

        return jsonify(route_data)
    except Exception as e:
        print("❌ 예외 발생:", str(e))
        return jsonify({"error": f"❌ 서버 오류: {str(e)}"}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000, debug=True)
