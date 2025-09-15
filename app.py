# app.py
# 실행 예:
#   Windows: set PORT=10000 && python app.py
#   macOS/Linux: export PORT=10000 && python app.py
#   Render: gunicorn -w 1 -k gthread --threads 8 --timeout 120 --keep-alive 30 -b 0.0.0.0:$PORT app:app

import os
import requests
from math import radians, cos, sin, asin, sqrt
from functools import lru_cache
from flask import Flask, request, jsonify, render_template_string

# .env(optional)
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# 이전에 쓰던 해변 좌표 그대로 사용
try:
    from beaches_coordinates import beach_coords  # dict: name -> (lon, lat)
except Exception:
    beach_coords = {}

app = Flask(__name__)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
ORS_API_KEY    = os.getenv("ORS_API_KEY")
TOURAPI_KEY    = os.getenv("TOURAPI_KEY")  # KorService(국문) 키 (인코딩/디코딩 어떤 형태든 자동 시도)

# -------------------------------
# 기존 경로 로직 (손대지 않음)
# -------------------------------
def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1))*cos(radians(lat2))*sin(dlon/2)**2
    return 2 * R * asin(sqrt(a))  # km

def geocode_google(address):
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    try:
        res = requests.get(url, params={"address": address, "key": GOOGLE_API_KEY}, timeout=8)
        loc = res.json()["results"][0]["geometry"]["location"]
        return loc["lat"], loc["lng"]
    except Exception:
        return None

@lru_cache(maxsize=2048)
def reverse_geocode_google(lat, lon):
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    try:
        res = requests.get(url, params={"latlng": f"{lat},{lon}", "key": GOOGLE_API_KEY}, timeout=8)
        return res.json()["results"][0]["formatted_address"]
    except Exception:
        return ""

def is_in_coastal_bounds(lat, lon):
    return (
        (35 <= lat <= 38 and 128 <= lon <= 131) or
        (33 <= lat <= 35 and 126 <= lon <= 129) or
        (34 <= lat <= 38 and 124 <= lon <= 126)
    )

def find_best_beach_waypoint(start, end):
    # 예전에 쓰던 간단 규칙 그대로
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

def get_ors_route(start, waypoint, end):
    """예전에 성공한 ORS 호출 그대로(출발→경유1→도착)"""
    url = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
    headers = {"Authorization": ORS_API_KEY, "Content-Type": "application/json"}
    body = {
        "coordinates": [
            [start[1], start[0]],
            [waypoint[2], waypoint[1]],
            [end[1], end[0]]
        ]
    }
    res = requests.post(url, headers=headers, json=body, timeout=20)
    return res.json(), res.status_code

# -------------------------------------------
# TourAPI (KorService2 우선 + 1 폴백 + 키자동)
# -------------------------------------------
KOR_BASES = [
    "https://apis.data.go.kr/B551011/KorService2",
    "https://apis.data.go.kr/B551011/KorService1",
    "http://apis.data.go.kr/B551011/KorService2",
    "http://apis.data.go.kr/B551011/KorService1",
]

def _result_code(j):
    try:
        return j["response"]["header"]["resultCode"]
    except Exception:
        return ""

def _get_json(url, params, timeout=7):
    try:
        r = requests.get(url, params=params, timeout=timeout)
        return r.json(), r.status_code
    except Exception:
        return {}, 599

def _kor_call(path, params, debug_list):
    """encoded키(URL 부착) + decoded키(params) 둘 다 시도, KorService2→1→http 폴백"""
    if not TOURAPI_KEY:
        return []

    # 시도1: encoded key를 URL에 직접 붙임
    for base in KOR_BASES:
        url1 = f"{base}/{path}?serviceKey={TOURAPI_KEY}"
        j, st = _get_json(url1, {k: v for k, v in params.items() if k != "serviceKey"})
        rc = _result_code(j)
        debug_list.append({"base": base, "path": path, "mode": "encoded", "status": st, "resultCode": rc})
        if st == 200 and rc == "0000":
            items = j.get("response", {}).get("body", {}).get("items", {}).get("item", [])
            if isinstance(items, dict):
                items = [items]
            if items:
                return items

    # 시도2: decoded key를 params로
    for base in KOR_BASES:
        url2 = f"{base}/{path}"
        p = dict(params); p["serviceKey"] = TOURAPI_KEY  # 디코딩된 키여도 OK
        j, st = _get_json(url2, p)
        rc = _result_code(j)
        debug_list.append({"base": base, "path": path, "mode": "decoded", "status": st, "resultCode": rc})
        if st == 200 and rc == "0000":
            items = j.get("response", {}).get("body", {}).get("items", {}).get("item", [])
            if isinstance(items, dict):
                items = [items]
            if items:
                return items

    return []

def _loc_list(lon, lat, ctype, radius, debug_list):
    params = {
        "mapX": lon, "mapY": lat, "radius": radius,
        "contentTypeId": ctype, "listYN": "Y", "arrange": "E",
        "numOfRows": 30, "pageNo": 1, "_type": "json",
        "MobileOS": "ETC", "MobileApp": "CoastalDrive",
    }
    return _kor_call("locationBasedList1", params, debug_list)

def _detail_intro(cid, ctype, debug_list):
    params = {
        "contentId": cid, "contentTypeId": ctype,
        "_type": "json", "MobileOS": "ETC", "MobileApp": "CoastalDrive",
    }
    items = _kor_call("detailIntro1", params, debug_list)
    return items[0] if items else {}

