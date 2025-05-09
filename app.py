from flask import Flask, request, jsonify, render_template
import requests
import os
import json
import math
import geopandas as gpd
from shapely.geometry import Point

app = Flask(__name__, template_folder="templates")

GOOGLE_API_KEY = "AIzaSyC9MSD-WhkqK_Og5YdVYfux21xiRjy2q1M"
ORS_API_KEY = "5b3ce3597851110001cf62486d543846e80049df9c7a9e10ecef2953"
TOUR_API_KEY = "e1tU33wjMx2nynKjH8yDBm/S4YNne6B8mpCOWtzMH9TSONF71XG/xAwPqyv1fANpgeOvbPY+Le+gM6cYCnWV8w=="

@app.route('/')
def index():
    return render_template("index.html")

def geocode_address(address):
    url = f"https://maps.googleapis.com/maps/api/geocode/json?address={address}&region=kr&key={GOOGLE_API_KEY}"
    response = requests.get(url).json()
    if response['status'] == 'OK':
        location = response['results'][0]['geometry']['location']
        formatted = response['results'][0]['formatted_address']
        return location['lat'], location['lng'], formatted
    return None, None, None

def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def find_candidate_waypoints(start_lat, start_lng, end_lat, end_lng):
    coast_gdf = gpd.read_file("./coastal_route_result.geojson").to_crs(epsg=4326)
    coords_list = []
    for geom in coast_gdf.geometry:
        if geom.geom_type == 'MultiLineString':
            for line in geom.geoms:
                coords_list.extend(list(line.coords))
        elif geom.geom_type == 'LineString':
            coords_list.extend(list(geom.coords))
    ranked = [(lat, lon, haversine(start_lat, start_lng, lat, lon) + haversine(lat, lon, end_lat, end_lng)) for lon, lat in coords_list]
    ranked.sort(key=lambda x: x[2])
    return [(lat, lon) for lat, lon, _ in ranked[:20]]  # 상위 20개 후보 반환

def get_route_by_coords(coords):
    headers = {
        'Authorization': ORS_API_KEY,
        'Content-Type': 'application/json'
    }
    body = {"coordinates": [[lng, lat] for lat, lng in coords]}
    try:
        res = requests.post("https://api.openrouteservice.org/v2/directions/driving-car", headers=headers, json=body)
        print("📡 ORS 응답 코드:", res.status_code)
        return res.json() if res.status_code == 200 and 'features' in res.json() else None
    except Exception as e:
        print("❌ 경로 요청 오류:", e)
        return None

def find_first_successful_waypoint(start_lat, start_lng, end_lat, end_lng):
    candidates = find_candidate_waypoints(start_lat, start_lng, end_lat, end_lng)
    for lat, lng in candidates:
        print("🔍 테스트 중인 후보 해안 좌표:", lat, lng)
        result = get_route_by_coords([(start_lat, start_lng), (lat, lng), (end_lat, end_lng)])
        if result:
            print("✅ 성공한 해안 경유지 발견!")
            return lat, lng, result
    return None, None, None

def get_tourspots(lat, lng):
    url = (
        f"https://apis.data.go.kr/B551011/KorService1/locationBasedList1"
        f"?serviceKey={TOUR_API_KEY}"
        f"&numOfRows=10&pageNo=1&MobileOS=ETC&MobileApp=CoastalDrive&_type=json"
        f"&mapX={lng}&mapY={lat}&radius=5000"
    )
    try:
        response = requests.get(url).json()
        return response['response']['body']['items']['item']
    except Exception as e:
        print("관광지 로딩 실패:", e)
        return []

@app.route('/route', methods=['POST'])
def route():
    data = request.get_json()
    start_input = data['start']
    end_input = data['end']

    start_lat, start_lng, start_fmt = geocode_address(start_input)
    end_lat, end_lng, end_fmt = geocode_address(end_input)

    if None in [start_lat, start_lng, end_lat, end_lng]:
        return jsonify({'error': '주소를 인식하지 못했습니다.'})

    try:
        waypoint_lat, waypoint_lng, route_result = find_first_successful_waypoint(start_lat, start_lng, end_lat, end_lng)
        if not route_result:
            return jsonify({'error': '경로 계산 실패'}), 500
    except Exception as e:
        print("❌ 전체 경로 계산 중 오류:", e)
        return jsonify({'error': '경로 계산 중 오류'}), 500

    tour_spots = get_tourspots(end_lat, end_lng)

    return jsonify({
        'geojson': route_result,
        'start_corrected': start_fmt,
        'end_corrected': end_fmt,
        'tourspots': tour_spots
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=True)
