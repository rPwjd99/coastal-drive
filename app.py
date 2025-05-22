from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import pandas as pd
import requests
from geopy.distance import geodesic

app = Flask(__name__)
CORS(app)

# NAVER API 인증정보
NAVER_CLIENT_ID = "vsdzf1f4n5"
NAVER_CLIENT_SECRET = "0gzctO51PUTVv0gUZU025JYNHPTmVzLS9sGbfYBM"

# 해안 도로 종점 데이터 로딩
road_df = pd.read_csv("road_endpoints_reduced.csv", encoding="utf-8-sig")

@app.route("/", methods=["GET"])
def home():
    return "<h2>✅ Coastal Drive Flask 서버 작동 중</h2><p>POST /route 엔드포인트를 사용하세요.</p>"

# NAVER 주소 → 좌표 변환
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
            return y, x
    return None

# NAVER 경로 탐색 API
def get_route(start, waypoint, end):
    url = "https://naveropenapi.apigw.ntruss.com/map-direction/v1/driving"
    headers = {
        "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID,
        "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET
    }
    params = {
        "start": f"{start[1]},{start[0]}",
        "goal": f"{end[1]},{end[0]}",
        "waypoints": f"{waypoint[1]},{waypoint[0]}",
        "option": "trafast"
    }
    res = requests.get(url, headers=headers, params=params)
    if res.status_code == 200 and "route" in res.json():
        return res.json()
    return None

# 웨이포인트 후보 중 도로 연결 가능하고 해안 3km 이내인 가장 가까운 지점 찾기
def find_nearest_coastal_waypoint(origin):
    origin_point = (origin[0], origin[1])  # (lat, lon)
    road_df["distance"] = road_df.apply(
        lambda row: geodesic(origin_point, (row["y"], row["x"])).meters, axis=1
    )
    candidates = road_df[road_df["distance"] <= 3000].sort_values(by="distance")

    for _, row in candidates.iterrows():
        waypoint = (row["y"], row["x"])
        test = get_route(origin, waypoint, origin)
        if test and "route" in test:
            return waypoint
    return None

@app.route("/route", methods=["POST"])
def route():
    data = request.json
    start_addr = data.get("start")
    end_addr = data.get("end")

    start_coord = geocode_naver(start_addr)
    end_coord = geocode_naver(end_addr)

    if not start_coord or not end_coord:
        return jsonify({"error": "❌ 주소 변환 실패"}), 400

    waypoint = find_nearest_coastal_waypoint(start_coord)
    if not waypoint:
        return jsonify({"error": "❌ 웨이포인트 탐색 실패"}), 400

    result = get_route(start_coord, waypoint, end_coord)
    if not result:
        return jsonify({"error": "❌ 경로 요청 실패"}), 500

    return jsonify(result)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
