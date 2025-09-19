from flask import Flask, request, jsonify, render_template
import os, logging
import requests
from dotenv import load_dotenv
from math import radians, cos, sin, asin, sqrt
from urllib.parse import unquote
from beaches_coordinates import beach_coords  # 기존 파일 그대로 사용

load_dotenv()

app = Flask(__name__)
log = logging.getLogger("coastal-drive")
logging.basicConfig(level=logging.INFO, format="%(levelname)s:coastal-drive:%(message)s")

# === ENV ===
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "").strip()
ORS_API_KEY     = os.getenv("ORS_API_KEY", "").strip()

# TourAPI: Encoding 값이 들어와도 자동 unquote
_raw_tour_key = os.getenv("TOURAPI_KEY", "").strip()
TOURAPI_KEY = unquote(_raw_tour_key) if "%" in _raw_tour_key else _raw_tour_key

# 기본은 KorService2, 자동 폴백 KorService1
TOURAPI_BASE_ENV = os.getenv("TOURAPI_BASE", "").strip()
TOURAPI_BASES = [
    TOURAPI_BASE_ENV if TOURAPI_BASE_ENV else "https://apis.data.go.kr/B551011/KorService2",
    "https://apis.data.go.kr/B551011/KorService1",
]

def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2)**2
    return 2 * R * asin(sqrt(a))

# ---------------- Google Geocode ----------------
def geocode_google(address):
    try:
        url = "https://maps.googleapis.com/maps/api/geocode/json"
        params = {"address": address, "key": GOOGLE_API_KEY}
        res = requests.get(url, params=params, timeout=8)
        res.raise_for_status()
        j = res.json()
        loc = j["results"][0]["geometry"]["location"]
        return loc["lat"], loc["lng"]
    except Exception:
        return None

def reverse_geocode_google(lat, lon):
    try:
        url = "https://maps.googleapis.com/maps/api/geocode/json"
        params = {"latlng": f"{lat},{lon}", "key": GOOGLE_API_KEY}
        res = requests.get(url, params=params, timeout=8)
        j = res.json()
        return j["results"][0]["formatted_address"]
    except Exception:
        return "주소 불러오기 실패"

# ---------------- Waypoint (기존 유지) ----------------
def is_in_coastal_bounds(lat, lon):
    return (
        (35 <= lat <= 38 and 128 <= lon <= 131) or
        (33 <= lat <= 35 and 126 <= lon <= 129) or
        (34 <= lat <= 38 and 124 <= lon <= 126)
    )

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
    return best_lat if best_lat and (not best_lon or best_lat[3] < best_lon[3]) else best_lon

# ---------------- ORS (기존 유지) ----------------
def get_ors_route(start, waypoint, end):
    url = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
    headers = {"Authorization": ORS_API_KEY, "Content-Type": "application/json"}
    body = {"coordinates": [[start[1], start[0]],[waypoint[2], waypoint[1]],[end[1], end[0]]]}
    res = requests.post(url, headers=headers, json=body, timeout=15)
    try:
        return res.json(), res.status_code
    except Exception as e:
        return {"error": str(e)}, 500

# ---------------- TourAPI 공통 호출(HTTPS + 2→1 폴백) ----------------
def call_tourapi(path, params, timeout=8):
    # 모든 호출에 Decoding(원문) 키 사용 (requests가 자동 인코딩)
    base_params = {
        "serviceKey": TOURAPI_KEY,
        "MobileOS": "ETC",
        "MobileApp": "CoastalDrive",
        "_type": "json"
    }
    merged = {**base_params, **params}

    last_err = None
    for base in TOURAPI_BASES:
        url = f"{base}/{path}"
        try:
            r = requests.get(url, params=merged, timeout=timeout)
            txt = r.text.strip()
            # JSON 시도
            try:
                j = r.json()
                # 정상/오류 구분을 위해 코드/메시지 보조 로그
                code = j.get("response",{}).get("header",{}).get("resultCode") \
                    or j.get("response",{}).get("body",{}).get("resultCode")
                msg  = j.get("response",{}).get("header",{}).get("resultMsg") \
                    or j.get("response",{}).get("body",{}).get("resultMsg")
                if code and code != "0000":
                    log.warning(f"[TourAPI] error code={code} msg={msg} base={base} path={path}")
                return j
            except Exception:
                # JSON 아니면 XML/HTML 오류 → 다음 base로 폴백
                log.warning(f"[TourAPI] non-JSON from {base}/{path}: {txt[:120]}...")
                last_err = txt
        except Exception as e:
            last_err = str(e)
            log.warning(f"[TourAPI] request error base={base} path={path}: {e}")
    # 둘 다 실패
    return {"_error": last_err}

# ---------------- 경로 주변 검색 (반경/샘플링 자동 조정) ----------------
def query_location_based(lon, lat, radius, content_type_id=None):
    params = {
        "mapX": lon, "mapY": lat, "radius": radius,
        "listYN": "Y", "arrange": "E",  # 거리순
        "numOfRows": 20, "pageNo": 1
    }
    if content_type_id:
        params["contentTypeId"] = str(content_type_id)
    j = call_tourapi("locationBasedList1", params)
    if "_error" in j:
        return [], j["_error"]

    try:
        items = j["response"]["body"]["items"]["item"]
        return items, None
    except Exception:
        return [], "empty"

