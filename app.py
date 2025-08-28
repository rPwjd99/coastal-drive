from flask import Flask, request, jsonify, render_template
import os
import requests
from dotenv import load_dotenv
from beaches_coordinates import beach_coords

load_dotenv()
app = Flask(__name__)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
ORS_API_KEY = os.getenv("ORS_API_KEY")
TOURAPI_KEY = "e1tU33wjMx2nynKjH8yDBm/S4YNne6B8mpCOWtzMH9TSONF71XG/xAwPqyv1fANpgeOvbPY+Le+gM6cYCnWV8w=="

# -------------------- 유틸 --------------------
def haversine(lat1, lon1, lat2, lon2):
    from math import radians, cos, sin, asin, sqrt
    R = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2)**2
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
    # (이전 코드 유지) 한국 해안권 대략 박스
    return (
        (35 <= lat <= 38 and 128 <= lon <= 131) or
        (33 <= lat <= 35 and 126 <= lon <= 129) or
        (34 <= lat <= 38 and 124 <= lon <= 126)
    )

# -------------------- 경유지 선택 --------------------
def find_best_beach_waypoint(start, end):
    """
    네가 쓰던 1번 경유지 찾기 로직 그대로.
    반환 형식: (name, lat, lon, dist_to_end)  # dist는 내부 사용용
    """
    start_lat, start_lon = start
    end_lat, end_lon = end
    lat_candidates, lon_candidates = [], []
    for name, (lon, lat) in beach_coords.items():
        if not is_in_coastal_bounds(lat, lon): 
            continue
        if abs(lat - start_lat) < 0.2 and (end_lon - start_lon) * (lon - start_lon) > 0:
            lat_candidates.append((name, lat, lon, haversine(end_lat, end_lon, lat, lon)))
        if abs(lon - start_lon) < 0.2 and (end_lat - start_lat) * (lat - start_lat) > 0:
            lon_candidates.append((name, lat, lon, haversine(end_lat, end_lon, lat, lon)))
    best_lat = min(lat_candidates, key=lambda x: x[3]) if lat_candidates else None
    best_lon = min(lon_candidates, key=lambda x: x[3]) if lon_candidates else None
    return best_lat if best_lat and (not best_lon or best_lat[3] < best_lon[3]) else best_lon

def _ahead_of(first_lat, first_lon, cand_lat, cand_lon, end_lat, end_lon):
    """
    '도착지 방향(전진 방향)에 있다'를 간단히 판정.
    벡터(F→E)와 벡터(F→C)의 내적이 양수면 '앞'으로 간주.
    """
    dx_goal = end_lon - first_lon
    dy_goal = end_lat - first_lat
    dx_cand = cand_lon - first_lon
    dy_cand = cand_lat - first_lat
    return (dx_goal * dx_cand + dy_goal * dy_cand) > 0

def find_second_beach_waypoint(first_wp, end):
    """
    2번 경유지 = '경유1에서 가장 가까운 해수욕장' (우선),
    가능하면 '도착지 방향(전진)'에 있는 후보만 대상으로 함.
    반환 형식: (name, lat, lon)
    """
    f_name, f_lat, f_lon = first_wp[0], first_wp[1], first_wp[2]
    end_lat, end_lon = end

    ahead_candidates = []
    any_candidates = []
    for name, (lon, lat) in beach_coords.items():
        if name == f_name: 
            continue
        if not is_in_coastal_bounds(lat, lon):
            continue
        d = haversine(f_lat, f_lon, lat, lon)
        any_candidates.append((name, lat, lon, d))
        if _ahead_of(f_lat, f_lon, lat, lon, end_lat, end_lon):
            ahead_candidates.append((name, lat, lon, d))

    picks = ahead_candidates if ahead_candidates else any_candidates
    if not picks:
        return None
    name, lat, lon, _ = min(picks, key=lambda x: x[3])
    return (name, lat, lon)

# -------------------- 라우팅/POI --------------------
def get_ors_route_with_waypoints(start, waypoint1, waypoint2, end):
    """
    ORS 호출을 '출발 → 경유1 → (경유2) → 도착' 순서로 구성.
    waypoint2가 None이면 1경유만 포함.
    """
    url = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
    headers = {"Authorization": ORS_API_KEY, "Content-Type": "application/json"}

    coords = [
        [start[1], start[0]],
        [waypoint1[2], waypoint1[1]],
    ]
    if waypoint2:
        coords.append([waypoint2[2], waypoint2[1]])
    coords.append([end[1], end[0]])

    res = requests.post(url, headers=headers, json={"coordinates": coords})
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

# -------------------- Flask --------------------
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

        # 1) 경유1: 기존 로직 그대로
        waypoint1 = find_best_beach_waypoint(start, end)
        if not waypoint1:
            return jsonify({"error": "❌ 경유지1 탐색 실패"}), 500

        # 2) 경유2: 경유1에서 '다음' 해수욕장 (가능하면 목적지 방향)
        waypoint2 = find_second_beach_waypoint(waypoint1, end)

        # 3) 경로 요청: 2경유 우선, 실패 시 1경유로 폴백
        route_data, status = get_ors_route_with_waypoints(start, waypoint1, waypoint2, end)
        if "error" in route_data or status != 200:
            route_data2, status2 = get_ors_route_with_waypoints(start, waypoint1, None, end)
            if "error" in route_data2 or status2 != 200:
                return jsonify({"error": route_data.get("error", "❌ 경로 계산 실패")}), status
            route_data = route_data2
            waypoint2 = None

        # 4) 경로 주변 POI
        spots = search_tour_spots_along_route(route_data)

        # 5) 경유지 주소 역지오코딩
        wp1_addr = reverse_geocode_google(waypoint1[1], waypoint1[2])
        wp1_obj = {"name": waypoint1[0], "lat": waypoint1[1], "lon": waypoint1[2], "address": wp1_addr}

        wp2_obj = None
        if waypoint2:
            wp2_addr = reverse_geocode_google(waypoint2[1], waypoint2[2])
            wp2_obj = {"name": waypoint2[0], "lat": waypoint2[1], "lon": waypoint2[2], "address": wp2_addr}

        # ✅ 기존 키 유지 + 확장
        return jsonify({
            "route": route_data,
            "waypoint": wp1_obj,      # 기존 프론트 호환
            "waypoint2": wp2_obj,     # 새로 추가
            "spots": spots or []
        })
    except Exception as e:
        return jsonify({"error": f"❌ 서버 오류: {str(e)}"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
