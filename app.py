from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import requests
import math
import json

app = Flask(__name__)
CORS(app)

# 직접 삽입된 API 키
VWORLD_KEY = '9E77283D-954A-3077-B7C8-9BD5ADB33255'
ORS_KEY = '5b3ce3597851110001cf62486d543846e80049df9c7a9e10ecef2953'
TOURAPI_KEY = 'e1tU33wjMx2nynKjH8yDBm/S4YNne6B8mpCOWtzMH9TSONF71XG/xAwPqyv1fANpgeOvbPY+Le+gM6cYCnWV8w=='

def geocode_vworld(address):
    url = f'https://api.vworld.kr/req/address?service=address&request=getcoord&format=json&type=both&address={address}&key={VWORLD_KEY}'
    res = requests.get(url)
    data = res.json()
    if data['response']['status'] == 'OK':
        point = data['response']['result']['point']
        return float(point['y']), float(point['x'])
    return None

def load_coastline():
    try:
        with open('해안선_국가기본도.geojson', 'r', encoding='utf-8') as f:
            geojson = json.load(f)
        coords = []
        for feature in geojson['features']:
            if feature['geometry']['type'] == 'LineString':
                for lon, lat in feature['geometry']['coordinates']:
                    coords.append((lat, lon))
            elif feature['geometry']['type'] == 'MultiLineString':
                for linestring in feature['geometry']['coordinates']:
                    for lon, lat in linestring:
                        coords.append((lat, lon))
        return coords
    except Exception as e:
        print(f"해안선 파일 오류: {e}")
        return []

def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def find_nearest_point(lat, lon, mode='lat'):
    points = load_coastline()
    if not points:
        return None
    candidates = sorted(points, key=lambda p: abs(p[0] - lat) if mode == 'lat' else abs(p[1] - lon))[:30]
    return min(candidates, key=lambda p: haversine(lat, lon, p[0], p[1]))

def get_route(coords):
    url = 'https://api.openrouteservice.org/v2/directions/driving-car/geojson'
    headers = {'Authorization': ORS_KEY, 'Content-Type': 'application/json'}
    body = {'coordinates': coords}
    res = requests.post(url, headers=headers, json=body)
    if res.status_code == 200:
        return res.json()
    return None

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/route', methods=['POST'])
def route():
    data = request.get_json()
    start = geocode_vworld(data['start'])
    end = geocode_vworld(data['end'])
    if not start or not end:
        return jsonify({'error': '주소 변환 실패'}), 400

    coast_lat = find_nearest_point(start[0], start[1], mode='lat')
    coast_lon = find_nearest_point(start[0], start[1], mode='lon')
    if not coast_lat or not coast_lon:
        return jsonify({'error': '해안선 파일 없음'}), 500

    waypoint = coast_lat if haversine(*start, *coast_lat) < haversine(*start, *coast_lon) else coast_lon
    route = get_route([[start[1], start[0]], [waypoint[1], waypoint[0]], [end[1], end[0]]])
    if not route:
        return jsonify({'error': '경로 계산 실패'}), 500

    return jsonify({'route': route})

@app.route('/api/tourspot', methods=['POST'])
def tourspot():
    data = request.get_json()
    lat, lon = data['lat'], data['lon']
    url = f'https://apis.data.go.kr/B551011/KorService1/locationBasedList1?MobileOS=ETC&MobileApp=test&arrange=E&mapX={lon}&mapY={lat}&radius=5000&numOfRows=10&pageNo=1&_type=json&serviceKey={TOURAPI_KEY}'
    res = requests.get(url)
    items = res.json().get('response', {}).get('body', {}).get('items', {}).get('item', [])
    spots = []
    for item in items:
        spots.append({
            'title': item.get('title'),
            'addr': item.get('addr1'),
            'image': item.get('firstimage', ''),
            'lat': float(item.get('mapy')),
            'lon': float(item.get('mapx'))
        })
    return jsonify(spots)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
