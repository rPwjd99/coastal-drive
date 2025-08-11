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

# 환경변수 키
ORS_API_KEY = os.getenv("ORS_API_KEY")
TOURAPI_KEY = os.getenv("TOURAPI_KEY")
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")
VWORLD_KEY = os.getenv("VWORLD_KEY")  # 백업 지오코딩용(있으면 사용)

app = Flask(__name__)

# 위경도 거리 계산
def haversine(lon1, lat1, lon2, lat2):
    lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * asin(sqrt(a))
    return 6371 * c  # km

# 시작점 기준 가까운 해수욕장 2곳 선택
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

# 주소 → 좌표 (NAVER 다단계 + VWORLD 백업)
@app.route("/geocode", methods=["POST"])
def geocode():
    data = request.json
    raw_query = (data.get("query") or "").strip()
    if not raw_query:
        return jsonify({"error": "empty query"}), 400

    def naver_geocode(q):
        url = "https://naveropenapi.apigw.ntruss.com/map-geocode/v2/geocode"
        headers = {
            "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID,
            "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET
        }
        r = requests.get(url, headers=headers, params={"query": q}, timeout=10)
        j = r.json()
        addrs = j.get("addresses", [])
        if addrs:
            return float(addrs[0]["x"]), float(addrs[0]["y"])  # lon, lat
        return None

    def vworld_geocode(q):
        if not VWORLD_KEY:
            return None
        url = "https://api.vworld.kr/req/address"
        params = {
            "service": "address",
            "request": "getCoord",
            "format": "json",
            "crs": "epsg:4326",
            "key": VWORLD_KEY,
            "type": "road",
            "address": q
        }
        r = requests.get(url, params=params, timeout=10)
        try:
            res = r.json()["response"]["result"]
            if res:
                x = float(res[0]["point"]["x"])
                y = float(res[0]["point"]["y"])
                return x, y
        except Exception:
            pass
        # 도로명 실패 시 지번 재시도
        params["type"] = "parcel"
        r = requests.get(url, params=params, timeout=10)
        try:
            res = r.json()["response"]["result"]
            if res:
                x = float(res[0]["point"]["x"])
                y = float(res[0]["point"]["y"])
                return x, y
        except Exception:
            pass
        return None

    # NAVER 재시도 목록(제주 축약 주소 대비)
    tries = [
        raw_query,
        f"제주특별자치도 {raw_query}",
        f"제주시 {raw_query}",
        f"서귀포시 {raw_query}",
    ]

    # 1) NAVER 순차 시도
    for q in tries:
        try:
            res = naver_geocode(q)
            if res:
                lon, lat = res
                return jsonify({"lon": lon, "lat": lat})
        except Exception:
            continue

    # 2) VWORLD 백업(있을 때)
    for q in tries:
        try:
            res = vworld_geocode(q)
            if res:
                lon, lat = res
                return jsonify({"lon": lon, "lat": lat})
        except Exception:
            continue

    return jsonify({"error": "geocode_failed", "hint": "주소에 시/도 정보를 포함해 보세요"}), 404

# ORS 경로 (경유지 2개: 출발 → 해수욕장1 → 해수욕장2 → 도착)
@app.route("/route", methods=["POST"])
def route():
    data = request.json
    start = data["start"]  # [lon, lat]
    end = data["end"]      # [lon, lat]

    waypoints = get_two_nearest_beaches(start)

    coords = [
        start,
        [waypoints[0]["lon"], waypoints[0]["lat"]],
        [waypoints[1]["lon"], waypoints[1]["lat"]],
        end
    ]

    ors_url = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
    headers = {"Authorization": ORS_API_KEY, "Content-Type": "application/json"}
    body = {"coordinates": coords, "instructions": False}

    res = requests.post(ors_url, headers=headers, json=body, timeout=25)
    route_data = res.json()

    return jsonify({"route": route_data, "waypoints": waypoints})

# 경유지 주변 5km: 맛집(39)·카페(38)·관광지(12) — 사진/운영시간/주차(있으면)
@app.route("/tourspot", methods=["POST"])
def tourspot():
    data = request.json
    lat = data["lat"]
    lon = data["lon"]

    results = []
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
            r = requests.get(url, params=params, timeout=15)
            j = r.json()
            items = j["response"]["body"]["items"].get("item", [])
        except Exception:
            items = []

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
    # Render 등 배포 환경 필수: 0.0.0.0 + PORT
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
