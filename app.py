import os
import requests
from flask import Flask, request, jsonify, render_template
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# API Keys
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# 주요 기관 POI → 도로명 주소 변환용
poi_aliases = {
    "세종시청": "세종특별자치시 한누리대로 2130",
    "속초시청": "강원도 속초시 중앙로 183",
    "서울역": "서울특별시 중구 한강대로 405",
    "대전역": "대전광역시 동구 중앙로 215"
}

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
        return float(item["y"]), float(item["x"])
    except:
        print("❌ NAVER 주소 변환 실패:", address)
        return None

def geocode_google(address):
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    res = requests.get(url, params={"address": address, "key": GOOGLE_API_KEY})
    try:
        loc = res.json()["results"][0]["geometry"]["location"]
        return loc["lat"], loc["lng"]
    except:
        print("❌ Google 주소 변환 실패:", address)
        return None

def geocode(address):
    result = geocode_naver(address)
    if result:
        return result
    return geocode_google(address)

def get_naver_route(start, waypoint, end):
    def build(version):
        url = f"https://naveropenapi.apigw.ntruss.com/map-direction/v{version}/driving"
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

    res = build(1)
    if res.status_code != 200:
        res = build(15)

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
        return {"error": "NAVER 응답에 route 데이터 없음"}, 500
    except Exception as e:
        return {"error": str(e)}, 500

def generate_candidate_waypoints(start, end):
    lat, lon = start
    use_lat = abs(start[0] - end[0]) > abs(start[1] - end[1])
    direction = "latitude" if use_lat else "longitude"
    candidates = []

    for i in range(-10, 11):
        delta = i * 0.01
        if direction == "latitude":
            candidates.append((lat + delta, lon))
        else:
            candidates.append((lat, lon + delta))

    return candidates

def find_connected_waypoint(start, end):
    candidates = generate_candidate_waypoints(start, end)
    print(f"🔍 후보 웨이포인트 {len(candidates)}개 생성")
    for waypoint in candidates:
        route_data, status = get_naver_route(start, waypoint, end)
        if status == 200:
            print("✅ 연결 성공 웨이포인트:", waypoint)
            return waypoint
        else:
            print("❌ 연결 실패:", waypoint)
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

        waypoint = find_connected_waypoint(start, end)
        if not waypoint:
            return jsonify({"error": "❌ 연결 가능한 웨이포인트 탐색 실패"}), 500

        route_data, status = get_naver_route(start, waypoint, end)
        return jsonify(route_data), status
    except Exception as e:
        print("❌ 서버 오류:", str(e))
        return jsonify({"error": f"서버 오류: {str(e)}"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
