import os
import requests
from flask import Flask, request, jsonify, render_template
from math import radians, cos, sin, asin, sqrt
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")
OCEANS_API_KEY = os.getenv("OCEANS_API_KEY")

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

def geocode_google(address):
    address = poi_aliases.get(address, address)
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {"address": address, "key": GOOGLE_API_KEY}
    res = requests.get(url, params=params)
    try:
        location = res.json()["results"][0]["geometry"]["location"]
        print("📍 Google 주소 변환 성공:", address, "→", location)
        return location["lat"], location["lng"]
    except:
        print("❌ Google 주소 변환 실패:", address)
        return None

def get_beaches():
    url = "https://apis.data.go.kr/1192000/service/OceansBeachInfoService1/getOceansBeachInfo1"
    params = {
        "serviceKey": OCEANS_API_KEY,
        "numOfRows": 1000,
        "pageNo": 1,
        "resultType": "json"
    }
    res = requests.get(url, params=params)
    try:
        items = res.json()['response']['body']['items']['item']
        beaches = [
            {
                'name': item.get('staNm'),
                'lat': float(item.get('lat', 0)),
                'lon': float(item.get('lon', 0))
            } for item in items if item.get('lat') and item.get('lon')
        ]
        print(f"✅ 해수욕장 {len(beaches)}개 로딩 완료")
        return beaches
    except Exception as e:
        print("❌ 해수욕장 데이터 로딩 실패:", e)
        return []

def find_waypoint_from_beaches(start, end, beaches):
    start_lat, start_lon = start
    end_lat, end_lon = end
    use_lat = abs(start_lat - end_lat) > abs(start_lon - end_lon)

    if use_lat:
        filtered = [b for b in beaches if abs(b['lat'] - start_lat) <= 0.1]
    else:
        filtered = [b for b in beaches if abs(b['lon'] - start_lon) <= 0.1]

    if not filtered:
        print("❌ 유사 위도/경도 해수욕장 없음")
        return None

    filtered.sort(key=lambda b: haversine(start_lat, start_lon, b['lat'], b['lon']))
    wp = filtered[0]
    print("✅ 선택된 해수욕장 웨이포인트:", wp['name'], wp['lat'], wp['lon'])
    return (wp['lat'], wp['lon'])

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
        print("⚠️ v1 실패, v15 시도 중...")
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
            return {"error": "NAVER 응답에 route 없음"}, 500
    except Exception as e:
        return {"error": str(e)}, 500

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/route", methods=["POST"])
def route():
    try:
        data = request.get_json()
        start = geocode_google(data.get("start"))
        end = geocode_google(data.get("end"))
        if not start or not end:
            return jsonify({"error": "❌ 주소 변환 실패"}), 400

        beaches = get_beaches()
        waypoint = find_waypoint_from_beaches(start, end, beaches)
        if not waypoint:
            return jsonify({"error": "❌ 연결 가능한 해수욕장 없음"}), 500

        route_data, status = get_naver_route(start, waypoint, end)
        if "error" in route_data:
            return jsonify({"error": f"❌ 경로 요청 실패: {route_data.get('error')}" }), status

        return jsonify(route_data)
    except Exception as e:
        print("❌ 서버 오류:", str(e))
        return jsonify({"error": f"❌ 서버 내부 오류: {str(e)}"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
