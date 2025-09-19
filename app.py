from flask import Flask, request, jsonify, render_template
import os, logging, time
import requests
from dotenv import load_dotenv
from math import radians, cos, sin, asin, sqrt
from urllib.parse import unquote
from beaches_coordinates import beach_coords  # 기존 파일

load_dotenv()

app = Flask(__name__)
log = logging.getLogger("coastal-drive")
logging.basicConfig(level=logging.INFO, format="%(levelname)s:coastal-drive:%(message)s")

# === ENV ===
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "").strip()
ORS_API_KEY    = os.getenv("ORS_API_KEY", "").strip()

# TourAPI 키: Encoding 값이 들어와도 자동 복구 (그래도 환경변수는 원문으로 저장 권장)
_raw_tour_key = os.getenv("TOURAPI_KEY", "").strip()
TOURAPI_KEY   = unquote(_raw_tour_key) if "%" in _raw_tour_key else _raw_tour_key

# 기본은 KorService2, 실패 시 1로 폴백
TOURAPI_BASE_ENV = os.getenv("TOURAPI_BASE", "").strip()
TOURAPI_BASES = [
    TOURAPI_BASE_ENV if TOURAPI_BASE_ENV else "https://apis.data.go.kr/B551011/KorService2",
    "https://apis.data.go.kr/B551011/KorService1",
]

def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = radians(lat2 - lat1); dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1))*cos(radians(lat2))*sin(dlon/2)**2
    return 2 * R * asin(sqrt(a))

# ---------- Google Geocoding (기존 성공 로직) ----------
def geocode_google(address):
    try:
        url = "https://maps.googleapis.com/maps/api/geocode/json"
        res = requests.get(url, params={"address": address, "key": GOOGLE_API_KEY}, timeout=8)
        res.raise_for_status()
        loc = res.json()["results"][0]["geometry"]["location"]
        return loc["lat"], loc["lng"]
    except Exception:
        return None

def reverse_geocode_google(lat, lon):
    try:
        url = "https://maps.googleapis.com/maps/api/geocode/json"
        res = requests.get(url, params={"latlng": f"{lat},{lon}", "key": GOOGLE_API_KEY}, timeout=8)
        return res.json()["results"][0]["formatted_address"]
    except Exception:
        return "주소 불러오기 실패"

# ---------- 경유지 선택 (기존 성공 로직) ----------
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

# ---------- ORS (기존 성공 로직) ----------
def get_ors_route(start, waypoint, end):
    url = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
    headers = {"Authorization": ORS_API_KEY, "Content-Type": "application/json"}
    body = {"coordinates": [[start[1], start[0]],[waypoint[2], waypoint[1]],[end[1], end[0]]]}
    res = requests.post(url, headers=headers, json=body, timeout=15)
    try:
        return res.json(), res.status_code
    except Exception as e:
        return {"error": str(e)}, 500

# ---------- TourAPI 공통 호출 (HTTPS + 2→1 폴백) ----------
def call_tourapi(path, params, timeout=8):
    base_params = {
        "serviceKey": TOURAPI_KEY,
        "MobileOS": "ETC",
        "MobileApp": "CoastalDrive",
        "_type": "json"
    }
    merged = {**base_params, **params}

    last_txt = None
    last_status = None
    for base in TOURAPI_BASES:
        url = f"{base}/{path}"
        try:
            r = requests.get(url, params=merged, timeout=timeout)
            last_status = r.status_code
            txt = r.text.strip()
            try:
                j = r.json()
                hdr = j.get("response", {}).get("header", {})
                code = hdr.get("resultCode")
                if code and code != "0000":
                    # 인증/탐색 오류 메시지도 로그로 남겨 추적
                    log.warning(f"[TourAPI] resultCode={code} msg={hdr.get('resultMsg')} url={url}")
                return j
            except Exception:
                log.warning(f"[TourAPI] non-JSON {last_status} url={url} body={txt[:160]}...")
                last_txt = txt
        except Exception as e:
            log.warning(f"[TourAPI] request error url={url} err={e}")
            last_txt = str(e)
        time.sleep(0.1)  # 게이트웨이 보호 (과속 방지)
    return {"_error": f"status={last_status}, body={str(last_txt)[:200]}"}

def _normalize_items(items_field):
    if not items_field:
        return []
    if isinstance(items_field, list):
        return items_field
    return [items_field]

# ---------- 경로 주변 수집(샘플 ~12, 반경 5→10→20km) ----------
def query_location_based(lon, lat, radius, content_type_id=None):
    params = {
        "mapX": lon, "mapY": lat, "radius": radius,
        "listYN": "Y", "arrange": "E",  # 거리순
        "numOfRows": 20, "pageNo": 1
    }
    if content_type_id:
        params["contentTypeId"] = str(content_type_id)

    j = call_tourapi("locationBasedList1", params)
    time.sleep(0.12)  # 초당 호출 제한 여유

    if "_error" in j:
        return [], j["_error"]

    try:
        body = j["response"]["body"]
        items = _normalize_items(body.get("items", {}).get("item"))
        return items, None
    except Exception:
        return [], "empty"

