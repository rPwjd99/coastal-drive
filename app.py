from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
import requests
import json

app = Flask(__name__)
CORS(app)

# NAVER API 키
NAVER_CLIENT_ID = "vsdzf1f4n5"
NAVER_CLIENT_SECRET = "0gzctO51PUTVv0gUZU025JYNHPTmVzLS9sGbfYBM"

# OpenRouteService API 키
ORS_API_KEY = "5b3ce3597851110001cf62486d543846e80049df9c7a9e10ecef2953"

def geocode_naver(address):
    url = "https://naveropenapi.apigw.ntruss.com/map-geocode/v2/geocode"
    headers = {
        "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID,
        "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET
    }
    params = {"query": address}

    res = requests.get(url, headers=headers, params=params)
    if res.status_code == 200:
        data = res.json()
        if data["addresses"]:
            x = float(data["addresses"][0]["x"])  # 경도
            y = float(data["addresses"][0]["y"])  # 위도
            return [x, y]
    return None

def get_route(start, end):
    url = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
    headers = {
        "Authorization": ORS_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "coordinates": [start, end]
    }
    response = requests.post(url, headers=headers, json=payload)
    if response.status_code == 200:
        return response.json()
    return None

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/api/route', methods=['POST'])
def route():
    data = request.get_json()
    origin = data.get("origin")
    destination = data.get("destination")

    start_coord = geocode_naver(origin)
    end_coord = geocode_naver(destination)

    if not start_coord or not end_coord:
        return jsonify({"error": "주소 변환 실패"}), 400

    route_data = get_route(start_coord, end_coord)

    if route_data:
        return jsonify(route_data)
    else:
        return jsonify({"error": "경로 계산 실패"}), 500

if __name__ == '__main__':
    app.run(debug=True)
