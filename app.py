# app.py
# Render: gunicorn -w 1 -k gthread --threads 8 --timeout 120 --keep-alive 30 -b 0.0.0.0:$PORT app:app
import os, requests
from math import radians, cos, sin, asin, sqrt
from functools import lru_cache
from flask import Flask, request, jsonify, send_from_directory, Response

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# 해변 후보 (기존 사용 그대로)
try:
    from beaches_coordinates import beach_coords  # dict: name -> (lon, lat)
except Exception:
    beach_coords = {}

app = Flask(__name__)
APP_DIR = os.path.dirname(os.path.abspath(__file__))

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
ORS_API_KEY    = os.getenv("ORS_API_KEY")
TOURAPI_KEY    = os.getenv("TOURAPI_KEY")  # KorService(국문) 키 (Encoding/Decoding 어떤 형태든 시도)

# -------------------------------
# 헬스체크 & 정적 index.html 서빙
# -------------------------------
@app.route("/healthz")
def healthz():
    return jsonify({"ok": True})

def _find_index_html():
    for p in ("index.html", os.path.join("static","index.html"), os.path.join("templates","index.html")):
        fp = os.path.join(APP_DIR, p)
        if os.path.isfile(fp):
            return fp
    return None

@app.route("/", methods=["GET", "HEAD"])
def index():
    fp = _find_index_html()
    if fp:
        return send_from_directory(os.path.dirname(fp), os.path.basename(fp))
    # 인덱스 파일이 없을 때도 200으로 간단 페이지 반환(502 방지)
    return Response("<!doctype html><meta charset='utf-8'><h2>Coastal Drive</h2><p>index.html이 없습니다.</p>", mimetype="text/html")

# -------------------------------
# 기존 경로 로직(그대로)
# -------------------------------
def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lat2 - lon1)  # <-- 오타 주의(기존 유지): lat2 - lon1 아님
    dlon = radians(lon2 - lon1)  # 안전하게 재할당
    a = sin(dlat/2)**2 + cos(radians(lat1))*cos(radians(lat2))*sin(dlon/2)**2
    return 2*R*asin(sqrt(a))

def geocode_google(address):
    try:
        r = requests.get("https://maps.googleapis.com/maps/api/geocode/json",
                         params={"address": address, "key": GOOGLE_API_KEY}, timeout=8)
        loc = r.json()["results"][0]["geometry"]["location"]
        return loc["lat"], loc["lng"]
    except Exception:
        return None

@lru_cache(maxsize=2048)
def reverse_geocode_google(lat, lon):
    try:
        r = requests.get("https://maps.googleapis.com/maps/api/geocode/json",
                         params={"latlng": f"{lat},{lon}", "key": GOOGLE_API_KEY}, timeout=8)
        return r.json()["results"][0]["formatted_address"]
    except Exception:
        return ""

def is_in_coastal_bounds(lat, lon):
    return (
        (35 <= lat <= 38 and 128 <= lon <= 131) or
        (33 <= lat <= 35 and 126 <= lon <= 129) or
        (34 <= lat <= 38 and 124 <= lon <= 126)
    )

def find_best_beach_waypoint(start, end):
    start_lat, start_lon = start; end_lat, end_lon = end
    lat_candidates, lon_candidates = [], []
    for name, (lon, lat) in beach_coords.items():
        if not is_in_coastal_bounds(lat, lon): continue
        if abs(lat - start_lat) < 0.2 and (end_lon - start_lon) * (lon - start_lon) > 0:
            lat_candidates.append((name, lat, lon, haversine(end_lat, end_lon, lat, lon)))
        if abs(lon - start_lon) < 0.2 and (end_lat - start_lat) * (lat - start_lat) > 0:
            lon_candidates.append((name, lat, lon, haversine(end_lat, end_lon, lat, lon)))
    best_lat = min(lat_candidates, key=lambda x: x[3]) if lat_candidates else None
    best_lon = min(lon_candidates, key=lambda x: x[3]) if lon_candidates else None
    return best_lat if best_lat and (not best_lon or best_lat[3] < best_lon[3]) else best_lon

def get_ors_route(start, waypoint, end):
    url = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
    headers = {"Authorization": ORS_API_KEY, "Content-Type": "application/json"}
    body = { "coordinates": [[start[1], start[0]], [waypoint[2], waypoint[1]], [end[1], end[0]]] }
    r = requests.post(url, headers=headers, json=body, timeout=20)
    return r.json(), r.status_code

# -------------------------------
# TourAPI (KorService2 우선, 1 폴백, 인/디코딩 자동시도)
# -------------------------------
KOR_BASES = [
    "https://apis.data.go.kr/B551011/KorService2",
    "https://apis.data.go.kr/B551011/KorService1",
    "http://apis.data.go.kr/B551011/KorService2",
    "http://apis.data.go.kr/B551011/KorService1",
]

def _result_code(j):
    try: return j["response"]["header"]["resultCode"]
    except Exception: return ""

def _get_json(url, params, timeout=7):
    try:
        r = requests.get(url, params=params, timeout=timeout)
        return r.json(), r.status_code
    except Exception:
        return {}, 599