def search_along_route(geojson):
    coords = geojson["features"][0]["geometry"]["coordinates"]
    step = max(1, len(coords)//12)  # 샘플 ~12개
    samples = coords[::step]
    log.info(f"[TourAPI] samples={len(samples)}")

    results_tour, results_food = [], []
    seen = set()

    for radius in (5000, 10000, 20000):
        for lon, lat in samples:
            # 관광지
            items, _ = query_location_based(lon, lat, radius)
            for it in items:
                cid = it.get("contentid")
                if not cid or cid in seen: 
                    continue
                seen.add(cid)
                try:
                    mx, my = float(it.get("mapx")), float(it.get("mapy"))
                except Exception:
                    continue
                results_tour.append({
                    "contentid": cid,
                    "title": it.get("title"),
                    "addr1": it.get("addr1"),
                    "mapx": mx, "mapy": my,
                    "firstimage": it.get("firstimage"),
                    "distance": round(haversine(lat, lon, my, mx), 2)
                })

            # 맛집(39)
            items39, _ = query_location_based(lon, lat, radius, content_type_id=39)
            for it in items39:
                cid = it.get("contentid")
                if not cid or cid in seen:
                    continue
                seen.add(cid)
                try:
                    mx, my = float(it.get("mapx")), float(it.get("mapy"))
                except Exception:
                    continue
                results_food.append({
                    "contentid": cid,
                    "title": it.get("title"),
                    "addr1": it.get("addr1"),
                    "mapx": mx, "mapy": my,
                    "firstimage": it.get("firstimage"),
                    "distance": round(haversine(lat, lon, my, mx), 2)
                })

        # 충분히 모이면 조기 종료
        if len(results_tour) + len(results_food) >= 20:
            break

    log.info(f"[TourAPI] collected tour={len(results_tour)} food={len(results_food)} total={len(results_tour)+len(results_food)}")
    return results_tour, results_food

# ---------- Routes ----------
@app.route("/")
def index():
    return render_template("index.html")

# Render Health Check용 (빠른 200)
@app.route("/healthz")
def healthz():
    return "ok", 200

# TourAPI 진단: 키/권한/포맷 즉시 확인
@app.route("/debug/tour")
def debug_tour():
    x = float(request.args.get("x", "127.0"))
    y = float(request.args.get("y", "37.5"))
    j = call_tourapi("locationBasedList1", {
        "mapX": x, "mapY": y, "radius": 5000, "listYN": "Y", "arrange": "E", "numOfRows": 3, "pageNo": 1
    })
    if "_error" in j:
        return jsonify({"ok": False, "error": j["_error"], "hint": "TOURAPI_KEY는 Decoding(원문) 값이어야 합니다."}), 200
    try:
        items = _normalize_items(j["response"]["body"]["items"]["item"])
        header = j["response"]["header"]
        return jsonify({"ok": True, "status": header, "count": len(items), "first": items[0] if items else None}), 200
    except Exception:
        return jsonify({"ok": False, "raw": j}), 200

@app.route("/route", methods=["POST"])
def route():
    try:
        data = request.get_json() or {}
        start = geocode_google(data.get("start", ""))
        end   = geocode_google(data.get("end", ""))

        if not start or not end:
            return jsonify({"error": "❌ 주소 변환 실패"}), 400

        # ✅ 경로/경유지: 기존 성공 로직 그대로
        waypoint = find_best_beach_waypoint(start, end)
        if not waypoint:
            return jsonify({"error": "❌ 경유지 탐색 실패"}), 500

        route_data, status = get_ors_route(start, waypoint, end)
        if "error" in route_data:
            return jsonify({"error": route_data["error"]}), status

        # 총 거리/시간
        try:
            summary = route_data["features"][0]["properties"]["summary"]
            eta_sec = summary.get("duration", 0)
            dist_km = round(summary.get("distance", 0) / 1000.0, 1)
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
        items = _normalize_items(j["response"]["body"]["items"]["item"])
        item = items[0]
    except Exception:
        return f"<h2>❌ 관광지 정보가 없습니다.</h2><p>contentid: {contentid}</p>", 404

    return render_template("tour_detail.html", item=item)


if __name__ == "__main__":
    # 로컬 실행용. Render에서는 gunicorn이 이 블록을 사용하지 않습니다.
    port = int(os.environ.get("PORT", 10000))
    log.info(f"Starting on 0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port)
