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

# -------------------- 유틸 --------------------
def haversine(lat1, lon1, lat2, lon2):
    from math import radians, cos, sin, asin, sqrt
    R = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1))*cos(radians(lat2))*sin(dlon/2)**2
    return 2 * R * asin(sqrt(a))

def geocode_google(address):
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    res = requests.get(url, params={"address": address, "key": GOOGLE_API_KEY, "language": "ko", "region": "kr"}, timeout=10)
    try:
        loc = res.json()["results"][0]["geometry"]["location"]
        return float(loc["lat"]), float(loc["lng"])
    except Exception:
        return None

def reverse_geocode_google(lat, lon):
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    res = requests.get(url, params={"latlng": f"{lat},{lon}", "key": GOOGLE_API_KEY, "language": "ko"}, timeout=10)
    try:
        return res.json()["results"][0]["formatted_address"]
    except Exception:
        return "주소 불러오기 실패"

def is_in_coastal_bounds(lat, lon):
    # 한국 해안 대략 범위(기존 유지 + 제주)
    return (
        (34.0 <= lat <= 38.6 and 126.0 <= lon <= 131.5) or
        (33.0 <= lat <= 33.7 and 126.0 <= lon <= 127.2)
    )

def _project_t_and_lateral_km(a_lat, a_lon, b_lat, b_lon, p_lat, p_lon):
    """
    A(출발)→B(도착) 선분에 P(후보)를 수직투영.
    반환: t(0~1 사이면 선분 위), lateral_km(측면 이탈 거리)
    """
    # 경도/위도를 도 단위 평면으로 근사 투영하여 t 계산 (짧은 거리 가정)
    vx, vy = (b_lon - a_lon), (b_lat - a_lat)
    wx, wy = (p_lon - a_lon), (p_lat - a_lat)
    denom = vx*vx + vy*vy
    if denom == 0:  # 출발=도착
        return 0.0, haversine(a_lat, a_lon, p_lat, p_lon)
    t = (wx*vx + wy*vy) / denom
    # 투영점
    q_lon = a_lon + t * vx
    q_lat = a_lat + t * vy
    lateral = haversine(p_lat, p_lon, q_lat, q_lon)
    return t, lateral

def _detour_km(prev_lat, prev_lon, cand_lat, cand_lon, end_lat, end_lon):
    """추가 우회 거리(km) = prev→cand + cand→end - prev→end"""
    return (haversine(prev_lat, prev_lon, cand_lat, cand_lon)
            + haversine(cand_lat, cand_lon, end_lat, end_lon)
            - haversine(prev_lat, prev_lon, end_lat, end_lon))

def _pick_beaches_along_path(start, end, max_n=3, corridor_km=30.0, detour_abs_km=50.0, detour_ratio=0.35):
    """
    출발→도착 '동선'과 같은 방향으로, 선분 코리도(corridor_km) 안의 해수욕장을
    진행 순서대로 최대 max_n개 고른다.
    - 각 단계는 직전 포인트(prev)→도착 기준으로 다시 필터링/선정 (단조 증가)
    """
    s_lat, s_lon = start
    e_lat, e_lon = end

    # 1차 후보: 전체 중 '출발→도착' 선분 기준으로 0<t<1 이고, 측면 이탈 <= corridor_km
    base_candidates = []
    for name, (lon, lat) in beach_coords.items():
        try:
            lon, lat = float(lon), float(lat)
        except Exception:
            continue
        if not is_in_coastal_bounds(lat, lon):
            continue
        t, lateral = _project_t_and_lateral_km(s_lat, s_lon, e_lat, e_lon, lat, lon)
        if 0.0 < t < 1.0 and lateral <= corridor_km:
            base_candidates.append((name, lat, lon, t, lateral))

    # t 오름차순, 같은 t면 라인에 더 가까운 순
    base_candidates.sort(key=lambda x: (x[3], x[4]))

    picked = []
    prev_lat, prev_lon = s_lat, s_lon
    prev_t = 0.0

    for name, lat, lon, t, lateral in base_candidates:
        # 단조 증가 보장: 이전 t보다 커야 함
        if t <= prev_t:
            continue
        # prev→end 기준으로 다시 detour 검사
        detour = _detour_km(prev_lat, prev_lon, lat, lon, e_lat, e_lon)
        direct_left = haversine(prev_lat, prev_lon, e_lat, e_lon)
        if detour > detour_abs_km or detour > direct_left * detour_ratio:
            continue
        picked.append((name, lat, lon))
        prev_lat, prev_lon = lat, lon
        prev_t = t
        if len(picked) >= max_n:
            break

    return picked

