from flask import Flask, request, jsonify
from flask_cors import CORS
import pandas as pd
import requests
from geopy.distance import geodesic

app = Flask(__name__)
CORS(app)

# NAVER API 인증정보
NAVER_CLIENT_ID = "vsdzf1f4n5"
NAVER_CLIENT_SECRET = "0gzctO51PUTVv0gUZU025JYNHPTmVzLS9sGbfYBM"

# 해안도로 종점 데이터 불러오기
road_df = pd.read_csv("road_endpoints_reduced.csv", encoding="utf-8-sig")

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
            x = float(data["addresses"][0]["x"])
            y = float(data["addresses"][0]["y"])
            return y, x
    return None

# NAVER Directions API 호출
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
    if res.status_code == 200:
        return res.json()
    return None

# 가장 가까운 종점 찾기 (1km 이내)
def find_nearest_road_point(origin):
    origin_point = (origin[0], origin[1])
    road_df["dist"] = road_df.apply(lambda row: geodesic(origin_point, (row["y"], row["x"])).meters, axis=1)
    candidates = road_df[road_df["dist"] <= 1000].sort_values(by="dist")
    for _, row in candidates.iterrows():
        candidate_point = (row["y"], row["x"])
        test_route = get_route(origin, candidate_point, origin)  # 출발지→해안→출발지 (테스트용)
        if test_route is not None and test_route.get("route"):
            return candidate_point
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

    coast_point = find_nearest_road_point(start_coord)
    if not coast_point:
        return jsonify({"error": "❌ 해안지점 찾기 실패"}), 400

    result = get_route(start_coord, coast_point, end_coord)
    if not result or not result.get("route"):
        return jsonify({"error": "❌ 경로 요청 실패"}), 500

    return jsonify(result)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
