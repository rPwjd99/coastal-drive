from flask import Flask, request, jsonify, render_template
import requests
import os
import json
import math
import geopandas as gpd

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
    coast_gdf = gpd.read_file("./해안선_국가기본도.geojson")
    coast_gdf = coast_gdf.to_crs(epsg=4326)
    coast_points = coast_gdf.explode(index_parts=False).geometry.apply(lambda geom: list(geom.coords)).explode().tolist()
    
    aligned_candidates = []
    for lon, lat in coast_points:
        lat_dist = abs(lat - start_lat)
        lng_dist = abs(lon - start_lng)
        end_lat_diff = abs(lat - end_lat)
        end_lng_diff = abs(lon - end_lng)
        total_dist = haversine(start_lat, start_lng, lat, lon) + haversine(lat, lon, end_lat, end_lng)
        aligned_candidates.append((lat, lon, total_dist))

    aligned_candidates.sort(key=lambda x: x[2])
    return aligned_candidates[0][0], aligned_candidates[0][1]  # lat, lon

def get_route_by_coords(coords):
    headers = {
        'Authorization': ORS_API_KEY,
        'Content-Type': 'application/json'
    }
    body = {"coordinates": [[lng, lat] for lat, lng in coords]}
    try:
        res = requests.post("https://api.openrouteservice.org/v2/directions/driving-car", headers=headers, json=body)
        print("ORS 응답 코드:", res.status_code)
        print("ORS 응답 내용:", res.text[:300])
        return res.json() if res.status_code == 200 else None
    except Exception as e:
        print("경로 요청 오류:", e)
        return None

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
