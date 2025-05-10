import os
import json
import requests
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS

tour_api_key = os.environ.get("TOURAPI_KEY") or "e1tU33wjMx2nynKjH8yDBm/S4YNne6B8mpCOWtzMH9TSONF71XG/xAwPqyv1fANpgeOvbPY+Le+gM6cYCnWV8w=="
naver_client_id = os.environ.get("NAVER_CLIENT_ID") or "vsdzf1f4n5"
naver_client_secret = os.environ.get("NAVER_CLIENT_SECRET") or "0gzctO51PUTVv0gUZU025JYNHPTmVzLS9sGbfYBM"

def geocode_google(address):
    url = f"https://maps.googleapis.com/maps/api/geocode/json?address={address}&key=AIzaSyC9MSD-WhkqK_Og5YdVYfux21xiRjy2q1M"
    res = requests.get(url)
    data = res.json()
    if data['status'] == 'OK':
        loc = data['results'][0]['geometry']['location']
        return loc['lat'], loc['lng'], data['results'][0]['formatted_address']
    return None, None, None

def get_route_naver(start_lon, start_lat, end_lon, end_lat):
    headers = {
        'X-NCP-APIGW-API-KEY-ID': naver_client_id,
        'X-NCP-APIGW-API-KEY': naver_client_secret,
        'Content-Type': 'application/json'
    }
    payload = {
        'start': f"{start_lon},{start_lat}",
        'goal': f"{end_lon},{end_lat}",
        'option': 'trafast',
        'cartype': 0,
        'waypoints': []
    }
    try:
        res = requests.post("https://naveropenapi.apigw.ntruss.com/map-direction-15/v1/driving", 
                            headers=headers, data=json.dumps(payload))
        print("[DEBUG] NAVER 응답코드:", res.status_code)
        print("[DEBUG] 응답내용:", res.text[:200])
        if res.status_code == 200:
            return res.json()
    except Exception as e:
        print("[ERROR] NAVER Directions 호출 실패:", e)
    return None

def get_tourspots(lat, lng):
    url = f"http://apis.data.go.kr/B551011/KorService1/locationBasedList1?serviceKey={tour_api_key}&numOfRows=20&pageNo=1&MobileOS=ETC&MobileApp=AppTest&arrange=E&mapX={lng}&mapY={lat}&radius=5000&_type=json"
    try:
        res = requests.get(url)
        items = res.json().get('response', {}).get('body', {}).get('items', {}).get('item', [])
        return items
    except Exception as e:
        print("[ERROR] TourAPI 호출 실패:", e)
        return []

app = Flask(__name__)
CORS(app)

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/route', methods=['POST'])
def route():
    try:
        data = request.get_json()
        start = data.get('start')
        end = data.get('end')

        if not start or not end:
            return jsonify({"error": "❌ 출발지와 목적지를 입력하세요."}), 400

        start_lat, start_lng, start_fmt = geocode_google(start)
        end_lat, end_lng, end_fmt = geocode_google(end)

        if not start_lat or not end_lat:
            return jsonify({"error": "❌ 주소 인식 실패"}), 400

        print("[INFO] 출발지:", start_fmt, start_lat, start_lng)
        print("[INFO] 목적지:", end_fmt, end_lat, end_lng)

        # 경로 요청 (네이버)
        route_data = get_route_naver(start_lng, start_lat, end_lng, end_lat)
        if not route_data or 'route' not in route_data:
            return jsonify({"error": "❌ 경로 계산 실패"}), 500

        geojson = {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [step['point'] for step in route_data['route']['trafast'][0]['path']]
            }
        }
        tourspots = get_tourspots(end_lat, end_lng)

        return jsonify({
            'geojson': geojson,
            'start_corrected': start_fmt,
            'end_corrected': end_fmt,
            'tourspots': tourspots
        })

    except Exception as e:
        print("[ERROR] 전체 처리 실패:", e)
        return jsonify({"error": "❌ 예외 발생: 서버 또는 네트워크 오류"}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)), debug=True)
