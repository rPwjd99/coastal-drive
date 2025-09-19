from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from dotenv import load_dotenv
import os, math, logging, requests

load_dotenv()

app = Flask(__name__, template_folder="templates", static_folder="static")
CORS(app)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("coastal-drive")

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
ORS_API_KEY     = os.getenv("ORS_API_KEY")
TOURAPI_KEY     = os.getenv("TOURAPI_KEY")  # 공공데이터포털 'Decoding' 키 그대로
# 기본 KorService2, 실패 시 KorService1 폴백
TOUR_BASES = [
    "https://apis.data.go.kr/B551011/KorService2",
    "https://apis.data.go.kr/B551011/KorService1",
]

# 해안 경유지 후보
try:
    from beaches_coordinates import beach_coords  # {이름: (lon, lat)}
except Exception:
    beach_coords = {}

# -------------------- 유틸 --------------------
def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(dlon/2)**2
    return 2*R*math.asin(math.sqrt(a))

def _to_float(x):
    try:
        return float(x)
    except Exception:
        return None

# -------------------- 지오코딩 --------------------
def geocode_google(address):
    if not GOOGLE_API_KEY or not address:
        return None
    try:
        r = requests.get(
            "https://maps.googleapis.com/maps/api/geocode/json",
            params={"address": address, "key": GOOGLE_API_KEY},
            timeout=10,
        )
        j = r.json()
        loc = j["results"][0]["geometry"]["location"]
        return float(loc["lat"]), float(loc["lng"])
    except Exception as e:
        log.warning("geocode_google failed: %s", e)
        return None

def reverse_geocode_google(lat, lon):
    if not GOOGLE_API_KEY:
        return ""
    try:
        r = requests.get(
            "https://maps.googleapis.com/maps/api/geocode/json",
            params={"latlng": f"{lat},{lon}", "key": GOOGLE_API_KEY},
            timeout=10,
        )
        j = r.json()
        return j["results"][0]["formatted_address"]
    except Exception:
        return ""

# -------------------- 경유지 --------------------
def is_in_coastal_bounds(lat, lon):
    return (
        (35 <= lat <= 38 and 128 <= lon <= 131) or
        (33 <= lat <= 35 and 126 <= lon <= 129) or
        (34 <= lat <= 38 and 124 <= lon <= 126)
    )

def find_best_beach_waypoint(start, end):
    start_lat, start_lon = start
    end_lat, end_lon     = end
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
    if best_lat and best_lon:
        return (best_lat if best_lat[3] <= best_lon[3] else best_lon)[:3]
    return (best_lat or best_lon)[:3] if (best_lat or best_lon) else None

# -------------------- ORS 경로 --------------------
def get_ors_route(start, waypoint, end):
    if not ORS_API_KEY:
        return {"error": "ORS_API_KEY is missing"}, 500
    url = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
    headers = {"Authorization": ORS_API_KEY, "Content-Type": "application/json"}
    body = {
        "coordinates": [
            [start[1], start[0]],
            [waypoint[2], waypoint[1]],
            [end[1], end[0]],
        ]
    }
    try:
        r = requests.post(url, headers=headers, json=body, timeout=30)
        return r.json(), r.status_code
    except Exception as e:
        return {"error": str(e)}, 500

# -------------------- TourAPI --------------------
def _tour_location_once(base_url, lon, lat, content_type_id=None, radius_m=20000, num_rows=20):
    if not TOURAPI_KEY:
        return [], ("9999", "TOURAPI_KEY missing")
    url = base_url.rstrip("/") + "/locationBasedList1"
    params = {
        "serviceKey": TOURAPI_KEY,
        "mapX": lon,
        "mapY": lat,
        "radius": max(1000, min(20000, int(radius_m))),
        "listYN": "Y",
        "arrange": "E",
        "numOfRows": max(1, min(50, int(num_rows))),
        "pageNo": 1,
        "MobileOS": "ETC",
        "MobileApp": "CoastalDrive",
        "_type": "json",
    }
    if content_type_id:
        params["contentTypeId"] = int(content_type_id)

    try:
        r = requests.get(url, params=params, headers={"Accept": "application/json"}, timeout=10)
        j = r.json()
    except Exception:
        # XML 오류 등 텍스트 일부 로깅
        txt = (r.text if 'r' in locals() else '')[:200].replace("\n", " ")
        log.warning("[TourAPI] non-JSON or request error: %s ...", txt)
        return [], ("9000", "non-JSON")

    header = j.get("response", {}).get("header", {})
    code   = header.get("resultCode", "")
    msg    = header.get("resultMsg", "")
    if code and code != "0000":
        log.warning("[TourAPI] resultCode=%s, resultMsg=%s", code, msg)
    items = j.get("response", {}).get("body", {}).get("items", {}).get("item", []) or []
    if isinstance(items, dict):
        items = [items]
    return items, (code or "0000", msg or "OK")

def _tour_location(lon, lat, content_type_id=None, radius_m=20000, num_rows=20):
    # 1) KorService2
    items, stat = _tour_location_once(TOUR_BASES[0], lon, lat, content_type_id, radius_m, num_rows)
    if items: return items
    # 2) KorService1
    items, _   = _tour_location_once(TOUR_BASES[1], lon, lat, content_type_id, radius_m, num_rows)
    if items: return items
    # 3) contentType 미지정 느슨 재조회
    items, _   = _tour_location_once(TOUR_BASES[1], lon, lat, None, radius_m, num_rows)
    return items

