from flask import Flask, render_template, request, jsonify
import requests
import geopandas as gpd
from shapely.geometry import Point
import os

app = Flask(__name__)

# 환경 변수 또는 직접 API Key 설정
NAVER_CLIENT_ID = "vsdzf1f4n5"
NAVER_CLIENT_SECRET = "0gzctO51PUTVv0gUZU025JYNHPTmVzLS9sGbfYBM"
TOURAPI_KEY = "e1tU33wjMx2nynKjH8yDBm/S4YNne6B8mpCOWtzMH9TSONF71XG/xAwPqyv1fANpgeOvbPY+Le+gM6cYCnWV8w=="

@app.route('/')
def index():
    return render_template('index.html')

def geocode_google(address):
    url = f"https://maps.googleapis.com/maps/api/geocode/json?address={address}&key=AIzaSyC9MSD-WhkqK_Og5YdVYfux21xiRjy2q1M"
    res = requests.get(url)
    data = res.json()
    if data['status'] == 'OK':
        location = data['results'][0]['geometry']['location']
        return location['lat'], location['lng']
    return None, None

def find_waypoint_near_coastline(start_coord):
    gdf = gpd.read_file("coastal_route_result.geojson")
    point = Point(start_coord[1], start_coord[0])
    gdf = gdf.to_crs(epsg=5179)  # 거리 단위(m)로 계산하기 위해 투영
    point_proj = gpd.GeoSeries([point], crs="EPSG:4326").to_crs(epsg=5179).iloc[0]
    gdf["distance"] = gdf.geometry.distance(point_proj)
    sorted_gdf = gdf.sort_values("distance").head(20)  # 거리순 정렬

    for geom in sorted_gdf.geometry:
        coords = list(geom.coords)
        for lon, lat in coords:
            route = get_naver_route(start_coord, (lat, lon))
            if route:
                return (lat, lon)
    return None

def get_naver_route(start, waypoint, end=None):
    headers = {
        "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID,
        "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET,
        "Content-Type": "application/json"
    }
    coords = [
        {"x": str(start[1]), "y": str(start[0])},
        {"x": str(waypoint[1]), "y": str(waypoint[0])}
    ]
    if end:
        coords.append({"x": str(end[1]), "y": str(end[0])})
    data = {"start": coords[0], "goal": coords[-1], "waypoints": coords[1:-1]}

    res = requests.post("https://naveropenapi.apigw.ntruss.com/map-direction/v1/driving",
                        headers=headers, json=data)
    if res.status_code == 200 and res.json().get("route"):
        return res.json()
    return None

def get_tourspots(lat, lng):
    url = f"http://apis.data.go.kr/B551011/KorService1/locationBasedList1?ServiceKey={TOURAPI_KEY}&mapX={lng}&mapY={lat}&radius=5000&MobileOS=ETC&MobileApp=TestApp&_type=json"
    res = requests.get(url)
    data = res.json()
    if 'response' in data and data['response']['body']['items']:
        return data['response']['body']['items']['item']
    return []

@app.route('/route', methods=['POST'])
def route():
    data = request.get_json()
    start_addr = data.get('start')
    end_addr = data.get('end')

    start_lat, start_lng = geocode_google(start_addr)
    end_lat, end_lng = geocode_google(end_addr)
    if not all([start_lat, start_lng, end_lat, end_lng]):
        return jsonify({"error": "❌ 주소 인식 실패"}), 500

    waypoint = find_waypoint_near_coastline((start_lat, start_lng))
    if not waypoint:
        return jsonify({"error": "❌ 해안 경유지 탐색 실패"}), 500

    result = get_naver_route((start_lat, start_lng), waypoint, (end_lat, end_lng))
    if not result:
        return jsonify({"error": "❌ 경로 계산 실패"}), 500

    tourspots = get_tourspots(end_lat, end_lng)

    return jsonify({
        "route": result,
        "waypoint": waypoint,
        "tourspots": tourspots
    })

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=10000)