# (백업) 과거 1경유 로직: 필요 시 0개일 때만 사용
def find_best_beach_waypoint(start, end):
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
    pick = best_lat if best_lat and (not best_lon or best_lat[3] < best_lon[3]) else best_lon
    if pick:
        return [(pick[0], float(pick[1]), float(pick[2]))]
    return []

# -------------------- ORS/POI --------------------
def _ors_route_with_waypoints(start, waypoints, end):
    if not ORS_API_KEY:
        return {"error": "ORS_API_KEY 미설정"}, 500
    url = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
    headers = {"Authorization": ORS_API_KEY, "Content-Type": "application/json"}
    coords = [[start[1], start[0]]] + [[wp[2], wp[1]] for wp in waypoints] + [[end[1], end[0]]]
    res = requests.post(url, headers=headers, json={"coordinates": coords}, timeout=20)
    try:
        return res.json(), res.status_code
    except Exception as e:
        return {"error": str(e)}, 500

def search_tour_spots_along_route(geojson):
    coords = geojson["features"][0]["geometry"]["coordinates"]
    spots, seen_ids = [], set()
    for pt in coords[::10]:
        if not isinstance(pt, (list, tuple)) or len(pt) < 2:
            continue
        lon, lat = float(pt[0]), float(pt[1])
        try:
            url = "http://apis.data.go.kr/B551011/KorService1/locationBasedList1"
            params = {
                "serviceKey": TOURAPI_KEY,
                "mapX": lon, "mapY": lat, "radius": 5000,
                "listYN": "Y", "arrange": "E",
                "numOfRows": 10, "pageNo": 1,
                "MobileOS": "ETC", "MobileApp": "SeaRoute", "_type": "json",
            }
            res = requests.get(url, params=params, timeout=10)
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
        except Exception:
            continue
    return spots

# -------------------- Flask --------------------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/route", methods=["POST"])
def route():
    try:
        data = request.get_json(force=True)
        start = geocode_google(data.get("start"))
        end   = geocode_google(data.get("end"))
        if not start or not end:
            return jsonify({"error": "❌ 주소 변환 실패"}), 400

        # 동선과 같은 방향으로 1~3개 선택
        waypoints = _pick_beaches_along_path(start, end, max_n=3, corridor_km=30.0, detour_abs_km=50.0, detour_ratio=0.35)

        # 아무 것도 못 고르면 과거 1경유 백업 로직 사용
        if not waypoints:
            waypoints = find_best_beach_waypoint(start, end)  # 0 또는 1개 리스트

        # ORS 라우팅 (전부 넣어서 시도 → 실패 시 개수 줄여 재시도)
        tried = False
        route_data, status = None, 500
        for k in range(len(waypoints), -1, -1):  # 3→2→1→0
            tried = True
            wps = waypoints[:k]
            route_data, status = _ors_route_with_waypoints(start, wps, end)
            if status == 200 and "features" in route_data:
                waypoints = wps
                break
        if not tried or status != 200:
            return jsonify({"error": route_data.get("error", "❌ 경로 계산 실패")}), status

        # POI
        spots = search_tour_spots_along_route(route_data)

        # 경유지 주소 포함 가공
        def to_obj(w):
            name, lat, lon = w
            return {"name": name, "lat": lat, "lon": lon, "address": reverse_geocode_google(lat, lon)}

        wp_objs = [to_obj(w) for w in waypoints]

        # 호환 키 구성
        payload = {
            "route": route_data,
            "waypoints": wp_objs,
            "spots": spots or []
        }
        if len(wp_objs) >= 1: payload["waypoint"]  = wp_objs[0]
        if len(wp_objs) >= 2: payload["waypoint2"] = wp_objs[1]
        if len(wp_objs) >= 3: payload["waypoint3"] = wp_objs[2]

        return jsonify(payload)
    except Exception as e:
        return jsonify({"error": f"❌ 서버 오류: {str(e)}"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
