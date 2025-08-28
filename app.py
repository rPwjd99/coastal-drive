from flask import Flask, request, jsonify, render_template
import os
import requests
from dotenv import load_dotenv
from beaches_coordinates import beach_coords
from functools import lru_cache
from math import radians, cos, sin, asin, sqrt, atan2, degrees

load_dotenv()
app = Flask(__name__)

# --- API Keys (환경변수 우선, 없으면 기본값 사용 가능) ---
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
ORS_API_KEY = os.getenv("ORS_API_KEY", "")
TOURAPI_KEY = os.getenv("TOURAPI_KEY", "e1tU33wjMx2nynKjH8yDBm/S4YNne6B8mpCOWtzMH9TSONF71XG/xAwPqyv1fANpgeOvbPY+Le+gM6cYCnWV8w==")

# --- 제주 경계(대략) ---
JEJU_MIN_LON, JEJU_MAX_LON = 126.0, 127.1
JEJU_MIN_LAT, JEJU_MAX_LAT = 33.05, 33.60

# --- 도/특·광역시 축약어 보정 맵 ---
PROV_MAP = {
    "경북": "경상북도",
    "경남": "경상남도",
    "전북": "전라북도",
    "전남": "전라남도",
    "충북": "충청북도",
    "충남": "충청남도",
    "강원": "강원도",
    "경기": "경기도",
    "서울": "서울특별시",
    "부산": "부산광역시",
    "대구": "대구광역시",
    "인천": "인천광역시",
    "광주": "광주광역시",
    "대전": "대전광역시",
    "울산": "울산광역시",
    "세종": "세종특별자치시",
    "제주": "제주특별자치도",
}

def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2)**2
    return 2 * R * asin(sqrt(a))

def bearing(lon1, lat1, lon2, lat2):
    y = sin(radians(lon2 - lon1)) * cos(radians(lat2))
    x = cos(radians(lat1)) * sin(radians(lat2)) - sin(radians(lat1)) * cos(radians(lat2)) * cos(radians(lon2 - lon1))
    br = degrees(atan2(y, x))
    return (br + 360) % 360

def angle_diff(a, b):
    d = abs(a - b) % 360
    return d if d <= 180 else 360 - d

def in_jeju(lat, lon):
    return (JEJU_MIN_LON <= lon <= JEJU_MAX_LON) and (JEJU_MIN_LAT <= lat <= JEJU_MAX_LAT)

def is_in_coastal_bounds(lat, lon):
    # 기존 네 범위 그대로 유지
    return (
        (35 <= lat <= 38 and 128 <= lon <= 131) or
        (33 <= lat <= 35 and 126 <= lon <= 129) or
        (34 <= lat <= 38 and 124 <= lon <= 126)
    )

def normalize_address_tries(raw):
    q = (raw or "").strip()
    if not q:
        return []

    tries = [q]

    # 제주 특화 우선 시도
    jeju_prefix = [f"제주특별자치도 {q}", f"제주시 {q}", f"서귀포시 {q}"]

    # 축약어 → 정식명칭
    expanded = []
    for abbr, full in PROV_MAP.items():
        if q.startswith(abbr):
            expanded.append(q.replace(abbr, full, 1))

    # 일반 접두(전국 시도 붙여 시도)
    generic_prefixes = [
        "서울특별시", "부산광역시", "대구광역시", "인천광역시", "광주광역시", "대전광역시", "울산광역시",
        "세종특별자치시", "경기도", "강원도", "충청북도", "충청남도", "전라북도", "전라남도",
        "경상북도", "경상남도", "제주특별자치도"
    ]
    generic_tries = [f"{p} {q}" for p in generic_prefixes]

    # 중복 제거하며 병합
    out, seen = [], set()
    for arr in [tries, jeju_prefix, expanded, generic_tries]:
        for t in arr:
            t = t.strip()
            if t and t not in seen:
                seen.add(t)
                out.append(t)
    return out

@lru_cache(maxsize=512)
def geocode_google_once(query):
    if not GOOGLE_API_KEY:
        return None
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    res = requests.get(url, params={"address": query, "key": GOOGLE_API_KEY, "language": "ko", "region": "kr"}, timeout=8)
    if res.status_code != 200:
        return None
    data = res.json()
    if data.get("status") != "OK":
        return None
    loc = data["results"][0]["geometry"]["location"]
    return (loc["lat"], loc["lng"])

def geocode_google(address):
    for q in normalize_address_tries(address):
        c = geocode_google_once(q)
        if c:
            return c
    return None

def reverse_geocode_google(lat, lon):
    if not GOOGLE_API_KEY:
        return "주소 불러오기 실패"
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    res = requests.get(url, params={"latlng": f"{lat},{lon}", "key": GOOGLE_API_KEY, "language": "ko"}, timeout=8)
    try:
        return res.json()["results"][0]["formatted_address"]
    except:
        return "주소 불러오기 실패"

