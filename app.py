# app.py
# Flask 서버: 출발→도착 선분에 대한 투영 t 단조 증가, 코리도(측면 이탈) ≤ 30km,
# 우회비용(절대 50km 또는 상대 0.35배) 제한 하에 1~3개 해수욕장 경유 자동 선택.
# ORS로 실제 도로 경로 계산 후, TourAPI로 관광/맛집 정보 수집하여 팝업용 데이터 반환.

import os
import math
import requests
from flask import Flask, request, jsonify, render_template
from dotenv import load_dotenv

# 해수욕장 좌표 사전: {이름: (lon, lat)}
from beaches_coordinates import beach_coords

load_dotenv()

# 루트에 index.html을 두는 구조를 지원하기 위해 template_folder="."
app = Flask(__name__, static_folder="static", template_folder=".")

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
ORS_API_KEY = os.getenv("ORS_API_KEY", "")
TOURAPI_KEY = os.getenv("TOURAPI_KEY", "e1tU33wjMx2nynKjH8yDBm/S4YNne6B8mpCOWtzMH9TSONF71XG/xAwPqyv1fANpgeOvbPY+Le+gM6cYCnWV8w==")

# -------------------- 유틸 --------------------

def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))

def geocode_google(address):
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {"address": address, "key": GOOGLE_API_KEY, "language": "ko", "region": "kr"}
    r = requests.get(url, params=params, timeout=15)
    try:
        loc = r.json()["results"][0]["geometry"]["location"]
        return (loc["lat"], loc["lng"])
    except Exception:
        return None

def reverse_geocode_google(lat, lon):
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {"latlng": f"{lat},{lon}", "key": GOOGLE_API_KEY, "language": "ko"}
    r = requests.get(url, params=params, timeout=15)
    try:
        return r.json()["results"][0]["formatted_address"]
    except Exception:
        return ""

def is_in_coastal_bounds(lat, lon):
    # 한반도 주변 대략 범위 필터 (노이즈 컷)
    return (
        (35 <= lat <= 38 and 128 <= lon <= 131) or  # 동해
        (33 <= lat <= 35 and 126 <= lon <= 129) or  # 남해/제주 포함
        (34 <= lat <= 38 and 124 <= lon <= 126)     # 서해
    )

def to_local_xy(lat, lon, lat0, lon0):
    # equirectangular 근사 (경도 축에 cos(lat0) 보정) → km 단위 평면화
    km_per_deg_lat = 111.32
    km_per_deg_lon = 111.32 * math.cos(math.radians(lat0))
    x = (lon - lon0) * km_per_deg_lon
    y = (lat - lat0) * km_per_deg_lat
    return x, y

def projection_on_segment_km(c_lat, c_lon, s_lat, s_lon, e_lat, e_lon):
    # C를 S→E 선분에 정사영: t in R, 또한 측면 이탈 거리(km) 반환
    lat0 = (s_lat + e_lat) / 2.0
    xS, yS = to_local_xy(s_lat, s_lon, lat0, s_lon)
    xE, yE = to_local_xy(e_lat, e_lon, lat0, s_lon)
    xC, yC = to_local_xy(c_lat, c_lon, lat0, s_lon)

    vx, vy = (xE - xS), (yE - yS)
    wx, wy = (xC - xS), (yC - yS)
    seg_len2 = vx * vx + vy * vy
    if seg_len2 == 0:
        return 0.0, math.hypot(wx, wy)
    t = (wx * vx + wy * vy) / seg_len2  # 실수 영역 투영
    # 수직거리 = 벡터 외적 크기 / |SE|
    cross = abs(wx * vy - wy * vx)
    lateral = cross / math.sqrt(seg_len2)
    return t, lateral  # t: 실수, lateral: km

# -------------------- ORS --------------------

def ors_route_geojson(coords_lonlat):
    """
    coords_lonlat: [[lon, lat], [lon, lat], ...]
    returns: (geojson, distance_km, status_code)
    """
    url = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
    headers = {"Authorization": ORS_API_KEY, "Content-Type": "application/json"}
    payload = {"coordinates": coords_lonlat}
    r = requests.post(url, headers=headers, json=payload, timeout=25)
    try:
        gj = r.json()
    except Exception:
        return {"error": "invalid ORS response"}, None, r.status_code
    if r.status_code == 200 and "features" in gj:
        try:
            dist_m = gj["features"][0]["properties"]["summary"]["distance"]
            return gj, dist_m / 1000.0, 200
        except Exception:
            return gj, None, 200
    return gj, None, r.status_code

# -------------------- 후보 해수욕장 선별/경유지 선택 --------------------

