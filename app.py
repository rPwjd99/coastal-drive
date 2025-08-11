# app.py
# 기존 구조 유지: Flask + render_template, os, requests, dotenv, beach_coords, haversine

from flask import Flask, request, jsonify, render_template
import os
import requests
from dotenv import load_dotenv
from beaches_coordinates import beach_coords
from math import radians, cos, sin, asin, sqrt

# 환경변수 로드
load_dotenv()

# 환경변수 키 (기존과 동일하게 사용)
ORS_API_KEY = os.getenv("ORS_API_KEY")
TOURAPI_KEY = os.getenv("TOURAPI_KEY")
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")

app = Flask(__name__)

# 위경도 거리 계산 (정확도 유지)
def haversine(lon1, lat1, lon2, lat2):
    lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * asin(sqrt(a))
    return 6371 * c  # km

# 해수욕장 경유지 2곳 선택: 시작점에서 가까운 순 2개
# (원하시면 "경로 상에서 실제로 먼저/다음에 만나는 2곳"으로 고도화 가능)
def get_two_nearest_beaches(start):
    start_lon, start_lat = start
    distances = []
    for b in beach_coords:
        dist = haversine(start_lon, start_lat, b["lon"], b["lat"])
        distances.append((dist, b))
    distances.sort(key=lambda x: x[0])
    return [distances[0][1], distances[1][1]]

@app.route("/")
def home():
    return render_template("index.html")

# 주소 → 좌표 (NAVER Geocoding)
@app.route("/geocode", methods=["POST"])
def geocode():
    data = request.json
    query = data.get("query", "").strip()
    if not query:
        return jsonify({"error": "empty query"}), 400

    url = "https://naveropenapi.apigw.ntruss.com/map-geocode/v2/geocode"
    headers = {
        "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID,
        "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET
    }
    params = {"query": query}

    r = requests.get(url, headers=headers, params=params, timeout=10)
    j = r.json()
    addrs = j.get("addresses", [])
    if not addrs:
        return jsonify({"error": "no result"}), 404

    lon = float(addrs[0]["x"])
    lat = float(addrs[0]["y"])
    return jsonify({"lon": lon, "lat": lat})

# 경유지 2개 포함 경로 (OpenRouteService)
@app.route("/route", methods=["POST"])
def route():
    data = request.json
    start = data["start"]  # [lon, lat]
    end = data["end"]      # [lon, lat]

    # 해수욕장 2곳 선택
    waypoints = get_two_nearest_beaches(start)

    # ORS는 [ [lon,lat], ... ] 순서
    coords = [
        start,
        [waypoints[0]["lon"], waypoints[0]["lat"]],
        [waypoints[1]["lon"], waypoints[1]["lat"]],
        end
    ]

    ors_url = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
    headers = {"Authorization": ORS_API_KEY, "Content-Type": "application/json"}
    body = {
        "coordinates": coords,
        "instructions": False
    }

    res = requests.post(ors_url, headers=headers, json=body, timeout=25)
    route_data = res.json()

    return jsonify({
        "route": route_data,
        "waypoints": waypoints
    })

# 경유지 주변 관광지/맛집/카페 (TourAPI) - 이미지/운영시간/주차 정보 포함 (있을 때만)
@app.route("/tourspot", methods=["POST"])
def tourspot():
    data = request.json
    lat = data["lat"]
    lon = data["lon"]

    results = []

    # contentTypeId: 39=음식점, 38=카페, 12=관광지
    for content_type, label in [("39", "restaurant"), ("38", "cafe"), ("12", "tourist")]:
        url = "http://apis.data.go.kr/B551011/KorService1/locationBasedList1"
        params = {
            "serviceKey": TOURAPI_KEY,
            "MobileOS": "ETC",
            "MobileApp": "coastalDrive",
            "mapX": lon,
            "mapY": lat,
            "radius": 5000,
            "contentTypeId": content_type,
            "_type": "json"
        }

        try:
            res = requests.get(url, params=params, timeout=15)
            j = res.json()
            items = j["response"]["body"]["items"].get("item", [])
        except Exception:
            items = []

        # 응답 항목에 따라 값이 없을 수 있으므로 None 안전 처리
        for item in items:
            results.append({
                "name": item.get("title") or "",
                "addr": item.get("addr1") or "",
                "lat": item.get("mapY"),
                "lon": item.get("mapX"),
                "img": item.get("firstimage"),
                "type": label,
                "info": " ".join(v for v in [item.get("parking"), item.get("usetime")] if v).strip()
            })

    return jsonify(results)

if __name__ == "__main__":
    # Render 등 클라우드에서 필수: 0.0.0.0 + PORT 바인딩
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
