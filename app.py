# app.py
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
from math import radians, sin, cos, sqrt, atan2
from beach_coords import beach_coords  # 같은 폴더 내 py 파일

app = Flask(__name__)
CORS(app)

# NAVER API 인증 정보
CLIENT_ID = "vsdzf1f4n5"
CLIENT_SECRET = "0gzctO51PUTVv0gUZU025JYNHPTmVzLS9sGbfYBM"

def geocode_address(address):
    url = "https://naveropenapi.apigw.ntruss.com/map-geocode/v2/geocode"
    headers = {
        "X-NCP-APIGW-API-KEY-ID": CLIENT_ID,
        "X-NCP-APIGW-API-KEY": CLIENT_SECRET,
    }
    params = {"query": address}
    response = requests.get(url, headers=headers, params=params)
    result = response.json()
    if result.get("addresses"):
        x = float(result["addresses"][0]["x"])
        y = float(result["addresses"][0]["y"])
        return (y, x)  # (lat, lon)
    else:
        return None

def haversine(coord1, coord2):
    R = 6371.0
    lat1, lon1 = map(radians, coord1)
    lat2, lon2 = map(radians, coord2)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat/2)**2 + cos(lat1)*cos(lat2)*sin(dlon/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return R * c

def find_closest_beach(start_coord):
    min_dist = float("inf")
    closest = None
    for name, (lon, lat) in beach_coords.items():
        dist = haversine(start_coord, (lat, lon))
        if dist < min_dist:
            min_dist = dist
            closest = (name, lat, lon)
    return closest  # (이름, lat, lon)

@app.route('/route', methods=['POST'])
def get_route():
    data = request.get_json()
    origin_addr = data.get("origin")
    destination_addr = data.get("destination")

    if not origin_addr or not destination_addr:
        return jsonify({"error": "Missing origin or destination"}), 400

    origin_coord = geocode_address(origin_addr)
    destination_coord = geocode_address(destination_addr)

    if not origin_coord or not destination_coord:
        return jsonify({"error": "Address geocoding failed"}), 400

    # 가장 가까운 해안 웨이포인트 찾기
    beach_name, beach_lat, beach_lon = find_closest_beach(origin_coord)

    # NAVER Directions API 호출
    url = "https://naveropenapi.apigw.ntruss.com/map-direction/v1/driving"
    headers = {
        "X-NCP-APIGW-API-KEY-ID": CLIENT_ID,
        "X-NCP-APIGW-API-KEY": CLIENT_SECRET,
    }
    params = {
        "start": f"{origin_coord[1]},{origin_coord[0]}",  # lon,lat
        "goal": f"{destination_coord[1]},{destination_coord[0]}",  # lon,lat
        "waypoints": f"{beach_lon},{beach_lat}",  # 해안 웨이포인트
        "option": "trafast"
    }
    response = requests.get(url, headers=headers, params=params)
    if response.status_code == 200:
        return jsonify({
            "origin": origin_addr,
            "destination": destination_addr,
            "waypoint": {
                "name": beach_name,
                "lat": beach_lat,
                "lon": beach_lon
            },
            "route": response.json()
        })
    else:
        return jsonify({"error": "Route request failed"}), 500

if __name__ == '__main__':
    app.run(debug=True)