def search_along_route(geojson):
    coords = geojson["features"][0]["geometry"]["coordinates"]
    # 경로가 길어도 호출 건수 아끼면서 커버: 12~15 포인트 정도
    step = max(1, len(coords)//12)
    samples = coords[::step]
    log.info(f"[TourAPI] samples={len(samples)}")

    results_tour, results_food = [], []
    seen = set()

    for radius in (5000, 10000, 20000):  # TourAPI 허용 범위 안에서만 확대
        for lon, lat in samples:
            # 관광(기본)
            items, err = query_location_based(lon, lat, radius)
            if items:
                for it in items:
                    cid = it.get("contentid")
                    if not cid or cid in seen:
                        continue
                    seen.add(cid)
                    results_tour.append({
                        "contentid": cid,
                        "title": it.get("title"),
                        "addr1": it.get("addr1"),
                        "mapx": it.get("mapx"),
                        "mapy": it.get("mapy"),
                        "firstimage": it.get("firstimage"),
                        "distance": round(haversine(lat, lon, float(it.get("mapy", lat)), float(it.get("mapx", lon))), 2)
                    })
            # 맛집(39)
            items39, err = query_location_based(lon, lat, radius, content_type_id=39)
            if items39:
                for it in items39:
                    cid = it.get("contentid")
                    if not cid or cid in seen:
                        continue
                    seen.add(cid)
                    results_food.append({
                        "contentid": cid,
                        "title": it.get("title"),
                        "addr1": it.get("addr1"),
                        "mapx": it.get("mapx"),
                        "mapy": it.get("mapy"),
                        "firstimage": it.get("firstimage"),
                        "distance": round(haversine(lat, lon, float(it.get("mapy", lat)), float(it.get("mapx", lon))), 2)
                    })

        # 충분히 모였으면 조기 종료
        if len(results_tour) + len(results_food) >= 20:
            break

    log.info(f"[TourAPI] collected tour={len(results_tour)} food={len(results_food)} total={len(results_tour)+len(results_food)}")
    return results_tour, results_food

# ---------------- Flask routes ----------------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/route", methods=["POST"])
def route():
    try:
        data = request.get_json() or {}
        start = geocode_google(data.get("start", ""))
        end   = geocode_google(data.get("end",   ""))

        if not start or not end:
            return jsonify({"error": "❌ 주소 변환 실패"}), 400

        waypoint = find_best_beach_waypoint(start, end)
        if not waypoint:
            return jsonify({"error": "❌ 경유지 탐색 실패"}), 500

        route_data, status = get_ors_route(start, waypoint, end)
        if "error" in route_data:
            return jsonify({"error": route_data["error"]}), status

        # 경로 시간/거리 (프론트에서 ETA 표시용)
        try:
            summary = route_data["features"][0]["properties"]["summary"]
            eta_sec = summary.get("duration", 0)
            dist_km = round((summary.get("distance", 0) / 1000.0), 1)
        except Exception:
            eta_sec, dist_km = 0, 0

        spots, restaurants = search_along_route(route_data)
        waypoint_addr = reverse_geocode_google(waypoint[1], waypoint[2])

        return jsonify({
            "route": route_data,
            "waypoint": {
                "name": waypoint[0],
                "lat": waypoint[1],
                "lon": waypoint[2],
                "address": waypoint_addr
            },
            "eta_sec": eta_sec,
            "dist_km": dist_km,
            "spots": spots or [],
            "restaurants": restaurants or []
        })
    except Exception as e:
        return jsonify({"error": f"❌ 서버 오류: {str(e)}"}), 500

@app.route("/tour_detail/<contentid>")
def tour_detail(contentid):
    j = call_tourapi("detailCommon1", {
        "contentId": contentid,
        "overviewYN": "Y",
        "defaultYN": "Y",
        "firstImageYN": "Y",
        "addrinfoYN": "Y",
        "mapinfoYN": "Y",
        "areacodeYN": "Y",
        "catcodeYN": "Y",
    }, timeout=10)

    if "_error" in j:
        return f"<h2>TourAPI 오류</h2><pre>{j['_error']}</pre>", 500

    try:
        item = j["response"]["body"]["items"]["item"][0]
    except Exception:
        return f"<h2>❌ 관광지 정보가 없습니다.</h2><p>contentid: {contentid}</p>", 404

    return render_template("tour_detail.html", item=item)

# 진단용: 키/승인/기간/엔드포인트 확인
@app.route("/debug/tour")
def debug_tour():
    x = float(request.args.get("x", "127.0"))
    y = float(request.args.get("y", "37.5"))
    j = call_tourapi("locationBasedList1", {
        "mapX": x, "mapY": y, "radius": 5000, "listYN": "Y", "arrange": "E", "numOfRows": 3, "pageNo": 1
    })
    if "_error" in j:
        return jsonify({"ok": False, "error": j["_error"], "hint": "TOURAPI_KEY Decoding(원문) 사용 여부/승인/기간 확인"}), 200
    try:
        items = j["response"]["body"]["items"]["item"]
        header = j["response"]["header"]
        return jsonify({"ok": True, "status": header, "count": len(items), "first": items[0] if items else None}), 200
    except Exception:
        return jsonify({"ok": False, "raw": j}), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
