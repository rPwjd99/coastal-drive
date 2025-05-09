import os
import requests
from flask import Flask, request, jsonify, render_template

app = Flask(__name__)

GOOGLE_API_KEY = "AIzaSyC9MSD-WhkqK_Og5YdVYfux21xiRjy2q1M"
ORS_API_KEY = "5b3ce3597851110001cf62486d543846e80049df9c7a9e10ecef2953"
TOUR_API_KEY = "e1tU33wjMx2nynKjH8yDBm/S4YNne6B8mpCOWtzMH9TSONF71XG/xAwPqyv1fANpgeOvbPY+Le+gM6cYCnWV8w=="

def geocode_address(address):
    url = f"https://maps.googleapis.com/maps/api/geocode/json?address={address}&region=kr&key={GOOGLE_API_KEY}"
    response = requests.get(url).json()
    if response['status'] == 'OK':
        location = response['results'][0]['geometry']['location']
        formatted = response['results'][0]['formatted_address']
        return location['lat'], location['lng'], formatted
    return None, None, None

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
    print("📡 OpenRouteService 요청 좌표:", body)
    print("📡 응답 코드:", response.status_code)
    try:
        return response.json()
    except Exception as e:
        print("❌ JSON 파싱 오류:", e)
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
        print("❌ 관광지 불러오기 오류:", e)
        return []

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/route', methods=['POST'])
def route():
    data = request.get_json()
    start_input = data['start']
    end_input = data['end']

    print("🚀 입력 받은 출발지:", start_input)
    print("🚀 입력 받은 목적지:", end_input)

    start_lat, start_lng, start_fmt = geocode_address(start_input)
    end_lat, end_lng, end_fmt = geocode_address(end_input)

    print("📍 출발지 좌표:", start_lat, start_lng)
    print("📍 목적지 좌표:", end_lat, end_lng)

    if None in [start_lat, start_lng, end_lat, end_lng]:
        print("❌ 주소 보정 실패")
        return jsonify({'error': '주소를 인식하지 못했습니다. 정확한 도로명 또는 지번 주소를 입력해 주세요.'})

    try:
        route_result = get_route((start_lat, start_lng), (end_lat, end_lng))
        print("🧭 경로 응답:", route_result)
        if not route_result or 'features' not in route_result:
            print("❌ features 없음")
            return jsonify({'error': '경로 계산 실패'}), 500
    except Exception as e:
        print("❌ 경로 계산 중 예외:", e)
        return jsonify({'error': '경로 계산 중 내부 오류'}), 500

    tour_spots = get_tourspots(end_lat, end_lng)
    print("🏖 관광지 수:", len(tour_spots))

    return jsonify({
        'geojson': route_result,
        'start_corrected': start_fmt,
        'end_corrected': end_fmt,
        'tourspots': tour_spots
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=True)
