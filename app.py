from flask import Flask, request, jsonify, render_template
import requests
import os
import json
import geopandas as gpd
from shapely.geometry import Point, LineString

app = Flask(__name__, template_folder="templates")

NAVER_CLIENT_ID = "vsdzf1f4n5"
NAVER_CLIENT_SECRET = "0gzctO51PUTVv0gUZU025JYNHPTmVzLS9sGbfYBM"

@app.route('/')
def index():
    return render_template("index.html")

def geocode_address_naver(address):
    url = f"https://naveropenapi.apigw.ntruss.com/map-geocode/v2/geocode?query={address}"
    headers = {
        "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID,
        "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET
    }
    res = requests.get(url, headers=headers)
    data = res.json()
    if data.get('addresses'):
        first = data['addresses'][0]
        return float(first['y']), float(first['x']), first['roadAddress']
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
    res = requests.get(url, headers=headers, params=params)
    print("📡 NAVER 응답 코드:", res.status_code)
    if res.status_code != 200:
        print(res.text)
        return None
    data = res.json()
    try:
        path = data['route']['trafast'][0]['path']
        line = LineString([(pt[0], pt[1]) for pt in path])
        return {
            "type": "Feature",
            "geometry": json.loads(gpd.GeoSeries([line]).to_json())['features'][0]['geometry']
        }
    except Exception as e:
        print("❌ NAVER 경로 파싱 실패:", e)
        return None

@app.route('/route', methods=['POST'])
def route():
    data = request.get_json()
    start_input = data['start']
    end_input = data['end']

    start_lat, start_lng, start_fmt = geocode_address_naver(start_input)
    end_lat, end_lng, end_fmt = geocode_address_naver(end_input)

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

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=True)
