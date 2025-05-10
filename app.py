from flask import Flask, request, jsonify, render_template
import requests
import os
from shapely.geometry import LineString

app = Flask(__name__, template_folder='templates')

# API 키 설정
GOOGLE_API_KEY = "AIzaSyC9MSD-WhkqK_Og5YdVYfux21xiRjy2q1M"
NAVER_CLIENT_ID = "vsdzf1f4n5"
NAVER_CLIENT_SECRET = "0gzctO51PUTVv0gUZU025JYNHPTmVzLS9sGbfYBM"

@app.route('/')
def index():
    return render_template("index.html")

def geocode_address_google(address):
    url = f"https://maps.googleapis.com/maps/api/geocode/json?address={address}&key={GOOGLE_API_KEY}"
    try:
        res = requests.get(url)
        data = res.json()
        if data.get('status') == 'OK':
            loc = data['results'][0]['geometry']['location']
            formatted = data['results'][0]['formatted_address']
            return loc['lat'], loc['lng'], formatted
    except Exception as e:
        print("❌ Google 주소 변환 오류:", e)
    return None, None, None

def get_route_naver(start_lat, start_lng, end_lat, end_lng):
    url = "https://naveropenapi.apigw.ntruss.com/map-direction/v1/driving"
    headers = {
        "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID,
        "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET
    }
    params = {
        "start": f"{start_lng},{start_lat}",
        "goal": f"{end_lng},{end_lat}",
        "option": "trafast"
    }
    try:
        res = requests.get(url, headers=headers, params=params)
        print("📡 NAVER 응답 코드:", res.status_code)
        print("📦 NAVER 응답 내용:", res.text)
        if res.status_code != 200:
            return None
        data = res.json()
        path = data['route']['trafast'][0]['path']
        return {
            "type": "LineString",
            "coordinates": [(pt[0], pt[1]) for pt in path]
        }
    except Exception as e:
        print("❌ NAVER 경로 오류:", e)
        return None

@app.route('/route', methods=['POST'])
def route():
    try:
        data = request.get_json()
        start_input = data['start']
        end_input = data['end']

        start_lat, start_lng, start_fmt = geocode_address_google(start_input)
        end_lat, end_lng, end_fmt = geocode_address_google(end_input)

        if None in [start_lat, start_lng, end_lat, end_lng]:
            return jsonify({'error': '주소 인식 실패'})

        route_result = get_route_naver(start_lat, start_lng, end_lat, end_lng)
        if not route_result:
            return jsonify({'error': '경로 계산 실패'}), 500

        return jsonify({
            'geojson': route_result,
            'start_corrected': start_fmt,
            'end_corrected': end_fmt,
            'tourspots': []
        })

    except Exception as e:
        print("❌ 전체 처리 예외:", e)
        return jsonify({'error': '서버 내부 오류'}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port, debug=True)
