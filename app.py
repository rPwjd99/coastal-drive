from flask import Flask, request, jsonify, render_template
import requests
import os

app = Flask(__name__)

# 경로 루트
@app.route('/')
def index():
    return render_template("index.html")

# 경로 계산 요청
@app.route('/route', methods=['POST'])
def route():
    try:
        data = request.get_json()
        start_address = data.get('start')
        end_address = data.get('end')

        print(f"📍 입력 주소: 출발지 = {start_address}, 목적지 = {end_address}")

        # 구글 지오코딩
        google_key = os.environ.get("GOOGLE_API_KEY")
        if not google_key:
            return jsonify({'error': 'Google API Key 없음'}), 500

        def geocode(address):
            url = f"https://maps.googleapis.com/maps/api/geocode/json?address={address}&key={google_key}"
            res = requests.get(url).json()
            if res['status'] == 'OK':
                loc = res['results'][0]['geometry']['location']
                return loc['lat'], loc['lng'], res['results'][0]['formatted_address']
            return None, None, None

        start_lat, start_lng, start_fmt = geocode(start_address)
        end_lat, end_lng, end_fmt = geocode(end_address)

        if not all([start_lat, start_lng, end_lat, end_lng]):
            return jsonify({'error': '❌ 주소 인식 실패'}), 500

        print(f"✅ 주소 변환 성공: 출발 좌표 ({start_lat}, {start_lng}), 목적 좌표 ({end_lat}, {end_lng})")

        # 네이버 경로 API
        naver_id = os.environ.get("NAVER_CLIENT_ID")
        naver_secret = os.environ.get("NAVER_CLIENT_SECRET")
        headers = {
            "X-NCP-APIGW-API-KEY-ID": naver_id,
            "X-NCP-APIGW-API-KEY": naver_secret
        }
        naver_url = f"https://naveropenapi.apigw.ntruss.com/map-direction/v1/driving?start={start_lng},{start_lat}&goal={end_lng},{end_lat}&option=trafast"

        route_res = requests.get(naver_url, headers=headers).json()
        if route_res.get("code") != 0:
            return jsonify({'error': '❌ 경로 계산 실패'}), 500

        coords = route_res['route']['trafast'][0]['path']
        geojson = {
            "type": "LineString",
            "coordinates": coords
        }

        return jsonify({
            "geojson": geojson,
            "start_corrected": start_fmt,
            "end_corrected": end_fmt
        })

    except Exception as e:
        print("❌ 서버 내부 오류:", e)
        return jsonify({'error': '❌ 서버 내부 오류'}), 500


if __name__ == '__main__':
    app.run(debug=True, port=10000)
