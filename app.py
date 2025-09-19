from flask import Flask, request, jsonify, render_template
import os, logging
import requests
from urllib.parse import unquote
from dotenv import load_dotenv
from beaches_coordinates import beach_coords  # { "남애해수욕장": (lon, lat), ... }
from math import radians, cos, sin, asin, sqrt

load_dotenv()
app = Flask(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# 환경변수 & 로거
# ─────────────────────────────────────────────────────────────────────────────
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
ORS_API_KEY    = os.getenv("ORS_API_KEY", "")
# Render에 인코딩 값이 들어있는 경우가 많아 unquote로 'Decoding(원문)'으로 자동 복원
TOURAPI_KEY    = unquote(os.getenv("TOURAPI_KEY", "").strip()) or "e1tU33wjMx2nynKjH8yDBm/S4YNne6B8mpCOWtzMH9TSONF71XG/xAwPqyv1fANpgeOvbPY+Le+gM6cYCnWV8w=="

logger = logging.getLogger("coastal-drive")
logging.basicConfig(level=logging.INFO, format="%(levelname)s:coastal-drive:%(message)s")

# TourAPI 엔드포인트: https 우선, 2 → 1 폴백
TOUR_BASES = [
    "https://apis.data.go.kr/B551011/KorService2",
    "https://apis.data.go.kr/B551011/KorService1",
]

# ─────────────────────────────────────────────────────────────────────────────
# 유틸
# ─────────────────────────────────────────────────────────────────────────────
def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1))*cos(radians(lat2))*sin(dlon/2)**2
    return 2 * R * asin(sqrt(a))

def geocode_google(address):
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {"address": address, "key": GOOGLE_API_KEY, "language": "ko", "region": "kr"}
    r = requests.get(url, params=params, timeout=10)
    try:
        loc = r.json()["results"][0]["geometry"]["location"]
        return float(loc["lat"]), float(loc["lng"])
    except Exception:
        return None

def reverse_geocode_google(lat, lon):
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {"latlng": f"{lat},{lon}", "key": GOOGLE_API_KEY, "language": "ko"}
    r = requests.get(url, params=params, timeout=10)
    try:
        return r.json()["results"][0]["formatted_address"]
    except Exception:
        return "주소 불러오기 실패"

def _tour_call(path, params, timeout=8):
    """KorService2 → 1 폴백, JSON만 반환"""
    base_params = {
        "serviceKey": TOURAPI_KEY,
        "MobileOS": "ETC",
        "MobileApp": "SeaRoute",
        "_type": "json",
    }
    q = {**base_params, **params}
    last_err = None
    for base in TOUR_BASES:
        try:
            r = requests.get(f"{base}/{path}", params=q, timeout=timeout)
            try:
                return r.json()
            except Exception as e:
                last_err = f"non-json {r.status_code}: {str(e)[:80]}"
        except Exception as e:
            last_err = str(e)
    logger.warning("[TourAPI] non-JSON or request error: %s", last_err)
    return {"_error": last_err}

def _as_list(x):
    if not x:
        return []
    return x if isinstance(x, list) else [x]

# ─────────────────────────────────────────────────────────────────────────────
# 경유지(세종→속초: 남애 포함 3개 고정, 그 외엔 자동)
# ─────────────────────────────────────────────────────────────────────────────
def _norm(s): 
    return str(s).replace(" ", "").lower()

def _find_beach_by_keywords(keywords):
    """beaches_coordinates.py의 키에서 부분 일치로 찾는다."""
    keys = list(beach_coords.keys())
    for kw in keywords:
        nkw = _norm(kw)
        for name in keys:
            if nkw in _norm(name):
                lon, lat = beach_coords[name]
                try:
                    return (name, float(lat), float(lon))  # (name, lat, lon)
                except Exception:
                    continue
    return None

def _is_near(lat, lon, lat0, lon0, km):
    return haversine(lat, lon, lat0, lon0) <= km

def _force_waypoints_if_sejong_to_sokcho(start, end):
    """세종(대략 36.5,127.3±30km) → 속초(대략 38.2,128.6±30km)면 남애 포함 3개 경유 고정."""
    s_ok = _is_near(start[0], start[1], 36.48, 127.29, 30)
    e_ok = _is_near(end[0],   end[1],   38.21, 128.59, 30)
    if not (s_ok and e_ok):
        return None

    picks = []
    target_sets = [
        ["남애", "남애해수욕장", "남애해변"],
        ["낙산", "낙산해수욕장"],
        ["하조대", "하조대해수욕장", "주문진", "경포", "속초해수욕장"],
    ]
    for kws in target_sets:
        found = _find_beach_by_keywords(kws)
        if found:
            picks.append(found)

    # 혹시 3개가 안 잡히면 자동 보완은 하지 않고, 잡힌 것만 사용
    if not picks:
        return None

    # 스타트→엔드 방향 순으로 정렬
    s_lat, s_lon = start
    e_lat, e_lon = end
    vx, vy = (e_lon - s_lon), (e_lat - s_lat)
    def t_param(lat, lon):
        wx, wy = (lon - s_lon), (lat - s_lat)
        denom = vx*vx + vy*vy
        return (wx*vx + wy*vy) / denom if denom else 0.0

    picks.sort(key=lambda w: t_param(w[1], w[2]))
    return picks[:3]

# ─────────────────────────────────────────────────────────────────────────────
# ORS 경로
# ─────────────────────────────────────────────────────────────────────────────
def _ors_route(start, waypoints, end):
    if not ORS_API_KEY:
        return {"error": "ORS_API_KEY 미설정"}, 500
    url = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
    headers = {"Authorization": ORS_API_KEY, "Content-Type": "application/json"}
    coords = [[start[1], start[0]]] + [[w[2], w[1]] for w in waypoints] + [[end[1], end[0]]]
    r = requests.post(url, headers=headers, json={"coordinates": coords}, timeout=20)
    try:
        return r.json(), r.status_code
    except Exception as e:
        return {"error": str(e)}, 500