def find_best_beach_waypoint_legacy(start, end):
    """
    이전 단일 경유지 선택(사용자 고정 로직). 반환: (name, lat, lon)
    """
    start_lat, start_lon = start
    end_lat, end_lon = end
    lat_candidates, lon_candidates = [], []
    for name, (lon, lat) in beach_coords.items():
        if not is_in_coastal_bounds(lat, lon):
            continue
        # 위도 정렬/경도 정렬 + 전진성
        if abs(lat - start_lat) < 0.2 and (end_lon - start_lon) * (lon - start_lon) > 0:
            d2end = haversine_km(end_lat, end_lon, lat, lon)
            lat_candidates.append((name, lat, lon, d2end))
        if abs(lon - start_lon) < 0.2 and (end_lat - start_lat) * (lat - start_lat) > 0:
            d2end = haversine_km(end_lat, end_lon, lat, lon)
            lon_candidates.append((name, lat, lon, d2end))
    best_lat = min(lat_candidates, key=lambda x: x[3]) if lat_candidates else None
    best_lon = min(lon_candidates, key=lambda x: x[3]) if lon_candidates else None
    picked = best_lat if best_lat and (not best_lon or best_lat[3] < best_lon[3]) else best_lon
    if picked:
        return (picked[0], picked[1], picked[2])
    return None

def select_beach_waypoints(start, end,
                           max_waypoints=3,
                           corridor_km=30.0,
                           detour_abs_km=50.0,
                           detour_rel=0.35,
                           max_candidates_to_test=25):
    """
    규칙:
      1) 방향성: 출발→도착 선분 투영값 t가 (0,1) 범위이고, 최종 선택된 경유지들의 t는 오름차순(단조 증가).
      2) 라인 근접도: 측면 이탈 거리 ≤ corridor_km.
      3) 우회비용 제어: 경유지를 하나 추가할 때마다, 직전 경로 대비
         - 절대 추가거리 > detour_abs_km 또는
         - 상대 추가거리 > detour_rel
         이면 제외.
      4) 1~3개 가변: 조건을 만족하는 경유지가 많아도 최대 3개. 0개면 레거시 1경유로 폴백.

    반환: [(name, lat, lon, t, lateral_km), ...]  (선택된 순서대로)
    """
    s_lat, s_lon = start
    e_lat, e_lon = end

    # 1) 기준 경로(직접) 길이
    base_geojson, base_dist_km, sc = ors_route_geojson([[s_lon, s_lat], [e_lon, e_lat]])
    if sc != 200 or base_dist_km is None:
        raise RuntimeError("ORS 기본 경로 계산 실패")

    # 2) 후보: 코리도 내 + 투영 t in (0,1) + 연안 박스
    cands = []
    for name, (lon, lat) in beach_coords.items():
        if not is_in_coastal_bounds(lat, lon):
            continue
        t, lateral = projection_on_segment_km(lat, lon, s_lat, s_lon, e_lat, e_lon)
        if 0.0 < t < 1.0 and lateral <= corridor_km:
            cands.append((name, lat, lon, t, lateral))

    if not cands:
        # 0개면 레거시 1경유 사용
        legacy = find_best_beach_waypoint_legacy(start, end)
        return [(legacy[0], legacy[1], legacy[2], 0.5, 0.0)] if legacy else []

    # 3) 테스트 후보 축소: 측면 이탈이 작은 순으로 상위 N개, 그 뒤 t 오름차순
    cands.sort(key=lambda x: (x[4], x[3]))  # lateral → t
    cands = cands[:max_candidates_to_test]
    cands.sort(key=lambda x: x[3])  # t 기준 정렬

    selected = []
    prev_dist_km = base_dist_km
    waypoint_chain = []  # [[lon, lat], ...]

    for name, lat, lon, t, lateral in cands:
        trial_coords = [[s_lon, s_lat]] + waypoint_chain + [[lon, lat], [e_lon, e_lat]]
        gj, d_km, sc = ors_route_geojson(trial_coords)
        if sc != 200 or d_km is None:
            continue
        added = d_km - prev_dist_km
        if added > detour_abs_km or (added / prev_dist_km) > detour_rel:
            # 우회비용 초과 → 제외
            continue
        # 채택
        selected.append((name, lat, lon, t, lateral))
        waypoint_chain = waypoint_chain[:-1] + [[lon, lat]] + [[e_lon, e_lat]] if waypoint_chain else [[lon, lat], [e_lon, e_lat]]
        prev_dist_km = d_km
        if len(selected) >= max_waypoints:
            break

    if not selected:
        # 조건 불충족 시에도 완전 무경유는 피함 → 레거시 1경유
        legacy = find_best_beach_waypoint_legacy(start, end)
        return [(legacy[0], legacy[1], legacy[2], 0.5, 0.0)] if legacy else []

    return selected

