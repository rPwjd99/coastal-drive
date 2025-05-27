from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
from math import radians, sin, cos, sqrt, atan2
from beaches_coordinates import beach_coords
import os

app = Flask(__name__)
CORS(app)

# 🔑 NAVER 인증정보
CLIENT_ID = "vsdzf1f4n5"
CLIENT_SECRET = "0gzctO51PUTVv0gUZU025JYNHPTmVzLS9sGbfYBM"

def geocode_address(address):
    print(f"📨 주소 변환 요청: {address}")
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
        return (y, x)
    else:
        print(f"❌ 주소 변환 실패: {address} → {result}")
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
    return closest

@app.route('/route', methods=['POST'])
def get_route():
    try:
        if not request.is_json:
            print("❌ JSON 형식 아님")
            return jsonify({"error": "Request must be JSON"}), 400

        data = request.get_json()
        print("📥 받은 데이터:", data)

        origin = data.get("origin")
        destination = data.get("destination")

        if not origin or not destination:
            print("❌ origin 또는 destination 없음")
            return jsonify({"error": "Missing origin or destination"}), 400

        origin_coord = geocode_address(origin)
        destination_coord = geocode_address(destination)

        if not origin_coord or not destination_coord:
            print("❌ 주소 변환 실패")
            return jsonify({"error": "Geocoding failed"}), 400

        beach_name, beach_lat, beach_lon = find_closest_beach(origin_coord)

        # 경로 요청
        url = "https://naveropenapi.apigw.ntruss.com/map-direction/v1/driving"
        headers = {
            "X-NCP-APIGW-API-KEY-ID": CLIENT_ID,
            "X-NCP-APIGW-API-KEY": CLIENT_SECRET,
        }
        params = {
            "start": f"{origin_coord[1]},{origin_coord[0]}",
            "goal": f"{destination_coord[1]},{destination_coord[0]}",
            "waypoints": f"{beach_lon},{beach_lat}",
            "option": "trafast"
        }

        response = requests.get(url, headers=headers, params=params)

        if response.status_code != 200:
            print("❌ NAVER 경로 API 실패:", response.text)
            return jsonify({"error": "Route request failed"}), 500

        print(f"✅ {origin} → {destination} 경로 생성 완료")
        return jsonify({
            "origin": origin,
            "destination": destination,
            "waypoint": {
                "name": beach_name,
                "lat": beach_lat,
                "lon": beach_lon
            },
            "route": response.json()
        })

    except Exception as e:
        print("❌ 예외 발생:", str(e))
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    print("🚀 Flask 서버 시작")
    port = int(os.environ.get("PORT", 10000))
    app.run(debug=True, host="0.0.0.0", port=port)
