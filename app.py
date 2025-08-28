from flask import Flask, request, jsonify, render_template
import os
import requests
from dotenv import load_dotenv
from beaches_coordinates import beach_coords  # {name: (lon, lat)}

load_dotenv()
app = Flask(__name__)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
ORS_API_KEY = os.getenv("ORS_API_KEY")
TOURAPI_KEY = os.getenv("TOURAPI_KEY", "e1tU33wjMx2nynKjH8yDBm/S4YNne6B8mpCOWtzMH9TSONF71XG/xAwPqyv1fANpgeOvbPY+Le+gM6cYCnWV8w==")

def haversine(lat1, lon1, lat2, lon2):
    from math import radians, cos, sin, asin, sqrt
    R = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1))*cos(radians(lat2))*sin(dlon/2)**2
    return 2 * R * asin(sqrt(a))

def geocode_google(address):
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    res = requests.get(url, params={"address": address, "key": GOOGLE_API_KEY, "language": "ko", "region": "kr"})
    try:
        location = res.json()["results"][0]["geometry"]["location"]
        return location["lat"], location["lng"]
    except:
        return None

def reverse_geocode_google(lat, lon):
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    res = requests.get(url, params={"latlng": f"{lat},{lon}", "key": GOOGLE_API_KEY, "language": "ko"})
    try:
        return res.json()["results"][0]["formatted_address"]
    except:
        return "주소 불러오기 실패"

def is_in_coastal_bounds(lat, lon):
    # 전국 해안 범위를 느슨하게: 동/남/서해 전반
    return (
        (34.0 <= lat <= 38.6 and 126.0 <= lon <= 131.5) or  # 한반도 본토 해안대
        (33.0 <= lat <= 33.7 and 126.0 <= lon <= 127.2)     # 제주권
    )

def find_best_beach_waypoint(start, end):
    """
    '전과 같이' 첫 경유지 찾기:
    - 출발과 위도(±0.2) 가까운 후보 중, (도착–출발) 진행방향과 같은 쪽의 롱/랏만 고려
    - 출발과 경도(±0.2) 가까운 후보도 동일 기준
    - 두 집합 각각에서 '도착과의 거리'가 가장 짧은 후보를 뽑고, 더 나은 쪽을 선택
    """
    start_lat, start_lon = start
    end_lat, end_lon = end
    lat_candidates, lon_candidates = [], []

    for name, (lon, lat) in beach_coords.items():
        if not is_in_coastal_bounds(lat, lon):
            continue

        # 위도 유사 + 진행방향 체크(경도 방향으로 전진)
        if abs(lat - start_lat) < 0.2 and (end_lon - start_lon) * (lon - start_lon) > 0:
            lat_candidates.append((name, lat, lon, haversine(end_lat, end_lon, lat, lon)))

        # 경도 유사 + 진행방향 체크(위도 방향으로 전진)
        if abs(lon - start_lon) < 0.2 and (end_lat - start_lat) * (lat - start_lat) > 0:
            lon_candidates.append((name, lat, lon, haversine(end_lat, end_lon, lat, lon)))

    best_lat = min(lat_candidates, key=lambda x: x[3]) if lat_candidates else None
    best_lon = min(lon_candidates, key=lambda x: x[3]) if lon_candidates else None

    if best_lat and best_lon:
        return best_lat if best_lat[3] <= best_lon[3] else best_lon
    return best_lat or best_lon

def find_second_beach_waypoint_simple(first_wp):
    """
    두 번째 경유지는 '경유1에서 가장 가까운 해수욕장'으로 단순 선택.
    (출발/도착 방향 필터 없이, 동일 좌표는 제외)
    """
    f_name, f_lat, f_lon = first_wp
    best = None
    best_d = 10**9
    for name, (lon, lat) in beach_coords.items():
        if name == f_name:
            continue
        if not is_in_coastal_bounds(lat, lon):
            continue
        d = haversine(f_lat, f_lon, lat, lon)
        if d < best_d:
            best_d = d
            best = (name, lat, lon)
    return best

def get_ors_route_ordered(start, waypoints, end):
    """
    ORS를 출발 → 경유1 → 경유2 → 도착 '순서 그대로' 호출.
    """
    if not ORS_API_KEY:
        return {"error": "ORS_API_KEY 미설정"}, 500
    url = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
    headers = {"Authorization": ORS_API_KEY, "Content-Type": "application/json"}

    coords = [[start[1], start[0]]] + [[wp[2], wp[1]] for wp in waypoints] + [[end[1], end[0]]]
    body = {"coordinates": coords}
    res = requests.post(url, headers=headers, json=body)
    try:
        return res.json(), res.status_code
    except Exception as e:
        return {"error": str(e)}, 500

def search_tour_spots_along_route(geojson):
    coords = geojson['features'][0]['geometry']['coordinates']
    spots, seen_ids = [], set()
    for lon, lat in coords[::10]:
        try:
            url = "http://apis.data.go.kr/B551011/KorService1/locationBasedList1"
            params = {
                "serviceKey": TOURAPI_KEY,
                "mapX": lon,
                "mapY": lat,
                "radius": 5000,
                "listYN": "Y",
                "arrange": "E",
                "numOfRows": 10,
                "pageNo": 1,
                "MobileOS": "ETC",
                "MobileApp": "SeaRoute",
                "_type": "json"
            }
            res = requests.get(url, params=params)
            items = res.json().get("response", {}).get("body", {}).get("items", {}).get("item", [])
            if not isinstance(items, list):
                items = [items] if items else []
            for item in items:
                cid = item.get("contentid")
                if cid and cid not in seen_ids:
                    seen_ids.add(cid)
                    spots.append({
                        "title": item.get("title"),
                        "addr1": item.get("addr1"),
                        "mapx": item.get("mapx"),
                        "mapy": item.get("mapy"),
                        "firstimage": item.get("firstimage"),
                        "homepage": item.get("homepage", "")
                    })
        except:
            continue
    return spots

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

        # 경유1: '전과 같이' 방식
        wp1 = find_best_beach_waypoint(start, end)
        if not wp1:
            return jsonify({"error": "❌ 경유지1 탐색 실패"}), 500

        # 경유2: 경유1과 가장 가까운 해수욕장
        wp2 = find_second_beach_waypoint_simple(wp1)
        waypoints = [wp1]
        if wp2:
            waypoints.append(wp2)

        # 반드시 출발→경유1→경유2→도착 순서로 요청
        route_data, status = get_ors_route_ordered(start, waypoints, end)
        if "error" in route_data or status != 200:
            # 혹시 2경유가 막히면 1경유로 축소해 마지막 재시도
            route_data2, status2 = get_ors_route_ordered(start, [wp1], end)
            if "error" in route_data2 or status2 != 200:
                return jsonify({"error": route_data.get("error", "❌ 경로 계산 실패")}), status
            route_data = route_data2
            waypoints = [wp1]

        # 관광지/맛집/카페는 간단 버전(전과 동일 엔드포인트 사용)
        spots = search_tour_spots_along_route(route_data)

        def wp_to_info(w):
            name, la, lo = w
            addr = reverse_geocode_google(la, lo)
            return {"name": name, "lat": la, "lon": lo, "address": addr}

        return jsonify({
            "route": route_data,
            "waypoints": [wp_to_info(w) for w in waypoints],
            "spots": spots
        })
    except Exception as e:
        return jsonify({"error": f"❌ 서버 오류: {str(e)}"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