# -------------------- TourAPI --------------------

SIGHT_CTIDS = {12, 14, 15, 25, 28, 32, 38}  # 관광지/문화/행사/레포츠/숙박/쇼핑 등
FOOD_CTID = 39

def tourapi_location_based(lon, lat, radius=5000, rows=20):
    url = "http://apis.data.go.kr/B551011/KorService1/locationBasedList1"
    params = {
        "serviceKey": TOURAPI_KEY,
        "mapX": lon,
        "mapY": lat,
        "radius": radius,
        "listYN": "Y",
        "arrange": "E",
        "numOfRows": rows,
        "pageNo": 1,
        "MobileOS": "ETC",
        "MobileApp": "SeaRoute",
        "_type": "json",
    }
    r = requests.get(url, params=params, timeout=15)
    try:
        items = r.json().get("response", {}).get("body", {}).get("items", {}).get("item", [])
        if isinstance(items, dict):
            items = [items]
        return items or []
    except Exception:
        return []

def tourapi_detail_common(content_id):
    url = "http://apis.data.go.kr/B551011/KorService1/detailCommon1"
    params = {
        "serviceKey": TOURAPI_KEY,
        "contentId": content_id,
        "defaultYN": "Y",
        "firstImageYN": "Y",
        "addrinfoYN": "Y",
        "mapinfoYN": "Y",
        "overviewYN": "Y",
        "MobileOS": "ETC",
        "MobileApp": "SeaRoute",
        "_type": "json",
    }
    r = requests.get(url, params=params, timeout=15)
    try:
        item = r.json().get("response", {}).get("body", {}).get("items", {}).get("item", [])
        return item[0] if isinstance(item, list) and item else (item if isinstance(item, dict) else {})
    except Exception:
        return {}

def tourapi_detail_intro(content_id, content_type_id):
    url = "http://apis.data.go.kr/B551011/KorService1/detailIntro1"
    params = {
        "serviceKey": TOURAPI_KEY,
        "contentId": content_id,
        "contentTypeId": content_type_id,
        "MobileOS": "ETC",
        "MobileApp": "SeaRoute",
        "_type": "json",
    }
    r = requests.get(url, params=params, timeout=15)
    try:
        item = r.json().get("response", {}).get("body", {}).get("items", {}).get("item", [])
        return item[0] if isinstance(item, list) and item else (item if isinstance(item, dict) else {})
    except Exception:
        return {}

def min_distance_from_route_km(route_coords, poi_lon, poi_lat):
    # 선분-점 거리 근사 계산 (equirectangular)
    if not route_coords:
        return None
    min_d = 1e9
    # 기준 위도
    lats = [c[1] for c in route_coords]
    lat0 = sum(lats) / len(lats)
    for i in range(len(route_coords) - 1):
        lon1, lat1 = route_coords[i]
        lon2, lat2 = route_coords[i + 1]
        t, lateral = projection_on_segment_km(poi_lat, poi_lon, lat1, lon1, lat2, lon2)
        # 세그먼트 바깥이면 양 끝점 거리 고려
        if t < 0:
            d = haversine_km(poi_lat, poi_lon, lat1, lon1)
        elif t > 1:
            d = haversine_km(poi_lat, poi_lon, lat2, lon2)
        else:
            d = lateral
        if d < min_d:
            min_d = d
    return min_d if min_d < 1e9 else None

