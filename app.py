from flask import Flask, request, jsonify, render_template
import os
import requests
from dotenv import load_dotenv
from beaches_coordinates import beach_coords  # {name: (lon, lat)}

load_dotenv()
app = Flask(__name__)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
ORS_API_KEY = os.getenv("ORS_API_KEY", "")
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
    # 한국 해안 대략 범위(본토+제주)
    return (
        (34.0 <= lat <= 38.6 and 126.0 <= lon <= 131.5) or
        (33.0 <= lat <= 33.7 and 126.0 <= lon <= 127.2)
    )

# --- 동선 투영/우회/선정 ---
def _project_t_and_lateral_km(a_lat, a_lon, b_lat, b_lon, p_lat, p_lon):
    vx, vy = (b_lon - a_lon), (b_lat - a_lat)
    wx, wy = (p_lon - a_lon), (p_lat - a_lat)
    denom = vx*vx + vy*vy
    if denom == 0:
        return 0.0, haversine(a_lat, a_lon, p_lat, p_lon)
    t = (wx*vx + wy*vy) / denom
    q_lon = a_lon + t * vx
    q_lat = a_lat + t * vy
    lateral = haversine(p_lat, p_lon, q_lat, q_lon)
    return t, lateral

def _detour_km(prev_lat, prev_lon, cand_lat, cand_lon, end_lat, end_lon):
    return (haversine(prev_lat, prev_lon, cand_lat, cand_lon)
            + haversine(cand_lat, cand_lon, end_lat, end_lon)
            - haversine(prev_lat, prev_lon, end_lat, end_lon))

def _pick_beaches_along_path(start, end, max_n=3, corridor_km=30.0, detour_abs_km=50.0, detour_ratio=0.35):
    s_lat, s_lon = start
    e_lat, e_lon = end

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
    base_candidates.sort(key=lambda x: (x[3], x[4]))  # t 오름차순, 이탈 적을수록 우선

    picked = []
    prev_lat, prev_lon = s_lat, s_lon
    prev_t = 0.0
    for name, lat, lon, t, lateral in base_candidates:
        if t <= prev_t:
            continue
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

# (백업) 옛 1경유 로직: 0개일 때만 사용
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

# -------------------- ORS --------------------
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

# -------------------- TourAPI 상세 --------------------
def tourapi_detail_common(content_id, content_type_id=None):
    url = "http://apis.data.go.kr/B551011/KorService1/detailCommon1"
    params = {
        "MobileOS": "ETC", "MobileApp": "SeaRoute", "_type": "json",
        "serviceKey": TOURAPI_KEY,
        "contentId": content_id,
        "defaultYN": "Y", "addrinfoYN": "Y", "overviewYN": "Y",
        "firstImageYN": "Y", "mapinfoYN": "Y"
    }
    if content_type_id:
        params["contentTypeId"] = content_type_id
    try:
        r = requests.get(url, params=params, timeout=10)
        items = r.json()["response"]["body"]["items"]["item"]
        return items[0] if isinstance(items, list) else items
    except Exception:
        return {}

