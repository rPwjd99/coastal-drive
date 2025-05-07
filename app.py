from flask import Flask, request, jsonify, render_template
import requests
import urllib.parse

app = Flask(__name__)

# 네이버 클라우드 플랫폼 API 키
NAVER_CLIENT_ID = "vsdzf1f4n5"
NAVER_CLIENT_SECRET = "0gzctO51PUTVv0gUZU025JYNHPTmVzLS9sGbfYBM"

# OpenRouteService 키 (필요시 경로계산 구현 가능)
ORS_API_KEY = "5b3ce3597851110001cf62486d543846e80049df9c7a9e10ecef2953"

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/geocode', methods=['POST'])
def geocode():
    data = request.get_json()
    address = data.get('address')
    if not address:
        return jsonify({'error': '주소를 입력하세요.'}), 400

    encoded_address = urllib.parse.quote(address)
    url = f"https://naveropenapi.apigw.ntruss.com/map-geocode/v2/geocode?query={encoded_address}"
    headers = {
        "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID,
        "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET
    }

    try:
        res = requests.get(url, headers=headers)
        res.raise_for_status()
        result = res.json()

        if 'addresses' not in result or not result['addresses']:
            return jsonify({'error': f"주소 해석 실패: {address}"}), 404

        first_result = result['addresses'][0]
        return jsonify({
            "lat": first_result['y'],
            "lng": first_result['x'],
            "roadAddress": first_result.get("roadAddress"),
            "jibunAddress": first_result.get("jibunAddress")
        })
    except Exception as e:
        return jsonify({'error': '서버 오류 발생', 'details': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)
