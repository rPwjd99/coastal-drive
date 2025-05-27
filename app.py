import os
import json
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

NAVER_CLIENT_ID = os.environ.get("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET")
NAVER_DIRECTIONS_URL = "https://naveropenapi.apigw.ntruss.com/map-direction/v1/driving"
NAVER_GEOCODE_URL = "https://naveropenapi.apigw.ntruss.com/map-geocode/v2/geocode"

# ✅ 주소를 위경도로 변환
def geocode_address(address):
    headers = {
        "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID,
        "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET
    }
    params = {"query": address}
    res = requests.get(NAVER_GEOCODE_URL, headers=headers, params=params)
    if res.status_code != 200 or not res.json().get("addresses"):
        return None
    addr = res.json()["addresses"][0]
    return float(addr["x"]), float(addr["y"])

# ✅ 웨이포인트 후보를 자동으로 선정 (위도 정렬 + 해안 근처)
def select_waypoint(start, end, beaches):
    sx, sy = start
    ex, ey = end
    use_lat = abs(sy - ey) > abs(sx - ex)
    target_val = sy if use_lat else sx

    # 후보 중 가장 가까운 것을 선택
    sorted_beaches = sorted(beaches.items(), key=lambda item: abs(item[1][1 if use_lat else 0] - target_val))
    for name, coord in sorted_beaches:
        if get_driving_time(start, coord) is not None:
            return coord
    return None

# ✅ 경로 계산 (출발 → 웨이포인트 → 도착)
def get_driving_route(coords):
    headers = {
        "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID,
        "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET
    }
    params = {
        "start": f"{coords[0][0]},{coords[0][1]}",
        "goal": f"{coords[2][0]},{coords[2][1]}",
        "waypoints": f"{coords[1][0]},{coords[1][1]}",
        "option": "trafast"
    }
    res = requests.get(NAVER_DIRECTIONS_URL, headers=headers, params=params)
    if res.status_code != 200:
        return None
    return res.json()

# ✅ 특정 구간 도달 가능 여부만 체크

def get_driving_time(p1, p2):
    headers = {
        "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID,
        "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET
    }
    params = {
        "start": f"{p1[0]},{p1[1]}",
        "goal": f"{p2[0]},{p2[1]}",
        "option": "trafast"
    }
    res = requests.get(NAVER_DIRECTIONS_URL, headers=headers, params=params)
    if res.status_code != 200:
        return None
    data = res.json()
    try:
        return data['route']['trafast'][0]['summary']['duration']
    except:
        return None

# ✅ 좌표가 담긴 해수욕장 목록 불러오기
from beaches_coordinates import beach_coords

@app.route("/route", methods=["POST"])
def coastal_route():
    data = request.get_json()
    start_addr = data.get("start")
    end_addr = data.get("end")

    if not start_addr or not end_addr:
        return jsonify({"error": "start, end 주소가 필요합니다."}), 400

    start_coord = geocode_address(start_addr)
    end_coord = geocode_address(end_addr)
    if not start_coord or not end_coord:
        return jsonify({"error": "주소 지오코딩 실패"}), 400

    waypoint_coord = select_waypoint(start_coord, end_coord, beach_coords)
    if not waypoint_coord:
        return jsonify({"error": "유효한 해안선 웨이포인트를 찾을 수 없습니다."}), 404

    full_route = get_driving_route([start_coord, waypoint_coord, end_coord])
    if not full_route:
        return jsonify({"error": "경로 계산 실패"}), 500

    return jsonify({
        "start": start_coord,
        "waypoint": waypoint_coord,
        "end": end_coord,
        "route": full_route
    })

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