def tourapi_detail_intro(content_id, content_type_id):
    url = "http://apis.data.go.kr/B551011/KorService1/detailIntro1"
    params = {
        "MobileOS": "ETC", "MobileApp": "SeaRoute", "_type": "json",
        "serviceKey": TOURAPI_KEY,
        "contentId": content_id, "contentTypeId": content_type_id
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        items = r.json()["response"]["body"]["items"]["item"]
        return items[0] if isinstance(items, list) else items
    except Exception:
        return {}

def _pick_first(d, keys):
    for k in keys:
        v = d.get(k)
        if v: return v
    return None

def _classify(ctype):
    # 39=음식점, 12=관광지, 14=문화시설, 25=여행코스, 28=레포츠, 32=숙박, 38=쇼핑
    if ctype == "39":
        return "food"
    if ctype in ("12", "14", "25", "28"):
        return "attraction"
    if ctype == "32":
        return "lodging"
    if ctype == "38":
        return "shopping"
    return "other"

def _bool_from_parking_str(s):
    if not s: return None
    s2 = str(s)
    if any(tok in s2 for tok in ["없", "불가", "X", "x", "미제공"]):
        return False
    if any(tok in s2 for tok in ["가능", "있", "O", "o", "주차", "무료", "유료", "대수", "면"]):
        return True
    return None

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
                "numOfRows": 20, "pageNo": 1,
                "MobileOS": "ETC", "MobileApp": "SeaRoute", "_type": "json",
            }
            r = requests.get(url, params=params, timeout=10)
            items = r.json().get("response", {}).get("body", {}).get("items", {}).get("item", [])
            if not isinstance(items, list):
                items = [items] if items else []

            for item in items:
                cid = str(item.get("contentid", "")).strip()
                if not cid or cid in seen_ids:
                    continue
                seen_ids.add(cid)
                ctype = str(item.get("contenttypeid", "")).strip()

                # 상세 정보
                common = tourapi_detail_common(cid, ctype)
                intro  = tourapi_detail_intro(cid, ctype) if ctype else {}

                title = common.get("title") or item.get("title")
                addr1 = common.get("addr1") or item.get("addr1")
                mapx = float(common.get("mapx") or item.get("mapx") or lon)
                mapy = float(common.get("mapy") or item.get("mapy") or lat)
                image = common.get("firstimage") or common.get("firstimage2") or item.get("firstimage") or item.get("firstimage2")
                homepage = common.get("homepage") or item.get("homepage")
                tel = common.get("tel") or _pick_first(intro, ["infocenter", "infocenterfood", "infocenterculture", "infocenterlodging", "infocenterleports", "infocentershopping"])

                # 타입별 필드 매핑
                hours = _pick_first(intro, ["opentime", "usetime", "opentimefood", "usetimefood"])
                rest  = _pick_first(intro, ["restdate", "restdatefood"])
                parking_str = _pick_first(intro, ["parking", "parkingfood", "parkingculture", "parkinglodging", "parkingleports", "parkingshopping"]) or common.get("parking")
                has_parking = _bool_from_parking_str(parking_str)

                # 편의(여행자 유용)
                pet = _pick_first(intro, ["chkpet", "chkpetfood", "chkpetculture", "chkpetlodging", "chkpetleports", "chkpetshopping"])
                stroller = _pick_first(intro, ["chkbabycarriage", "chkbabycarriagefood", "chkbabycarriageculture", "chkbabycarriagelodging", "chkbabycarriageleports", "chkbabycarriageshopping"])

                category = _classify(ctype)

                spots.append({
                    "contentid": cid,
                    "contenttypeid": ctype,
                    "category": category,             # attraction/food/...
                    "title": title,
                    "addr1": addr1,
                    "mapx": mapx,
                    "mapy": mapy,
                    "firstimage": image,
                    "homepage": homepage,
                    "phone": tel,
                    "hours": hours,
                    "rest": rest,
                    "parking": parking_str,
                    "has_parking": has_parking,
                    "pet": pet,
                    "stroller": stroller
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

        # 동선 일치 경유 해수욕장 1~3개
        waypoints = _pick_beaches_along_path(start, end, max_n=3, corridor_km=30.0, detour_abs_km=50.0, detour_ratio=0.35)
        if not waypoints:
            waypoints = find_best_beach_waypoint(start, end)  # 0 또는 1개

        # ORS 라우팅 (개수 줄여가며 성공할 때까지 시도)
        route_data, status = None, 500
        for k in range(len(waypoints), -1, -1):
            wps = waypoints[:k]
            route_data, status = _ors_route_with_waypoints(start, wps, end)
            if status == 200 and "features" in route_data:
                waypoints = wps
                break
        if status != 200:
            return jsonify({"error": route_data.get("error", "❌ 경로 계산 실패")}), status

        # POI 수집(상세 포함)
        spots = search_tour_spots_along_route(route_data)

        # 경유지 주소 포함
        def to_obj(w):
            name, lat, lon = w
            return {"name": name, "lat": lat, "lon": lon, "address": reverse_geocode_google(lat, lon)}
        wp_objs = [to_obj(w) for w in waypoints]

        payload = {
            "route": route_data,
            "waypoints": wp_objs,
            "spots": spots or []
        }
        # (옵션) 호환키
        if len(wp_objs) >= 1: payload["waypoint"]  = wp_objs[0]
        if len(wp_objs) >= 2: payload["waypoint2"] = wp_objs[1]
        if len(wp_objs) >= 3: payload["waypoint3"] = wp_objs[2]

        return jsonify(payload)
    except Exception as e:
        return jsonify({"error": f"❌ 서버 오류: {str(e)}"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