def _polyline_samples(coords, interval_km=9.0, max_samples=50):
    if not coords: return []
    out = []
    last_lon, last_lat = coords[0]
    out.append((last_lon, last_lat))
    acc = 0.0
    for i in range(1, len(coords)):
        lon, lat = coords[i]
        acc += haversine(last_lat, last_lon, lat, lon)
        if acc >= interval_km:
            out.append((lon, lat)); acc = 0.0
            if len(out) >= max_samples: break
        last_lon, last_lat = lon, lat
    if out[-1] != (coords[-1][0], coords[-1][1]):
        out.append((coords[-1][0], coords[-1][1]))
    return out

def _normalize_item(raw, anchor_lat, anchor_lon, category):
    lon = _to_float(raw.get("mapx")); lat = _to_float(raw.get("mapy"))
    if lon is None or lat is None: return None
    return {
        "contentid": str(raw.get("contentid") or ""),
        "title": raw.get("title") or "",
        "addr1": raw.get("addr1") or "",
        "mapx": lon,
        "mapy": lat,
        "firstimage": raw.get("firstimage") or "",
        "tel": raw.get("tel") or "",
        "homepage": raw.get("homepage") or "",
        "readcount": raw.get("readcount") or "",
        "distance": round(haversine(anchor_lat, anchor_lon, lat, lon), 2),
        "category": category,
    }

def search_along_route(geojson):
    try:
        coords = geojson["features"][0]["geometry"]["coordinates"]
    except Exception:
        return {"spots": [], "restaurants": []}

    samples = _polyline_samples(coords, interval_km=9.0, max_samples=50)
    log.info("[TourAPI] samples=%d", len(samples))

    seen = set()
    tours, foods = [], []

    # 관광지(12)
    for lon, lat in samples:
        for raw in _tour_location(lon, lat, content_type_id=12, radius_m=20000, num_rows=30):
            cid = str(raw.get("contentid") or "")
            if not cid or cid in seen: continue
            seen.add(cid)
            it = _normalize_item(raw, lat, lon, "tour")
            if it: tours.append(it)
        if len(tours) >= 80: break

    # 음식(39)
    for lon, lat in samples:
        for raw in _tour_location(lon, lat, content_type_id=39, radius_m=20000, num_rows=30):
            cid = str(raw.get("contentid") or "")
            if not cid or cid in seen: continue
            seen.add(cid)
            it = _normalize_item(raw, lat, lon, "food")
            if it: foods.append(it)
        if len(foods) >= 80: break

    log.info("[TourAPI] collected tour=%d food=%d total=%d", len(tours), len(foods), len(tours)+len(foods))
    return {"spots": tours + foods, "restaurants": foods}

# -------------------- 라우트 --------------------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/route", methods=["POST"])
def route():
    try:
        data = request.get_json(force=True, silent=True) or {}
        start_s = (data.get("start") or "").strip()
        end_s   = (data.get("end") or "").strip()
        if not start_s or not end_s:
            return jsonify({"error": "출발지/도착지를 입력하세요."}), 400

        start = geocode_google(start_s)
        end   = geocode_google(end_s)
        if not start or not end:
            return jsonify({"error": "❌ 주소 변환 실패"}), 400

        waypoint = find_best_beach_waypoint(start, end)
        if not waypoint:
            return jsonify({"error": "❌ 경유지 탐색 실패"}), 500

        route_obj, status = get_ors_route(start, waypoint, end)
        if status != 200 or "error" in route_obj:
            return jsonify({"error": route_obj.get("error", f"OpenRouteService 오류({status})")}), status

        # 경로 주변 수집
        around = search_along_route(route_obj)

        return jsonify({
            "route": route_obj,
            "waypoint": { "name": waypoint[0], "lat": waypoint[1], "lon": waypoint[2],
                          "address": reverse_geocode_google(waypoint[1], waypoint[2]) },
            "spots": around["spots"],
            "restaurants": around["restaurants"],
        }), 200

    except Exception as e:
        log.exception("route error")
        return jsonify({"error": f"❌ 서버 오류: {str(e)}"}), 500

@app.route("/tour_detail/<contentid>")
def tour_detail(contentid):
    url = "https://apis.data.go.kr/B551011/KorService1/detailCommon1"
    params = {
        "serviceKey": TOURAPI_KEY,
        "MobileOS": "ETC",
        "MobileApp": "CoastalDrive",
        "contentId": contentid,
        "overviewYN": "Y",
        "defaultYN": "Y",
        "firstImageYN": "Y",
        "addrinfoYN": "Y",
        "mapinfoYN": "Y",
        "_type": "json",
    }
    try:
        r = requests.get(url, params=params, headers={"Accept": "application/json"}, timeout=10)
        j = r.json()
        items = j.get("response", {}).get("body", {}).get("items", {}).get("item", [])
        item = items[0] if isinstance(items, list) and items else {}
        return render_template("tour_detail.html", item=item)
    except Exception as e:
        return f"<h2>TourAPI 상세 호출 오류</h2><p>{str(e)}</p>", 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
