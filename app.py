import os
import json
import requests
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import geopandas as gpd
from shapely.geometry import Point

app = Flask(__name__)
CORS(app)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")
TOUR_API_KEY = os.getenv("TOUR_API_KEY")

# 해안선 GeoJSON 불러오기
COASTLINE_PATH = "coastal_route_result.geojson"
coastline = gpd.read_file(COASTLINE_PATH).to_crs(epsg=4326)

# 좌표계산 거리 기준
BUFFER_KM = 10


def geocode_address(address):
    url = f"https://maps.googleapis.com/maps/api/geocode/json?address={address}&key={GOOGLE_API_KEY}"
    res = requests.get(url)
    if res.status_code == 200:
        data = res.json()
        if data['results']:
            location = data['results'][0]['geometry']['location']
            return location['lat'], location['lng'], data['results'][0]['formatted_address']
    return None, None, None


def find_closest_coast_point(lat, lng):
    point = Point(lng, lat)
    coast_with_dist = coastline.copy()
    coast_with_dist['distance'] = coast_with_dist.geometry.distance(point)
    coast_within_buffer = coast_with_dist[coast_with_dist['distance'] <= BUFFER_KM / 111]
    if coast_within_buffer.empty:
        return None
    closest = coast_within_buffer.sort_values('distance').iloc[0].geometry
    return closest.x, closest.y


def get_naver_route(start, waypoint, end):
    coords = f"{start[1]},{start[0]}|{waypoint[1]},{waypoint[0]}|{end[1]},{end[0]}"
    headers = {
        "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID,
        "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET
    }
    url = f"https://naveropenapi.apigw.ntruss.com/map-direction/v1/driving?start={start[1]},{start[0]}&goal={end[1]},{end[0]}&waypoints={waypoint[1]},{waypoint[0]}&option=trafast"
    res = requests.get(url, headers=headers)
    if res.status_code == 200:
        return res.json()
    else:
        print("❌ 네이버 Directions API 실패", res.status_code, res.text)
        return None


def search_tourspots(lat, lng):
    url = (
        f"https://apis.data.go.kr/B551011/KorService1/locationBasedList1?"
        f"MobileOS=ETC&MobileApp=CoastalDrive&ServiceKey={TOUR_API_KEY}"
        f"&_type=json&mapX={lng}&mapY={lat}&radius=5000"
    )
    res = requests.get(url)
    if res.status_code == 200:
        data = res.json()
        return data.get('response', {}).get('body', {}).get('items', {}).get('item', [])
    return []


@app.route('/')
def home():
    return render_template('index.html')


@app.route('/route', methods=['POST'])
def route():
    try:
        data = request.get_json()
        start_address, end_address = data['start'], data['end']

        slat, slng, s_fmt = geocode_address(start_address)
        elat, elng, e_fmt = geocode_address(end_address)

        if not all([slat, slng, elat, elng]):
            return jsonify({'error': '❌ 주소 인식 실패'}), 500

        print(f"✅ 출발지 좌표: {slat}, {slng}")
        print(f"✅ 도착지 좌표: {elat}, {elng}")

        waypoint = find_closest_coast_point(slat, slng)
        if not waypoint:
            return jsonify({'error': '❌ 해안 경유지 탐색 실패'}), 500

        print(f"✅ 해안 경유지 좌표: {waypoint[1]}, {waypoint[0]}")

        route_data = get_naver_route((slat, slng), waypoint, (elat, elng))
        if not route_data:
            return jsonify({'error': '❌ 경로 계산 실패'}), 500

        tourspots = search_tourspots(elat, elng)

        return jsonify({
            'route': route_data,
            'start_corrected': s_fmt,
            'end_corrected': e_fmt,
            'tourspots': tourspots
        })
    except Exception as e:
        print("❌ 예외 발생:", e)
        return jsonify({'error': '❌ 서버 내부 오류'}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=True)
