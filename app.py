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

def find_nearest_coast(start_lat, start_lng, end_lat, end_lng):
    try:
        coast_gdf = gpd.read_file("./coastal_route_result.geojson").to_crs(epsg=4326)
        coords_list = []
        for geom in coast_gdf.geometry:
            if geom.geom_type == 'MultiLineString':
                for line in geom.geoms:
                    coords_list.extend(list(line.coords))
            elif geom.geom_type == 'LineString':
                coords_list.extend(list(geom.coords))

        aligned = [(lat, lon, haversine(start_lat, start_lng, lat, lon)) for lon, lat in coords_list]
        aligned.sort(key=lambda x: x[2])
        return aligned[0][0], aligned[0][1]

    except Exception as e:
        print("❌ 해안선 분석 오류:", e)
        raise

def get_route_by_coords(coords):
    headers = {
        'Authorization': ORS_API_KEY,
        'Content-Type': 'application/json'
    }
    body = {"coordinates": [[lng, lat] for lat, lng in coords]}
    print("📦 ORS 요청 좌표:", body)
    try:
        res = requests.post("https://api.openrouteservice.org/v2/directions/driving-car", headers=headers, json=body)
        print("📡 ORS 응답 코드:", res.status_code)
        print("📨 ORS 응답 내용:", res.text[:500])
        return res.json() if res.status_code == 200 else None
    except Exception as e:
        print("❌ 경로 요청 오류:", e)
        return None

def find_fallback_waypoint(lat, lon):
    coast_gdf = gpd.read_file("./coastal_route_result.geojson").to_crs(epsg=4326)
    point = Point(lon, lat)
    buffered = point.buffer(0.045)  # 약 5km 버퍼 (도 단위 기준)
    for geom in coast_gdf.geometry:
        if geom.intersects(buffered):
            coords = list(geom.coords)
            for lon2, lat2 in coords:
                return lat2, lon2
    return lat, lon

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
        waypoint_lat, waypoint_lng = find_nearest_coast(start_lat, start_lng, end_lat, end_lng)
        route_result = get_route_by_coords([(start_lat, start_lng), (waypoint_lat, waypoint_lng), (end_lat, end_lng)])
        if not route_result or 'features' not in route_result:
            print("❌ 첫 번째 경로 실패 → Fallback 실행")
            waypoint_lat, waypoint_lng = find_fallback_waypoint(waypoint_lat, waypoint_lng)
            route_result = get_route_by_coords([(start_lat, start_lng), (waypoint_lat, waypoint_lng), (end_lat, end_lng)])
            if not route_result or 'features' not in route_result:
                return jsonify({'error': '경로 계산 실패'}), 500

    except Exception as e:
        print("경유지 경로 오류:", e)
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