def _norm(item, intro, category):
    def fnum(x):
        try: return float(x)
        except Exception: return None
    mapx = fnum(item.get("mapx")); mapy = fnum(item.get("mapy"))
    out = {
        "contentid": str(item.get("contentid") or ""),
        "title": item.get("title") or "",
        "addr1": item.get("addr1") or "",
        "mapx": mapx if mapx is not None else 0.0,
        "mapy": mapy if mapy is not None else 0.0,
        "firstimage": item.get("firstimage") or "",
        "tel": item.get("tel") or "",
        "homepage": item.get("homepage") or "",
        "category": category,  # 'tour' or 'food'
        "openhour": "",
        "restday": "",
        "parking_info": "",
    }
    if category == "tour":
        out["openhour"]     = intro.get("usetime") or ""
        out["restday"]      = intro.get("restdate") or ""
        out["parking_info"] = intro.get("parking") or ""
    else:
        out["openhour"]     = intro.get("opentimefood") or ""
        out["restday"]      = intro.get("restdatefood") or ""
        out["parking_info"] = intro.get("parkingfood") or ""
    return out

def search_tour_and_food_along_route(geojson):
    """경로선 주변 30km 근사: 반경 20km(최대 권장)로 촘촘 샘플링 → 누락 최소화"""
    probe = {"tour": [], "food": []}  # 진단용
    try:
        coords = geojson["features"][0]["geometry"]["coordinates"]  # [ [lon,lat]... ]
    except Exception:
        return [], [], {"message": "경로좌표없음"}
    if not coords:
        return [], [], {"message": "경로좌표없음"}

    seen = set()
    tours, foods = [], []

    # 첫 중간 지점에서 프로브(상태 확인)
    lon0, lat0 = coords[len(coords)//2]
    _ = _loc_list(lon0, lat0, 12, 20000, probe["tour"])
    _ = _loc_list(lon0, lat0, 39, 20000, probe["food"])

    # 본 수집
    step = max(1, len(coords)//300)  # 촘촘
    for idx in range(0, len(coords), step):
        lon, lat = coords[idx]

        # 관광지(12)
        items = _loc_list(lon, lat, 12, 20000, [])
        for it in items:
            cid = str(it.get("contentid") or "")
            if not cid or cid in seen: continue
            seen.add(cid)
            intro = _detail_intro(cid, 12, [])
            norm  = _norm(it, intro, "tour")
            if norm["mapx"] and norm["mapy"]:
                tours.append(norm)
            if len(tours) >= 40: break

        # 맛집(39)
        items = _loc_list(lon, lat, 39, 20000, [])
        for it in items:
            cid = str(it.get("contentid") or ""
                     )
            if not cid or cid in seen: continue
            seen.add(cid)
            intro = _detail_intro(cid, 39, [])
            norm  = _norm(it, intro, "food")
            if norm["mapx"] and norm["mapy"]:
                foods.append(norm)
            if len(foods) >= 40: break

        if len(tours) >= 40 and len(foods) >= 40:
            break

    return tours, foods, {"message": "OK", "probe": probe}

# -------------------------------
# 라우팅 엔드포인트 (경로 그대로)
# -------------------------------
@app.route("/route", methods=["POST"])
def route():
    try:
        data = request.get_json()
        start = geocode_google(data.get("start"))
        end   = geocode_google(data.get("end"))
        if not start or not end:
            return jsonify({"error": "❌ 주소 변환 실패"}), 400

        waypoint = find_best_beach_waypoint(start, end)
        if not waypoint:
            return jsonify({"error": "❌ 경유지 탐색 실패"}), 500

        route_data, status = get_ors_route(start, waypoint, end)
        if status != 200 or not isinstance(route_data, dict) or "features" not in route_data:
            return jsonify({"error": "❌ 경로 요청 실패(ORS)"}), 502

        # 거리/시간 요약
        try:
            summary = route_data["features"][0]["properties"]["summary"]
            distance = float(summary.get("distance", 0.0))
            duration = float(summary.get("duration", 0.0))
        except Exception:
            distance, duration = 0.0, 0.0

        # 경로 주변 관광/맛집 (팝업용)
        tour_list, food_list, probe = search_tour_and_food_along_route(route_data)
        spots = tour_list + food_list

        return jsonify({
            "route": route_data,
            "summary": {"distance": distance, "duration": duration},
            "waypoint": {
                "name": waypoint[0], "lat": waypoint[1], "lon": waypoint[2],
                "address": reverse_geocode_google(waypoint[1], waypoint[2]) or ""
            },
            "spots": spots,
            "spots_grouped": {"tour": tour_list, "food": food_list},
            "tourapi_probe": probe
        }), 200

    except Exception as e:
        return jsonify({"error": f"❌ 서버 오류: {str(e)}"}), 500

# (옵션) 간단 상세 페이지
@app.route("/tour_detail/<contentid>")
def tour_detail(contentid):
    # 최소 렌더 (필요시 detailCommon1로 확장 가능)
    html = f"""
    <!doctype html><meta charset="utf-8">
    <h2>관광지 상세</h2>
    <p>contentid: {contentid}</p>
    <p>자세한 설명은 화면의 '홈페이지' 링크 또는 한국관광공사 상세 API 연동으로 보완하세요.</p>
    """
    return render_template_string(html)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