def fetch_tourapi_spots_along_route(route_geojson, sample_step=20, search_radius=5000, max_details=60):
    coords = route_geojson["features"][0]["geometry"]["coordinates"]  # [[lon, lat], ...]
    sampled = coords[::sample_step] if len(coords) > sample_step else coords
    seen = set()
    items = []

    for lon, lat in sampled:
        arr = tourapi_location_based(lon, lat, radius=search_radius, rows=20)
        for it in arr:
            cid = it.get("contentid")
            if not cid or cid in seen:
                continue
            seen.add(cid)
            try:
                ctid = int(it.get("contenttypeid"))
            except Exception:
                ctid = None
            items.append({
                "contentid": cid,
                "contenttypeid": ctid,
                "title": it.get("title"),
                "addr1": it.get("addr1"),
                "mapx": float(it.get("mapx")) if it.get("mapx") else None,
                "mapy": float(it.get("mapy")) if it.get("mapy") else None,
                "firstimage": it.get("firstimage"),
                "tel": it.get("tel")
            })

    # 디테일 정보 병합 (상위 max_details개만)
    detailed = []
    route_lonlat = coords
    for it in items[:max_details]:
        cid = it["contentid"]
        ctid = it["contenttypeid"]
        common = tourapi_detail_common(cid)
        intro = tourapi_detail_intro(cid, ctid) if ctid else {}
        lon = it["mapx"] if it["mapx"] is not None else common.get("mapx")
        lat = it["mapy"] if it["mapy"] is not None else common.get("mapy")

        # 카테고리/주차/영업시간 추출
        category = "other"
        if ctid in SIGHT_CTIDS:
            category = "sight"
            open_time = intro.get("usetime") or intro.get("usetimeculture") or ""
            rest_day = intro.get("restdate") or intro.get("restdateculture") or ""
            parking = intro.get("parking") or intro.get("parkingculture") or ""
        elif ctid == FOOD_CTID:
            category = "food"
            open_time = intro.get("opentimefood") or ""
            rest_day = intro.get("restdatefood") or ""
            parking = intro.get("parkingfood") or ""
        else:
            open_time = intro.get("usetime") or ""
            rest_day = intro.get("restdate") or ""
            parking = intro.get("parking") or ""

        pflag = False
        if isinstance(parking, str):
            pflag = any(k in parking for k in ["가능", "주차", "있음", "O", "Yes", "가능함"])
        elif isinstance(parking, (int, float)):
            pflag = parking != 0

        img = it["firstimage"] or common.get("firstimage") or common.get("firstimage2") or ""
        tel = it["tel"] or common.get("tel") or ""
        addr = it["addr1"] or common.get("addr1") or ""
        homepage = common.get("homepage", "")
        overview = common.get("overview", "")

        # 경로로부터 거리(km)
        dist_from_route = None
        if lon and lat:
            try:
                dist_from_route = min_distance_from_route_km(route_lonlat, float(lon), float(lat))
            except Exception:
                dist_from_route = None

        detailed.append({
            "id": cid,
            "contentTypeId": ctid,
            "category": category,
            "title": it["title"] or common.get("title") or "",
            "addr1": addr,
            "tel": tel,
            "homepage": homepage,
            "openTime": open_time,
            "restDay": rest_day,
            "parkingText": parking if isinstance(parking, str) else "",
            "hasParking": bool(pflag),
            "image": img,
            "mapx": float(lon) if lon else None,
            "mapy": float(lat) if lat else None,
            "overview": overview,
            "distanceFromRouteKm": dist_from_route
        })

    return detailed

# -------------------- Flask --------------------

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/route", methods=["POST"])
def route():
    try:
        data = request.get_json(force=True)
        start_str = data.get("start", "").strip()
        end_str = data.get("end", "").strip()
        if not start_str or not end_str:
            return jsonify({"error": "출발지와 도착지를 모두 입력하세요."}), 400

        start = geocode_google(start_str)
        end = geocode_google(end_str)
        if not start or not end:
            return jsonify({"error": "주소 변환에 실패했습니다."}), 400

        s_lat, s_lon = start
        e_lat, e_lon = end

        # 1~3개 경유 자동 선택
        waypoints = select_beach_waypoints(start, end)

        # ORS 경로 계산: 출발 → 해1 → 해2 → 해3 → 도착
        coords = [[s_lon, s_lat]] + [[wp[2], wp[1]] for wp in waypoints] + [[e_lon, e_lat]]
        route_gj, route_km, sc = ors_route_geojson(coords)
        if sc != 200 or route_km is None:
            # 폴백: 1경유 레거시
            legacy = find_best_beach_waypoint_legacy(start, end)
            if legacy:
                coords = [[s_lon, s_lat], [legacy[2], legacy[1]], [e_lon, e_lat]]
                route_gj, route_km, sc = ors_route_geojson(coords)
            if sc != 200 or route_km is None:
                return jsonify({"error": "경로 계산에 실패했습니다."}), 502

        # 주변 POI 수집
        spots = fetch_tourapi_spots_along_route(route_gj, sample_step=20, search_radius=5000, max_details=60)

        # 경유지 주소 역지오코딩
        waypoint_objs = []
        for idx, wp in enumerate(waypoints, start=1):
            name, lat, lon, t, lateral = wp
            addr = reverse_geocode_google(lat, lon) if GOOGLE_API_KEY else ""
            waypoint_objs.append({
                "idx": idx,
                "name": name,
                "lat": lat,
                "lon": lon,
                "t": t,
                "lateralKm": lateral,
                "address": addr
            })

        return jsonify({
            "route": route_gj,
            "routeKm": route_km,
            "waypoints": waypoint_objs,
            "spots": spots
        })

    except Exception as e:
        return jsonify({"error": f"서버 오류: {str(e)}"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