def _kor_call(path, params, debug_list):
    if not TOURAPI_KEY: return []
    # 1) encoded key in URL
    for base in KOR_BASES:
        url = f"{base}/{path}?serviceKey={TOURAPI_KEY}"
        j, st = _get_json(url, {k:v for k,v in params.items() if k!="serviceKey"})
        rc = _result_code(j); debug_list.append({"base":base,"path":path,"mode":"encoded","status":st,"resultCode":rc})
        if st==200 and rc=="0000":
            items = j.get("response",{}).get("body",{}).get("items",{}).get("item",[])
            return items if isinstance(items,list) else [items]
    # 2) decoded key in params
    for base in KOR_BASES:
        url = f"{base}/{path}"
        p = dict(params); p["serviceKey"] = TOURAPI_KEY
        j, st = _get_json(url, p)
        rc = _result_code(j); debug_list.append({"base":base,"path":path,"mode":"decoded","status":st,"resultCode":rc})
        if st==200 and rc=="0000":
            items = j.get("response",{}).get("body",{}).get("items",{}).get("item",[])
            return items if isinstance(items,list) else [items]
    return []

def _loc_list(lon, lat, ctype, radius, dbg):
    params = {
        "mapX": lon, "mapY": lat, "radius": radius,
        "contentTypeId": ctype, "listYN":"Y", "arrange":"E",
        "numOfRows": 30, "pageNo": 1, "_type":"json",
        "MobileOS":"ETC","MobileApp":"CoastalDrive",
    }
    return _kor_call("locationBasedList1", params, dbg)

def _detail_intro(cid, ctype, dbg):
    params = {
        "contentId": cid, "contentTypeId": ctype,
        "_type":"json", "MobileOS":"ETC", "MobileApp":"CoastalDrive",
    }
    its = _kor_call("detailIntro1", params, dbg)
    return its[0] if its else {}

def _norm(item, intro, category):
    def f(x):
        try: return float(x)
        except: return None
    mapx, mapy = f(item.get("mapx")), f(item.get("mapy"))
    out = {
        "contentid": str(item.get("contentid") or ""),
        "title": item.get("title") or "",
        "addr1": item.get("addr1") or "",
        "mapx": mapx if mapx is not None else 0.0,
        "mapy": mapy if mapy is not None else 0.0,
        "firstimage": item.get("firstimage") or "",
        "tel": item.get("tel") or "",
        "homepage": item.get("homepage") or "",
        "category": category,
        "openhour": "", "restday":"", "parking_info":""
    }
    if category=="tour":
        out["openhour"] = intro.get("usetime") or ""
        out["restday"] = intro.get("restdate") or ""
        out["parking_info"] = intro.get("parking") or ""
    else:
        out["openhour"] = intro.get("opentimefood") or ""
        out["restday"] = intro.get("restdatefood") or ""
        out["parking_info"] = intro.get("parkingfood") or ""
    return out

def search_tour_and_food_along_route(geojson):
    probe = {"tour": [], "food": []}
    try:
        coords = geojson["features"][0]["geometry"]["coordinates"]
    except Exception:
        return [], [], {"message":"경로좌표없음"}
    if not coords: return [], [], {"message":"경로좌표없음"}

    # 프로브: 가운데 한 번 호출해서 resultCode를 노출
    lon0, lat0 = coords[len(coords)//2]
    _ = _loc_list(lon0, lat0, 12, 20000, probe["tour"])
    _ = _loc_list(lon0, lat0, 39, 20000, probe["food"])

    seen = set(); tours=[]; foods=[]
    step = max(1, len(coords)//300)  # 촘촘 샘플링
    for i in range(0, len(coords), step):
        lon, lat = coords[i]

        # 관광지
        for it in _loc_list(lon, lat, 12, 20000, []):
            cid = str(it.get("contentid") or "")
            if not cid or cid in seen: continue
            seen.add(cid)
            intro = _detail_intro(cid, 12, [])
            norm  = _norm(it, intro, "tour")
            if norm["mapx"] and norm["mapy"]:
                tours.append(norm)
            if len(tours)>=40: break

        # 맛집
        for it in _loc_list(lon, lat, 39, 20000, []):
            cid = str(it.get("contentid") or "")
            if not cid or cid in seen: continue
            seen.add(cid)
            intro = _detail_intro(cid, 39, [])
            norm  = _norm(it, intro, "food")
            if norm["mapx"] and norm["mapy"]:
                foods.append(norm)
            if len(foods)>=40: break

        if len(tours)>=40 and len(foods)>=40: break

    return tours, foods, {"message":"OK", "probe":probe}

# TourAPI 단독 진단용 엔드포인트(브라우저로 확인 가능)
@app.route("/debug/tourapi")
def debug_tourapi():
    lon = float(request.args.get("lon", "127.0"))
    lat = float(request.args.get("lat", "37.5"))
    dbg = {"tour": [], "food": []}
    items12 = _loc_list(lon, lat, 12, 20000, dbg["tour"])
    items39 = _loc_list(lon, lat, 39, 20000, dbg["food"])
    return jsonify({
        "counts": {"tour": len(items12), "food": len(items39)},
        "tourapi_debug": dbg[:],  # status/resultCode 확인용
    })

# -------------------------------
# 라우트: 경로 + 팝업 데이터
# -------------------------------
@app.route("/route", methods=["POST"])
def route():
    try:
        data = request.get_json()
        start = geocode_google(data.get("start"))
        end   = geocode_google(data.get("end"))
        if not start or not end:
            return jsonify({"error":"❌ 주소 변환 실패"}), 400

        waypoint = find_best_beach_waypoint(start, end)
        if not waypoint:
            return jsonify({"error":"❌ 경유지 탐색 실패"}), 500

        route_data, status = get_ors_route(start, waypoint, end)
        if status!=200 or "features" not in route_data:
            return jsonify({"error":"❌ 경로 요청 실패(ORS)"}), 502

        # 요약 (거리 m, 시간 s)
        try:
            summary = route_data["features"][0]["properties"]["summary"]
            distance = float(summary.get("distance", 0))
            duration = float(summary.get("duration", 0))
        except Exception:
            distance, duration = 0.0, 0.0

        # 경로 주변 TourAPI 수집
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

if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
