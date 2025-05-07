from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import requests
import urllib.parse
import geopandas as gpd
import shapely.geometry
import os

app = Flask(__name__)
CORS(app)

# 기본 설정
PORT = int(os.environ.get("PORT", 10000))
NAVER_CLIENT_ID = "vsdzf1f4n5"
NAVER_CLIENT_SECRET = "0gzctO51PUTVv0gUZU025JYNHPTmVzLS9sGbfYBM"
ORS_API_KEY = "5b3ce3597851110001cf62486d543846e80049df9c7a9e10ecef2953"

# 해안선 GeoJSON 불러오기
coastline = gpd.read_file("해안선_국가기본도.geojson").to_crs(epsg=4326)


def geocode_address(address):
    query = urllib.parse.quote(address)
    url = f"https://naveropenapi.apigw.ntruss.com/map-geocode/v2/geocode?query={query}"
    headers = {
        "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID,
        "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET
    }
    try:
        response = requests.get(url, headers=headers)
        data = response.json()
        if 'addresses' in data and data['addresses']:
            addr = data['addresses'][0]
            return float(addr['y']), float(addr['x'])
        return None
    except Exception as e:
        print(f"Geocoding Error: {e}")
        return None


def find_nearest_coast_point(lat, lon):
    point = shapely.geometry.Point(lon, lat)
    coastline['distance'] = coastline.geometry.distance(point)
    nearest = coastline.loc[coastline['distance'].idxmin()]
    return nearest.geometry.centroid.y, nearest.geometry.centroid.x


def get_route(coords):
    url = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
    headers = {
        "Authorization": ORS_API_KEY,
        "Content-Type": "application/json"
    }
    body = {
        "coordinates": coords
    }
    try:
        response = requests.post(url, json=body, headers=headers)
        if response.status_code == 200:
            return response.json()
    except Exception as e:
        print(f"Route Error: {e}")
    return None


@app.route('/')
def home():
    return render_template("index.html")


@app.route('/api/route', methods=['POST'])
def route():
    data = request.get_json()
    start_address = data.get('start')
    end_address = data.get('end')

    start_coord = geocode_address(start_address)
    end_coord = geocode_address(end_address)

    if not start_coord or not end_coord:
        return jsonify({"error": "주소 해석 실패. 도로명, 지번, 업체명 등을 정확히 입력해 주세요."}), 400

    coast_lat, coast_lon = find_nearest_coast_point(*start_coord)
    coords = [[start_coord[1], start_coord[0]], [coast_lon, coast_lat], [end_coord[1], end_coord[0]]]
    route_geojson = get_route(coords)

    if route_geojson:
        return jsonify(route_geojson)
    else:
        return jsonify({"error": "해안 우회 경로 계산 실패."}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=PORT, debug=True)