# ─────────────────────────────────────────────────────────────────────────────
# 경로 주변 TourAPI 수집 (반경/샘플 자동 조정)
# ─────────────────────────────────────────────────────────────────────────────
def _sample_points(coords, max_calls=24):
    n = len(coords)
    if n == 0:
        return []
    step = max(1, n // max_calls)
    pts = [coords[i] for i in range(0, n, step)]
    if pts[-1] != coords[-1]:
        pts.append(coords[-1])
    return pts

def _classify(ctype):
    c = str(ctype or "").strip()
    if c == "39":
        return "food"
    if c in {"12","14","25","28"}:
        return "attraction"
    if c == "32":
        return "lodging"
    if c == "38":
        return "shopping"
    return "other"

def search_pois_along_route(geojson):
    coords = geojson["features"][0]["geometry"]["coordinates"]
    samples = _sample_points(coords, max_calls=24)
    logger.info("[TourAPI] samples=%d", len(samples))

    seen, out = set(), []
    # 반경을 늘려가며 시도
    radii = [5000, 10000, 20000]  # TourAPI radius 최대 20000m
    for lon, lat in samples:
        for radius in radii:
            j = _tour_call("locationBasedList1", {
                "mapX": lon, "mapY": lat, "radius": radius,
                "listYN": "Y", "arrange": "E",
                "numOfRows": 20, "pageNo": 1
            })
            if "_error" in j:
                continue
            items = _as_list(j.get("response", {}).get("body", {}).get("items", {}).get("item"))
            got_new = False
            for it in items:
                cid = str(it.get("contentid", "")).strip()
                if not cid or cid in seen:
                    continue
                # 좌표/이미지/주소
                try:
                    mx = float(it.get("mapx"))
                    my = float(it.get("mapy"))
                except Exception:
                    continue
                seen.add(cid)
                out.append({
                    "contentid": cid,
                    "contenttypeid": it.get("contenttypeid"),
                    "category": _classify(it.get("contenttypeid")),
                    "title": it.get("title"),
                    "addr1": it.get("addr1"),
                    "mapx": mx,
                    "mapy": my,
                    "firstimage": it.get("firstimage"),
                    "homepage": it.get("homepage") or "",
                })
                got_new = True
            if got_new:
                break  # 이 샘플 지점에서 항목이 나왔다면 반경 더 늘리지 않음

    logger.info("[TourAPI] collected total=%d", len(out))
    return out

# ─────────────────────────────────────────────────────────────────────────────
# Flask 라우트
# ─────────────────────────────────────────────────────────────────────────────
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

        # 세종→속초일 때만 ‘남애 포함 3경유’ 고정 우선
        waypoints = _force_waypoints_if_sejong_to_sokcho(start, end) or []
        # 그래도 비었으면(혹은 일부만 잡혀도) 현재 picks 그대로 사용 (경로 로직 자체는 그대로)

        route_data, status = _ors_route(start, waypoints, end)
        if status != 200 or "features" not in route_data:
            return jsonify({"error": route_data.get("error", "❌ 경로 계산 실패")}), status

        # ORS 요약 (거리/시간)
        try:
            summary = route_data["features"][0]["properties"]["summary"]
            distance_km = round(float(summary.get("distance", 0))/1000.0, 1)
            duration_min = int(round(float(summary.get("duration", 0))/60.0))
        except Exception:
            distance_km, duration_min = None, None

        # 경유지 주소 역지오코딩
        wp_objs = []
        for name, lat, lon in waypoints:
            wp_objs.append({
                "name": name,
                "lat": lat,
                "lon": lon,
                "address": reverse_geocode_google(lat, lon)
            })

        # POI 수집
        pois = search_pois_along_route(route_data)

        payload = {
            "route": route_data,
            "waypoints": wp_objs,        # 경유 0~3
            "summary": {"distance_km": distance_km, "duration_min": duration_min},
            "spots": pois or []
        }
        # 과거 호환 키(경유 1개만 쓰던 프론트 대비)
        if len(wp_objs) >= 1: payload["waypoint"]  = wp_objs[0]
        if len(wp_objs) >= 2: payload["waypoint2"] = wp_objs[1]
        if len(wp_objs) >= 3: payload["waypoint3"] = wp_objs[2]

        if not pois:
            # 프론트가 안내문을 띄우도록 힌트 메시지 제공(경로/반경은 서버에서 이미 자동 조정됨)
            payload["notice"] = "경로 주변에서 항목을 찾지 못했습니다. (서버가 반경/샘플 수를 자동 조정합니다)"
        return jsonify(payload)
    except Exception as e:
        return jsonify({"error": f"❌ 서버 오류: {str(e)}"}), 500

@app.route("/tour_detail/<contentid>")
def tour_detail(contentid):
    j = _tour_call("detailCommon1", {
        "contentId": contentid,
        "overviewYN": "Y",
        "defaultYN": "Y",
        "firstImageYN": "Y",
        "addrinfoYN": "Y",
        "mapinfoYN": "Y",
    }, timeout=10)
    if "_error" in j:
        return f"<h2>TourAPI 오류</h2><pre>{j['_error']}</pre>", 500
    items = _as_list(j.get("response", {}).get("body", {}).get("items", {}).get("item"))
    if not items:
        return f"<h2>❌ 관광지 정보가 없습니다.</h2><p>contentid: {contentid}</p>", 404
    return render_template("tour_detail.html", item=items[0])

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
