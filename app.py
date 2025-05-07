from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import requests
import urllib.parse

app = Flask(__name__)
CORS(app)

# 네이버 API 키
NAVER_CLIENT_ID = "vsdzf1f4n5"
NAVER_CLIENT_SECRET = "0gzctO51PUTVv0gUZU025JYNHPTmVzLS9sGbfYBM"

# 주소 보정 함수
def correct_address(addr):
    # 예외처리 또는 자동 보정 패턴 추가 가능
    if "한누리대로" in addr and "세종" not in addr:
        return "세종특별자치시 " + addr
    if "중앙로" in addr and "속초" not in addr:
        return "강원도 속초시 " + addr
    return addr

# 주소를 좌표로 변환
def get_coordinates(address):
    address = correct_address(address)
    encoded_address = urllib.parse.quote(address)
    url = f"https://naveropenapi.apigw.ntruss.com/map-geocode/v2/geocode?query={encoded_address}"
    headers = {
        "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID,
        "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET
    }
    response = requests.get(url, headers=headers)
    data = response.json()

    if 'addresses' in data and len(data['addresses']) > 0:
        point = data['addresses'][0]
        return float(point['y']), float(point['x'])
    else:
        return None

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/geocode", methods=["POST"])
def geocode():
    data = request.get_json()
    address = data.get("address")
    coords = get_coordinates(address)

    if coords:
        return jsonify({"success": True, "lat": coords[0], "lon": coords[1], "corrected": correct_address(address)})
    else:
        return jsonify({"success": False, "message": "주소 해석 실패"})

if __name__ == "__main__":
    app.run(debug=True)
