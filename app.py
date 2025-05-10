from flask import Flask, render_template, request, jsonify
import requests
import geopandas as gpd
from shapely.geometry import Point
import math

app = Flask(__name__)

NAVER_CLIENT_ID = 'vsdzf1f4n5'
NAVER_CLIENT_SECRET = '0gzctO51PUTVv0gUZU025JYNHPTmVzLS9sGbfYBM'

# 해안선 GeoJSON 불러오기
coast_gdf = gpd.read_file("coastal_route_result.geojson")

# 거리 계산 함수
def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    c = 2 * math.asin(math.sqrt(a))
    return R * c

# 가장 가까운 해안선 좌표 반환
def get_nearest_coast_point(lat, lon):
    coast_gdf["dist"] = coast_gdf.geometry.apply(lambda p: haversine(lat, lon, p.y, p.x))
    nearest = coast_gdf.sort_values("dist").iloc[0]
    return nearest.geometry.y, nearest.geometry.x

# VWorld API를 통한 주소 → 좌표 변환
def geocode_address(address):
    url = "https://api.vworld.kr/req/address"
    params = {
        "service": "address",
        "request": "getcoord",
        "format": "json",
        "crs": "EPSG:4326",
        "address": address,
        "type": "road",
        "key": "9E77283D-954A-3077-B7C8-9BD5ADB33255"
    }
    res = requests.get(url, params=params)
    try:
        coord = res.json()['response']['result']['point']
        return float(coord['y']), float(coord['x'])
    except:
        return None

# Naver Directions API를 통한 경로 계산
def get_naver_route(start, waypoint, end):
    url = "https://naveropenapi.apigw.ntruss.com/map-direction/v1/driving"
    headers = {
        "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID,
        "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET
    }
    params = {
        "start": f"{start[1]},{start[0]}",
        "goal": f"{end[1]},{end[0]}",
        "waypoints": f"{waypoint[1]},{waypoint[0]}",
        "option": "trafast"
    }
    res = requests.get(url, headers=headers, params=params)
    try:
        return res.json()
    except:
        return None

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/route', methods=['POST'])
def route():
    try:
        data = request.get_json()
        start_address = data['start']
        end_address = data['end']

        start = geocode_address(start_address)
        end = geocode_address(end_address)
        if not start or not end:
            return jsonify({"error": "❌ 주소 인식 실패"}), 500

        waypoint = get_nearest_coast_point(start[0], start[1])
        route_data = get_naver_route(start, waypoint, end)

        if not route_data or 'route' not in route_data:
            return jsonify({"error": "❌ 경로 계산 실패"}), 500

        return jsonify({
            "route": route_data['route'],
            "start_corrected": start_address,
            "end_corrected": end_address,
            "waypoint": waypoint
        })
    except Exception as e:
        return jsonify({"error": f"❌ 서버 오류: {str(e)}"}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000, debug=True)
