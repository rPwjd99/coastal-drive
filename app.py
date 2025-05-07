from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import requests
import urllib.parse

app = Flask(__name__)
CORS(app)

NAVER_CLIENT_ID = "vsdzf1f4n5"
NAVER_CLIENT_SECRET = "0gzctO51PUTVv0gUZU025JYNHPTmVzLS9sGbfYBM"

@app.route('/')
def root():
    return render_template('index.html')

@app.route('/api/route')
def get_route():
    start_addr = request.args.get("start")
    end_addr = request.args.get("end")

    if not start_addr or not end_addr:
        return jsonify({"error": "출발지와 도착지를 입력하세요."})

    try:
        # 주소를 URL 인코딩
        start_encoded = urllib.parse.quote(start_addr)
        end_encoded = urllib.parse.quote(end_addr)

        # NAVER API 호출
        def geocode_naver(address_encoded):
            url = f"https://naveropenapi.apigw.ntruss.com/map-geocode/v2/geocode?query={address_encoded}"
            headers = {
                "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID,
                "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET
            }
            response = requests.get(url, headers=headers)
            data = response.json()
            if response.status_code == 200 and data.get('addresses'):
                addr = data['addresses'][0]
                return float(addr['x']), float(addr['y'])  # (lon, lat)
            else:
                print(f"NAVER API 응답 실패: {data}")
                return None

        start_coord = geocode_naver(start_encoded)
        end_coord = geocode_naver(end_encoded)

        if not start_coord or not end_coord:
            return jsonify({"error": "주소 해석 실패. 도로명 주소를 정확히 입력해 주세요."})

        # OpenRouteService 경로 요청
        ORS_KEY = "5b3ce3597851110001cf62486d543846e80049df9c7a9e10ecef2953"
        ors_url = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
        ors_headers = {
            "Authorization": ORS_KEY,
            "Content-Type": "application/json"
        }
        route_body = {
            "coordinates": [
                list(start_coord),  # [lon, lat]
                list(end_coord)
            ]
        }

        ors_response = requests.post(ors_url, headers=ors_headers, json=route_body)
        if ors_response.status_code != 200:
            return jsonify({"error": "경로 계산 실패. OpenRouteService 오류."})

        route_data = ors_response.json()
        return jsonify({"route": route_data})

    except Exception as e:
        print("서버 오류:", e)
        return jsonify({"error": "서버 내부 오류 발생. 관리자에게 문의하세요."})

if __name__ == '__main__':
    app.run(debug=True)
