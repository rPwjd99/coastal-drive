from flask import Flask, request, jsonify, render_template
import requests
import geopandas as gpd
from shapely.geometry import Point
import math
import os

app = Flask(__name__)

# Naver API 인증 정보
NAVER_CLIENT_ID = "vsdzf1f4n5"
NAVER_CLIENT_SECRET = "0gzctO51PUTVv0gUZU025JYNHPTmVzLS9sGbfYBM"

# 해안선 데이터 불러오기
gdf = gpd.read_file("coastal_route_result.geojson")
coastal_points = list(gdf.geometry[0].coords)

# 두 좌표 사이 거리 계산 (Haversine 공식)
def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return 2*R*math.atan2(math.sqrt(a), math.sqrt(1 - a))

# 가장 가까운 해안선 좌표 반환
def get_nearest_coast(lat, lon):
    min_dist = float('inf')
    nearest = None
    for pt in coastal_points:
        dist = haversine(lat, lon, pt[1], pt[0])
        if dist < min_dist:
            min_dist = dist
            nearest = pt
    return nearest if min_dist <= 5 else None

# 주소 → 좌표 (VWorld 또는 Google 대신 예제용)
def geocode(address):
    url = "https://naveropenapi.apigw.ntruss.com/map-geocode/v2/geocode"
    headers = {
        "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID,
        "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET
    }
    params = {"query": address}
    res = requests.get(url, headers=headers, params=params)
    data = res.json()
    if data.get("addresses"):
        addr = data["addresses"][0]
        return float(addr["y"]), float(addr["x"]), addr["roadAddress"]
    return None, None, None

# 경로 계산 (출발지 → 경유 → 목적지)
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
        "option": "traoptimal"
    }
    res = requests.get(url, headers=headers, params=params)
    return res.json()

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/route', methods=['POST'])
def route():
    data = request.get_json()
    start_addr = data.get("start")
    end_addr = data.get("end")

    start_lat, start_lon, start_fmt = geocode(start_addr)
    end_lat, end_lon, end_fmt = geocode(end_addr)

    if not start_lat or not end_lat:
        return jsonify({"error": "❌ 주소 인식 실패"}), 500

    wpt = get_nearest_coast(start_lat, start_lon)
    if not wpt:
        return jsonify({"error": "❌ 인근 해안선 좌표 없음"}), 500

    try:
        route_result = get_route((start_lat, start_lon), wpt, (end_lat, end_lon))
        if 'route' not in route_result:
            raise Exception("경로 없음")
        return jsonify({
            "start_corrected": start_fmt,
            "end_corrected": end_fmt,
            "route": route_result
        })
    except Exception as e:
        print("❌ 경로 계산 오류:", e)
        return jsonify({"error": "❌ 경로 계산 실패"}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000, debug=True)
