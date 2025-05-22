import os
import requests
from flask import Flask, request, jsonify, render_template
from math import radians, cos, sin, asin, sqrt
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

poi_aliases = {
    "세종시청": "세종특별자치시 한누리대로 2130",
    "속초시청": "강원도 속초시 중앙로 183",
    "서울역": "서울특별시 중구 한강대로 405",
    "대전역": "대전광역시 동구 중앙로 215"
}

def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
    return 2 * R * asin(sqrt(a))

def geocode_naver(address):
    address = poi_aliases.get(address, address)
    url = "https://naveropenapi.apigw.ntruss.com/map-geocode/v2/geocode"
    headers = {
        "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID,
        "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET
    }
    params = {"query": address}
    res = requests.get(url, headers=headers, params=params)
    try:
        item = res.json()["addresses"][0]
        lat = float(item["y"])
        lon = float(item["x"])
        print("📍 NAVER 주소 변환 성공:", address, "→", lat, lon)
        return lat, lon
    except:
        print("❌ NAVER 주소 변환 실패:", address)
        return None

def geocode_google(address):
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    res = requests.get(url, params={"address": address, "key": GOOGLE_API_KEY})
    try:
        location = res.json()["results"][0]["geometry"]["location"]
        print("📍 Google 주소 변환 성공:", address, "→", location)
        return location["lat"], location["lng"]
    except:
        print("❌ Google 주소 변환 실패:", address)
        return None

def geocode(address):
    result = geocode_naver(address)
    if result:
        return result
    print("➡️ NAVER 실패, Google 시도 중...")
    return geocode_google(address)

def get_naver_route(start, waypoint, end):
    def build_route(api_version):
        url = f"https://naveropenapi.apigw.ntruss.com/map-direction/v{api_version}/driving"
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
        return requests.get(url, headers=headers, params=params)

    res = build_route(1)
    if res.status_code != 200:
        print("⚠️ Directions v1 실패, v15 시도 중...")
        res = build_route(15)

    print("📡 NAVER 응답코드:", res.status_code)
    try:
        data = res.json()
        if "route" in data and "trafast" in data["route"]:
            path = data["route"]["trafast"][0]["path"]
            geojson = {
                "type": "FeatureCollection",
                "features": [{
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[lon, lat] for lat, lon in path]
                    },
                    "properties": {
                        "summary": data["route"]["trafast"][0]["summary"]
                    }
                }]
            }
            return geojson, 200
        else:
            return {"error": "NAVER 응답에 route 데이터 없음"}, 500
    except Exception as e:
        return {"error": str(e)}, 500

def find_coastal_waypoint(start, end):
    lat, lon = start
    use_lat = abs(start[0] - end[0]) > abs(start[1] - end[1])
    candidates = []

    for dlat in [-0.03, -0.02, -0.01, 0.01, 0.02, 0.03]:
        for dlon in [-0.03, -0.02, -0.01, 0.01, 0.02, 0.03]:
            waypoint = (lat + dlat, lon + dlon)
            # 해안선 위경도 범위 필터
            if 33 <= waypoint[0] <= 38 and 124 <= waypoint[1] <= 131:
                candidates.append(waypoint)

    candidates.sort(key=lambda w: haversine(w[0], w[1], end[0], end[1]))
    print(f"🔍 웨이포인트 후보 {len(candidates)}개 중 테스트 중...")

    for wp in candidates:
        route_data, status = get_naver_route(start, wp, end)
        if status == 200:
            print("✅ 연결 가능한 웨이포인트:", wp)
            return wp
        else:
            print("❌ 연결 실패:", wp)

    return None

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/route", methods=["POST"])
def route():
    try:
        data = request.get_json()
        start = geocode(data.get("start"))
        end = geocode(data.get("end"))
        if not start or not end:
            return jsonify({"error": "❌ 주소 변환 실패"}), 400

        waypoint = find_coastal_waypoint(start, end)
        if not waypoint:
            return jsonify({"error": "❌ 연결 가능한 웨이포인트 없음"}), 500

        route_data, status = get_naver_route(start, waypoint, end)
        if "error" in route_data:
            return jsonify({"error": f"❌ 경로 요청 실패: {route_data.get('error')}" }), status

        return jsonify(route_data)
    except Exception as e:
        print("❌ 서버 오류:", str(e))
        return jsonify({"error": f"❌ 서버 내부 오류: {str(e)}"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