def find_best_beach_waypoint(start, end, candidates):
    start_lat, start_lon = start
    end_lat, end_lon = end
    br_to_goal = bearing(start_lon, start_lat, end_lon, end_lat)

    best = None
    best_score = 10**12

    for name, (lon, lat) in candidates.items():
        if not is_in_coastal_bounds(lat, lon):
            continue
        # 방향성 + 우회비용 최소
        ang = angle_diff(br_to_goal, bearing(start_lon, start_lat, lon, lat))
        detour = haversine(start_lat, start_lon, lat, lon) + haversine(lat, lon, end_lat, end_lon) - haversine(start_lat, start_lon, end_lat, end_lon)
        score = ang * 2000 + detour
        if score < best_score:
            best_score = score
            best = (name, lat, lon)
    return best

def find_second_beach_waypoint(start, end, first_wp, candidates):
    start_lat, start_lon = start
    end_lat, end_lon = end
    _, f_lat, f_lon = first_wp
    br_to_goal_from_first = bearing(f_lon, f_lat, end_lon, end_lat)
    dist_start_first = haversine(start_lat, start_lon, f_lat, f_lon)

    best = None
    best_score = 10**12
    for name, (lon, lat) in candidates.items():
        if (lat, lon) == (f_lat, f_lon):
            continue
        if not is_in_coastal_bounds(lat, lon):
            continue
        # 첫 경유지 이후에 있는 후보만
        if haversine(start_lat, start_lon, lat, lon) <= dist_start_first:
            continue
        ang = angle_diff(br_to_goal_from_first, bearing(f_lon, f_lat, lon, lat))
        detour = haversine(f_lat, f_lon, lat, lon) + haversine(lat, lon, end_lat, end_lon) - haversine(f_lat, f_lon, end_lat, end_lon)
        score = ang * 2000 + detour
        if score < best_score:
            best_score = score
            best = (name, lat, lon)

    # 백업: 없으면 첫 경유지→도착 기준으로 다시 최적 하나 고르고, 그래도 같으면 첫 경유지와 가장 가까운 다른 해변
    if not best:
        temp = find_best_beach_waypoint((f_lat, f_lon), (end_lat, end_lon), candidates)
        if temp and (temp[1], temp[2]) != (f_lat, f_lon):
            best = temp
        else:
            others = sorted(
                [(n, la, lo) for n, (lo, la) in candidates.items() if (la, lo) != (f_lat, f_lon)],
                key=lambda x: haversine(f_lat, f_lon, x[1], x[2])
            )
            if others:
                n, la, lo = others[0]
                best = (n, la, lo)
    return best

def get_ors_route(start, waypoints, end):
    if not ORS_API_KEY:
        return {"error": "ORS_API_KEY not set"}, 500
    url = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
    headers = {"Authorization": ORS_API_KEY, "Content-Type": "application/json"}
    coords = [[start[1], start[0]]] + [[wp[2], wp[1]] for wp in waypoints] + [[end[1], end[0]]]
    body = {"coordinates": coords}
    res = requests.post(url, headers=headers, json=body, timeout=15)
    try:
        return res.json(), res.status_code
    except Exception as e:
        return {"error": str(e)}, 500

def tourapi_detail_common(content_id, content_type_id=None):
    base = "http://apis.data.go.kr/B551011/KorService1/detailCommon1"
    params = {
        "MobileOS": "ETC", "MobileApp": "SeaRoute", "_type": "json",
        "serviceKey": TOURAPI_KEY,
        "contentId": content_id,
        "defaultYN": "Y", "addrinfoYN": "Y", "overviewYN": "Y",
        "firstImageYN": "Y", "mapinfoYN": "Y"
    }
    if content_type_id:
        params["contentTypeId"] = content_type_id
    r = requests.get(base, params=params, timeout=8)
    try:
        items = r.json()["response"]["body"]["items"]["item"]
        return items[0] if isinstance(items, list) else items
    except:
        return {}

def tourapi_detail_intro(content_id, content_type_id):
    base = "http://apis.data.go.kr/B551011/KorService1/detailIntro1"
    params = {
        "MobileOS": "ETC", "MobileApp": "SeaRoute", "_type": "json",
        "serviceKey": TOURAPI_KEY,
        "contentId": content_id, "contentTypeId": content_type_id
    }
    r = requests.get(base, params=params, timeout=8)
    try:
        items = r.json()["response"]["body"]["items"]["item"]
        return items[0] if isinstance(items, list) else items
    except:
        return {}

