from flask import Flask, request, jsonify, render_template
import requests
import os

app = Flask(__name__)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/route', methods=['POST'])
def route():
    data = request.get_json()
    start_address = data.get('start')
    end_address = data.get('end')

    if not start_address or not end_address:
        return jsonify({'error': '❌ 주소 입력이 필요합니다'}), 400

    # Google Maps Geocoding API를 이용한 주소 → 좌표 변환
    google_api_key = os.environ.get('GOOGLE_API_KEY')
    def geocode(address):
        url = f"https://maps.googleapis.com/maps/api/geocode/json?address={address}&key={google_api_key}"
        res = requests.get(url).json()
        if res['status'] == 'OK':
            loc = res['results'][0]['geometry']['location']
            fmt = res['results'][0]['formatted_address']
            return loc['lat'], loc['lng'], fmt
        return None, None, None

    start_lat, start_lng, start_fmt = geocode(start_address)
    end_lat, end_lng, end_fmt = geocode(end_address)

    print("📍 출발지:", start_address, "→", start_lat, start_lng)
    print("📍 목적지:", end_address, "→", end_lat, end_lng)

    if not all([start_lat, start_lng, end_lat, end_lng]):
        return jsonify({'error': '❌ 주소 인식 실패'}), 500

    # NAVER Directions API
    client_id = os.environ.get("NAVER_CLIENT_ID")
    client_secret = os.environ.get("NAVER_CLIENT_SECRET")
    headers = {
        "X-NCP-APIGW-API-KEY-ID": client_id,
        "X-NCP-APIGW-API-KEY": client_secret,
        "Content-Type": "application/json"
    }
    payload = {
        "start": {"lat": start_lat, "lng": start_lng, "name": "출발지"},
        "goal": {"lat": end_lat, "lng": end_lng, "name": "도착지"},
        "option": "trafast"
    }
    naver_url = "https://naveropenapi.apigw.ntruss.com/map-direction/v1/driving"
    route_res = requests.post(naver_url, headers=headers, json=payload).json()

    print("📩 NAVER 응답:", route_res)

    try:
        path = route_res['route']['trafast'][0]['path']
        geojson = {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [[lng, lat] for lat, lng in path]
            }
        }
    except Exception as e:
        print("❌ 경로 계산 예외:", e)
        return jsonify({'error': '❌ 경로 계산 실패'}), 500

    return jsonify({
        'geojson': geojson,
        'start_corrected': start_fmt,
        'end_corrected': end_fmt,
        'tourspots': []  # 향후 관광지 마커 추가 시 업데이트
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=True)
