import os
import requests
from flask import Flask, request, jsonify, render_template
from math import radians, cos, sin, asin, sqrt
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
NAVER_CLIENT_ID = os.getenv("NAVER_API_KEY_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_API_KEY_SECRET")
OCEANS_API_KEY = os.getenv("OCEANS_API_KEY")

poi_aliases = {
    "세종시청": "세종특별자치시 한누리대로 2130",
    "속초시청": "강원도 속초시 중앙로 183"
}

def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
    return 2 * R * asin(sqrt(a))

def geocode_google(address):
    address = poi_aliases.get(address, address)
    if not GOOGLE_API_KEY:
        print("❌ GOOGLE_API_KEY 누락")
        return None
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {"address": address, "key": GOOGLE_API_KEY}
    try:
        res = requests.get(url, params=params)
        res.raise_for_status()
        result = res.json()["results"][0]["geometry"]["location"]
        print("📍 주소 변환 성공:", result)
        return result["lat"], result["lng"]
    except Exception as e:
        print("❌ 주소 변환 실패:", res.text)
        return None

def get_beaches():
    if not OCEANS_API_KEY:
        print("❌ OCEANS_API_KEY 누락")
        return []

    url = "https://apis.data.go.kr/1192000/service/OceansBeachInfoService1/getOceansBeachInfo1"
    params = {
        "serviceKey": OCEANS_API_KEY,
        "numOfRows": 1000,
        "pageNo": 1,
        "resultType": "json"
    }

    res = None
    try:
        res = requests.get(url, params=params, timeout=10)
        res.raise_for_status()
        json_data = res.json()
        print("🌊 해수욕장 응답 미리보기:", json_data)

        items = (
            json_data.get("response", {})
            .get("body", {})
            .get("items", {})
            .get("item", [])
        )

        if not items:
            print("⚠️ 해수욕장 항목 없음 또는 응답 구조 오류")
            return []

        beaches = [
            {
                'name': item.get('staNm'),
                'lat': float(item.get('lat', 0)),
                'lon': float(item.get('lon', 0))
            } for item in items if item.get('lat') and item.get('lon')
        ]
        print(f"✅ 해수욕장 {len(beaches)}개 로딩 완료")
        return beaches

    except requests.exceptions.SSLError as ssl_err:
        print("❌ SSL 오류 발생: 해수욕장 API 연결 불가:", ssl_err)
        return []
    except Exception as e:
        print("❌ 해수욕장 API 실패:", str(e))
        if res:
            try:
                print("🔻 응답 내용:", res.text)
            except:
                pass
        return []

def find_waypoint_from_beaches(start, end, beaches):
    start_lat, start_lon = start
    end_lat, end_lon = end
    use_lat = abs(start_lat - end_lat) > abs(start_lon - end_lon)

    # 1차 필터: ±0.1도 (약 11km)
    filtered = [b for b in beaches if abs((b['lat'] if use_lat else b['lon']) - (start_lat if use_lat else start_lon)) <= 0.1]

    if not filtered:
        print("⚠️ 유사 해수욕장 없음 → 전체 탐색으로 전환")
        filtered = beaches

    if not filtered:
        print("❌ 해수욕장 데이터 자체 없음")
        return None

    filtered.sort(key=lambda b: haversine(start_lat, start_lon, b['lat'], b['lon']))
    wp = filtered[0]
    print("✅ 선택된 waypoint:", wp['name'], wp['lat'], wp['lon'])
    return (wp['lat'], wp['lon'])

def get_naver_route(start, waypoint, end):
    if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
        print("❌ NAVER API 키 누락")
        return {"error": "NAVER API 키 누락"}, 500

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

    try:
        res = build(1)
        if res.status_code != 200:
            print("⚠️ NAVER v1 실패 → v15 시도")
            res = build(15)

        print("📡 NAVER 응답코드:", res.status_code)
        data = res.json()
        if "route" in data and "trafast" in data["route"]:
            path = data["route"]["trafast"][0]["path"]
            return {
                "type": "FeatureCollection",
                "features": [{
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[lon, lat] for lat, lon in path]
                    },
                    "properties": {}
                }]
            }, 200
        else:
            return {"error": "NAVER 응답에 route 없음"}, 500
    except Exception as e:
        print("❌ NAVER 오류:", str(e))
        return {"error": str(e)}, 500

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/route", methods=["POST"])
def route():
    print("✅ /route 요청 수신됨")
    try:
        data = request.get_json()
        print("📦 입력 데이터:", data)

        if not data or "start" not in data or "end" not in data:
            return jsonify({"error": "❌ 입력값 부족"}), 400

        start = geocode_google(data.get("start"))
        end = geocode_google(data.get("end"))
        if not start or not end:
            return jsonify({"error": "❌ 주소 변환 실패"}), 400

        beaches = get_beaches()
        waypoint = find_waypoint_from_beaches(start, end, beaches)
        if not waypoint:
            return jsonify({"error": "❌ 해수욕장 검색 실패"}), 500

        route_data, status = get_naver_route(start, waypoint, end)
        if "error" in route_data:
            return jsonify({"error": route_data["error"]}), status

        print("✅ 경로 계산 완료")
        return jsonify(route_data)

    except Exception as e:
        import traceback
        print("❌ 서버 내부 오류 발생:", str(e))
        traceback.print_exc()
        return jsonify({"error": f"❌ 서버 내부 오류: {str(e)}"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
