import os
import requests
from flask import Flask, request, jsonify, render_template

app = Flask(__name__)

# API Keys
GOOGLE_API_KEY = "AIzaSyC9MSD-WhkqK_Og5YdVYfux21xiRjy2q1M"
ORS_API_KEY = "5b3ce3597851110001cf62486d543846e80049df9c7a9e10ecef2953"

# 주소를 Google Maps Geocoding API로 좌표 변환
def geocode_address(address):
    url = f"https://maps.googleapis.com/maps/api/geocode/json?address={address}&region=kr&key={GOOGLE_API_KEY}"
    response = requests.get(url).json()
    if response['status'] == 'OK':
        location = response['results'][0]['geometry']['location']
        formatted = response['results'][0]['formatted_address']
        return location['lat'], location['lng'], formatted
    return None, None, None

# 해안도로 기반 경로 탐색 (직선 경로는 OpenRouteService가 계산)
def get_route(start_coords, end_coords):
    headers = {
        'Authorization': ORS_API_KEY,
        'Content-Type': 'application/json'
    }
    body = {
        "coordinates": [
            [start_coords[1], start_coords[0]],
            [end_coords[1], end_coords[0]]
        ]
    }
    response = requests.post("https://api.openrouteservice.org/v2/directions/driving-car", headers=headers, json=body)
    if response.status_code == 200:
        return response.json()
    return None

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/route', methods=['POST'])
def route():
    data = request.get_json()
    start_input = data['start']
    end_input = data['end']

    start_lat, start_lng, start_fmt = geocode_address(start_input)
    end_lat, end_lng, end_fmt = geocode_address(end_input)

    if None in [start_lat, start_lng, end_lat, end_lng]:
        return jsonify({'error': '주소를 인식하지 못했습니다. 정확한 도로명 또는 지번 주소를 입력해 주세요.'})

    route_result = get_route((start_lat, start_lng), (end_lat, end_lng))
    if route_result is None:
        return jsonify({'error': '경로 계산에 실패했습니다. 다시 시도해 주세요.'})

    return jsonify({
        'geojson': route_result,
        'start_corrected': start_fmt,
        'end_corrected': end_fmt
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=True)
