# app.py
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
from math import radians, sin, cos, sqrt, atan2

# 📦 해안 웨이포인트 불러오기
try:
    from beaches_coordinates import beach_coords
    print("✅ beach_coords 로딩 성공: 총", len(beach_coords), "개 지점")
except Exception as e:
    print("❌ beach_coords 로딩 실패:", e)
    raise

app = Flask(__name__)
CORS(app)

# 🔑 NAVER API 인증 정보
CLIENT_ID = "vsdzf1f4n5"
CLIENT_SECRET = "0gzctO51PUTVv0gUZU025JYNHPTmVzLS9sGbfYBM"

# 📍 주소 → 좌표 변환
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
        print(f"📌 변환 결과: {address} → (lat: {y}, lon: {x})")
        return (y, x)
    else:
        print(f"❌ 주소 변환 실패: {address}")
        return None

# 📏 거리 계산 (Haversine)
def haversine(coord1, coord2):
    R = 6371.0
    lat1, lon1 = map(radians, coord1)
    lat2, lon2 = map(radians, coord2)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat/2)**2 + cos(lat1)*cos(lat2)*sin(dlon/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return R * c

# 🌊 가장 가까운 해안 웨이포인트 찾기
def find_closest_beach(start_coord):
    print("🔍 해안 웨이포인트 탐색 시작")
    min_dist = float("inf")
    closest = None
    for name, (lon, lat) in beach_coords.items():
        dist = haversine(start_coord, (lat, lon))
        print(f" ↳ {name}: {dist:.2f}km")
        if dist < min_dist:
            min_dist = dist
            closest = (name, lat, lon)
    print(f"✅ 최종 선택된 해안: {closest[0]} ({min_dist:.2f}km)")
    return closest

# 🧭 경로 API 처리
@app.route('/route', methods=['POST'])
def get_route():
    try:
        data = request.get_json()
        print("📥 받은 요청:", data)

        origin_addr = data.get("origin")
        destination_addr = data.get("destination")

        if not origin_addr or not destination_addr:
            print("❌ 요청값 누락")
            return jsonify({"error": "Missing origin or destination"}), 400

        origin_coord = geocode_address(origin_addr)
        destination_coord = geocode_address(destination_addr)

        if not origin_coord or not destination_coord:
            return jsonify({"error": "Address geocoding failed"}), 400

        # 해안선 우회 포인트
        beach_name, beach_lat, beach_lon = find_closest_beach(origin_coord)

        # NAVER Directions API 호출
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

        print("📡 NAVER Directions API 요청 중...")
        response = requests.get(url, headers=headers, params=params)

        if response.status_code == 200:
            print("✅ 경로 요청 성공")
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
            print("❌ 경로 요청 실패:", response.text)
            return jsonify({"error": "Route request failed"}), 500

    except Exception as e:
        print("❌ 서버 예외 발생:", str(e))
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    print("🚀 Flask 서버 시작 중...")
    app.run(debug=True)