def search_tour_spots_along_route(geojson):
    coords = geojson['features'][0]['geometry']['coordinates']
    spots, seen_ids = [], set()
    # 경로를 적당히 샘플링하여 API 과다호출 방지
    for lon, lat in coords[::10]:
        try:
            url = "http://apis.data.go.kr/B551011/KorService1/locationBasedList1"
            params = {
                "serviceKey": TOURAPI_KEY,
                "mapX": lon, "mapY": lat,
                "radius": 5000,
                "listYN": "Y",
                "arrange": "E",
                "numOfRows": 20,
                "pageNo": 1,
                "MobileOS": "ETC",
                "MobileApp": "SeaRoute",
                "_type": "json"
            }
            res = requests.get(url, params=params, timeout=8)
            items = res.json().get("response", {}).get("body", {}).get("items", {}).get("item", [])
            if not isinstance(items, list):
                items = [items] if items else []
            for item in items:
                cid = item.get("contentid")
                if not cid or cid in seen_ids:
                    continue
                seen_ids.add(cid)
                ctype = item.get("contenttypeid")
                common = tourapi_detail_common(cid, ctype)
                intro = tourapi_detail_intro(cid, int(ctype)) if ctype else {}

                photo = common.get("firstimage") or common.get("firstimage2") or item.get("firstimage") or item.get("firstimage2")
                hours = intro.get("usetime") or intro.get("opentime") or intro.get("usetimefood") or intro.get("opentimefood")
                parking = intro.get("parking") or intro.get("parkingfood") or common.get("parking")

                spots.append({
                    "title": item.get("title") or common.get("title"),
                    "addr1": common.get("addr1") or item.get("addr1"),
                    "mapx": float(common.get("mapx") or item.get("mapx") or lon),
                    "mapy": float(common.get("mapy") or item.get("mapy") or lat),
                    "firstimage": photo,
                    "homepage": item.get("homepage", ""),
                    "hours": hours,
                    "parking": parking,
                    "contenttypeid": ctype
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
        data = request.get_json(force=True)
        start_raw = data.get("start")
        end_raw = data.get("end")
        jeju_only = data.get("jeju_only", True)  # 기본 True

        start = geocode_google(start_raw)
        end = geocode_google(end_raw)
        if not start or not end:
            return jsonify({"error": "주소 변환 실패"}), 400

        s_lat, s_lon = start
        e_lat, e_lon = end

        s_in = in_jeju(s_lat, s_lon)
        e_in = in_jeju(e_lat, e_lon)
        if jeju_only:
            if s_in and not e_in:
                return jsonify({"error": "제주 섬내 전용: 출발 제주, 도착 본토 불가"}), 400
            if e_in and not s_in:
                return jsonify({"error": "제주 섬내 전용: 도착 제주, 출발 본토 불가"}), 400

        # 제주 모드면 후보를 제주 내 해수욕장만 사용하도록 필터(데이터가 전국이면 여기서 필터)
        candidates = dict(beach_coords)
        if s_in or e_in or jeju_only:
            candidates = {n: (lo, la) for n, (lo, la) in beach_coords.items()
                          if JEJU_MIN_LON <= lo <= JEJU_MAX_LON and JEJU_MIN_LAT <= la <= JEJU_MAX_LAT}

        if not candidates:
            return jsonify({"error": "경유지 후보 없음(제주 후보가 비어있음)"}), 400

        wp1 = find_best_beach_waypoint((s_lat, s_lon), (e_lat, e_lon), candidates)
        if not wp1:
            return jsonify({"error": "경유지1 탐색 실패"}), 500
        wp2 = find_second_beach_waypoint((s_lat, s_lon), (e_lat, e_lon), wp1, candidates)

        # 라우트 시도: 2경유 → 실패 시 1경유 축소
        waypoints = [wp1] + ([wp2] if wp2 else [])
        route_data, status = get_ors_route((s_lat, s_lon), waypoints, (e_lat, e_lon))
        if "error" in route_data or status != 200:
            # 축소 재시도
            route_data2, status2 = get_ors_route((s_lat, s_lon), [wp1], (e_lat, e_lon))
            if "error" in route_data2 or status2 != 200:
                return jsonify({"error": route_data.get("error", "경로 계산 실패")}), status
            route_data = route_data2
            waypoints = [wp1]

        # POI(관광지/맛집/카페) 수집 + 세부정보(사진/운영시간/주차)
        spots = search_tour_spots_along_route(route_data)

        wp_info = []
        for w in waypoints:
            name, la, lo = w
            addr = reverse_geocode_google(la, lo)
            wp_info.append({"name": name, "lat": la, "lon": lo, "address": addr})

        return jsonify({
            "route": route_data,
            "waypoints": wp_info,  # 최대 2개
            "spots": spots or []
        })
    except Exception as e:
        return jsonify({"error": f"서버 오류: {str(e)}"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=True)
