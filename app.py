# app.py

from flask import Flask, request, jsonify, render_template
import os
import requests
from dotenv import load_dotenv
from beaches_coordinates import beach_coords
from math import radians, cos, sin, asin, sqrt

load_dotenv()

NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")
TOURAPI_KEY = os.getenv("TOURAPI_KEY")

app = Flask(__name__)

def haversine(lon1, lat1, lon2, lat2):
    lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * asin(sqrt(a))
    return 6371 * c  # 지구 반지름(km)

# ✅ 기존 함수: 단일 해수욕장에서 → 해수욕장 2개로 변경
def get_two_nearest_beaches(start, end):
    start_lon, start_lat = start
    distances = []
    for beach in beach_coords:
        dist = haversine(start_lon, start_lat, beach["lon"], beach["lat"])
        distances.append((dist, beach))
    distances.sort(key=lambda x: x[0])
    return [distances[0][1], distances[1][1]]  # 거리 기준 상위 2개

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/route", methods=["POST"])
def route():
    data = request.json
    start = data["start"]
    end = data["end"]

    # ✅ 해수욕장 2곳
    waypoints = get_two_nearest_beaches(start, end)

    coords = [
        start,
        [waypoints[0]["lon"], waypoints[0]["lat"]],
        [waypoints[1]["lon"], waypoints[1]["lat"]],
        end
    ]

    headers = {
        "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID,
        "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET
    }

    params = {
        "start": f"{coords[0][0]},{coords[0][1]}",
        "goal": f"{coords[3][0]},{coords[3][1]}",
        "waypoints": f"{coords[1][0]},{coords[1][1]}|{coords[2][0]},{coords[2][1]}",
        "option": "trf"
    }

    res = requests.get("https://naveropenapi.apigw.ntruss.com/map-direction/v1/driving", headers=headers, params=params)
    route_data = res.json()

    return jsonify({
        "route": route_data,
        "waypoints": waypoints
    })

@app.route("/tourspot", methods=["POST"])
def tourspot():
    data = request.json
    lat = data["lat"]
    lon = data["lon"]

    results = []

    # ✅ 관광지 + 맛집 + 카페 추가됨 (팝업용 이미지 포함)
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

        res = requests.get(url, params=params)
        try:
            items = res.json()["response"]["body"]["items"]["item"]
        except Exception:
            items = []

        for item in items:
            results.append({
                "name": item.get("title"),
                "addr": item.get("addr1"),
                "lat": item.get("mapY"),
                "lon": item.get("mapX"),
                "img": item.get("firstimage"),
                "type": label,
                "info": f"{item.get('parking', '')} {item.get('usetime', '')}".strip()
            })

    return jsonify(results)

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
