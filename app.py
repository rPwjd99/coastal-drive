import os
import pandas as pd
import requests
from flask import Flask, request, jsonify, render_template
from math import radians, cos, sin, asin, sqrt

app = Flask(__name__)

# NAVER API
NAVER_ID = "4etplzn46c"
NAVER_SECRET = "mHHltk1um0D09kTbRbbdJLN0MDpA0SXLboPlHx1F"

# 도로 끝점 로딩
ROAD_CSV_PATH = os.path.join(os.path.dirname(__file__), "road_endpoints_reduced.csv")
road_points = pd.read_csv(ROAD_CSV_PATH, low_memory=False)

# 해안선 필터링
def filter_coastal_points(df):
    east = df[(df['y'] >= 35) & (df['y'] <= 38) & (df['x'] >= 128) & (df['x'] <= 131)]
    south = df[(df['y'] >= 33) & (df['y'] <= 35) & (df['x'] >= 126) & (df['x'] <= 129)]
    west = df[(df['y'] >= 34) & (df['y'] <= 38) & (df['x'] >= 124) & (df['x'] <= 126)]
    return pd.concat([east, south, west]).drop_duplicates()

coastal_points = filter_coastal_points(road_points)

# 거리 계산 함수
def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
    return 2 * R * asin(sqrt(a))

# 주소 → 좌표 (NAVER Geocoding API)
def geocode_naver(address):
    url = "https://naveropenapi.apigw.ntruss.com/map-geocode/v2/geocode"
    headers = {
        "X-NCP-APIGW-API-KEY-ID": NAVER_ID,
        "X-NCP-APIGW-API-KEY": NAVER_SECRET
    }
    params = { "query": address }
    try:
        res = requests.get(url, headers=headers, params=params, timeout=5)
        data = res.json()
        if data.get("addresses"):
            addr = data["addresses"][0]
            lat, lon = float(addr["y"]), float(addr["x"])
            print(f"📍 주소 변환 성공: {address} → ({lat}, {lon})")
            return lat, lon
    except Exception as e:
        print("❌ 주소 변환 예외:", e)
    print(f"❌ 주소 변환 실패: {address}")
    return None

# 해안 경유지 선택
def find_best_waypoint(start, end):
    start_lat, start_lon = start
    end_lat, end_lon = end

    use_lat = abs(start_lat - end_lat) > abs(start_lon - end_lon)
    rounded_lat = round(start_lat, 2)
    rounded_lon = round(start_lon, 2)

    if use_lat:
        candidates = coastal_points[coastal_points['y'].round(2) == rounded_lat]
        direction = lambda row: (end_lon - start_lon) * (row['x'] - start_lon) > 0
    else:
        candidates = coastal_points[coastal_points['x'].round(2) == rounded_lon]
        direction = lambda row: (end_lat - start_lat) * (row['y'] - start_lat) > 0

    candidates = candidates[candidates.apply(direction, axis=1)]

    if candidates.empty:
        return None

    candidates["dist_to_end"] = candidates.apply(
        lambda row: haversine(row["y"], row["x"], end_lat, end_lon), axis=1
    )

    selected = candidates.sort_values("dist_to_end").iloc[0]
    print("📍 선택된 waypoint:", selected["y"], selected["x"])
    return selected["y"], selected["x"]

# NAVER Directions 15 API 호출
def get_naver_route(start, waypoint, end):
    headers = {
        "X-NCP-APIGW-API-KEY-ID": NAVER_ID,
        "X-NCP-APIGW-API-KEY": NAVER_SECRET
    }
    params = {
        "start": f"{start[1]},{start[0]}",
        "goal": f"{end[1]},{end[0]}",
        "option": "trafast",
        "cartype": 1,
        "fueltype": "gasoline",
        "mileage": 14,
        "lang": "ko"
    }
    if waypoint:
        params["waypoints"] = f"{waypoint[1]},{waypoint[0]}"

    res = requests.get("https://naveropenapi.apigw.ntruss.com/map-direction-15/v1/driving", headers=headers, params=params)
    print("📡 NAVER 응답코드:", res.status_code)
    try:
        return res.json(), res.status_code
    except Exception as e:
        return {"error": str(e)}, 500

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/route", methods=["POST"])
def route():
    try:
        data = request.get_json()
        start_addr = data.get("start")
        end_addr = data.get("end")

        start = geocode_naver(start_addr)
        end = geocode_naver(end_addr)

        if not start or not end:
            return jsonify({"error": "❌ 주소 변환 실패"}), 400

        waypoint = find_best_waypoint(start, end)
        if not waypoint:
            return jsonify({"error": "❌ 경유지 탐색 실패"}), 500

        route_data, status = get_naver_route(start, waypoint, end)
        return jsonify(route_data)

    except Exception as e:
        print("❌ 서버 오류:", str(e))
        return jsonify({"error": f"서버 내부 오류: {str(e)}"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
