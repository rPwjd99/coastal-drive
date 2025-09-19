import os, json, math, ssl, logging, time
from urllib.parse import unquote
import requests
from flask import Flask, request, render_template, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context

load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("coastal-drive")

app = Flask(__name__, template_folder="templates", static_folder="static")
CORS(app)

# ---------- ENV ----------
ORS_KEY = os.getenv("ORS_API_KEY", "").strip()
GOOGLE_KEY = os.getenv("GOOGLE_API_KEY", "").strip()  # (옵션) Places 폴백 시 사용
RAW_TOUR_KEY = os.getenv("TOURAPI_KEY", os.getenv("TOUR_API_KEY", "")).strip()
# TourAPI는 'Decoding' 키(=원문) 사용 권장. 들어온게 % 문자가 있으면 디코드
TOUR_KEY = unquote(RAW_TOUR_KEY) if "%" in RAW_TOUR_KEY else RAW_TOUR_KEY

# ---------- TLS 1.2 강제 어댑터 ----------
# 일부 환경에서 apis.data.go.kr TLS 협상이 깨지는 문제 대응
CIPHERS = (
    "ECDHE+AESGCM:ECDHE+CHACHA20:ECDH+AESGCM:"
    "AES256+EECDH:AES256+EDH:AES128+EECDH:AES128+EDH:!aNULL:!MD5:!3DES"
)

class TLS12Adapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        ctx = create_urllib3_context(ciphers=CIPHERS, ssl_version=ssl.PROTOCOL_TLSv1_2)
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, *args, **kwargs):
        ctx = create_urllib3_context(ciphers=CIPHERS, ssl_version=ssl.PROTOCOL_TLSv1_2)
        kwargs["ssl_context"] = ctx
        return super().proxy_manager_for(*args, **kwargs)

sess = requests.Session()
sess.mount("https://", TLS12Adapter())
sess.headers.update({"User-Agent": "SeaRoute/1.0 (+https://render.com)"})

# ---------- 상수 ----------
ORS_URL = "https://api.openrouteservice.org/v2/directions/driving-car"
TOUR_BASES = [
    "https://apis.data.go.kr/B551011/KorService2",
    "https://apis.data.go.kr/B551011/KorService1",
    "http://apis.data.go.kr/B551011/KorService2",  # TLS 실패 시 HTTP 폴백
    "http://apis.data.go.kr/B551011/KorService1",
]
OSM_OVERPASS = "https://overpass-api.de/api/interpreter"

DEFAULT_VIAS = ["남애해수욕장", "낙산해수욕장", "하조대해수욕장"]

# ---------- 유틸 ----------
def haversine_km(lon1, lat1, lon2, lat2):
    R = 6371.0088
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat/2)**2 +
         math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(dlon/2)**2)
    return 2 * R * math.asin(math.sqrt(a))

def douglas_peucker(points, tolerance_km=1.0):
    # points: [(lon,lat), ...]
    if len(points) < 3:
        return points
    # 재귀형 단순 버전
    def perp_dist(p, a, b):
        # 거리 근사(직선거리 대비)
        (x, y), (x1,y1), (x2,y2) = p, a, b
        if (x1, y1) == (x2, y2):
            return haversine_km(x, y, x1, y1)
        # 직선 거리 근사(위경도 직접)
        # 여기선 간단히 해버사인 max로 근사
        d = max(haversine_km(x,y,x1,y1), haversine_km(x,y,x2,y2)) / 2
        return d
    def rdp(pts):
        if len(pts) <= 2:
            return pts
        a, b = pts[0], pts[-1]
        idx, dmax = 0, -1
        for i in range(1, len(pts)-1):
            d = perp_dist(pts[i], a, b)
            if d > dmax:
                idx, dmax = i, d
        if dmax >= tolerance_km:
            left = rdp(pts[:idx+1])
            right = rdp(pts[idx:])
            return left[:-1] + right
        else:
            return [a, b]
    return rdp(points)

def sample_along(points, every_km=7.5):
    if not points:
        return []
    out = [points[0]]
    acc = 0.0
    for i in range(1, len(points)):
        prev = out[-1]
        cur = points[i]
        d = haversine_km(prev[0], prev[1], cur[0], cur[1])
        acc += d
        if acc >= every_km:
            out.append(cur)
            acc = 0.0
    if out[-1] != points[-1]:
        out.append(points[-1])
    return out

# ---------- TourAPI 호출 ----------
def call_tour_location(mapx, mapy, radius):
    if not TOUR_KEY:
        return None, "NO_KEY"
    params = {
        "serviceKey": TOUR_KEY,  # Decoding 키 전달 -> requests가 안전 인코딩
        "MobileOS": "ETC",
        "MobileApp": "SeaRoute",
        "_type": "json",
        "mapX": f"{mapx:.6f}",
        "mapY": f"{mapy:.6f}",
        "radius": int(radius),
        "listYN": "Y",
        "arrange": "E",  # 거리순
        "numOfRows": 60,
        "pageNo": 1,
    }
    last_err = None
    for base in TOUR_BASES:
        url = f"{base}/locationBasedList1"
        try:
            r = sess.get(url, params=params, timeout=6)
            r.raise_for_status()
            data = r.json()
            return data, None
        except requests.exceptions.SSLError as e:
            log.warning("[TourAPI] SSL error on %s: %s", base, repr(e))
            last_err = e
            continue
        except ValueError as e:
            log.warning("[TourAPI] non-JSON: %s (%s)", base, repr(e))
            last_err = e
            continue
        except Exception as e:
            log.warning("[TourAPI] error: %s (%s)", base, repr(e))
            last_err = e
            continue

    # 마지막 카드: 인증서 검증 해제 + HTTP 베이스 재시도(짧게)
    for base in [b.replace("https://", "http://") for b in TOUR_BASES]:
        url = f"{base}/locationBasedList1"
        try:
            r = sess.get(url, params=params, timeout=5, verify=False)
            data = r.json()
            return data, None
        except Exception as e:
            last_err = e
            continue

    return None, f"TourAPI-failed: {last_err}"

def parse_tour_items(raw):
    if not raw:
        return []
    try:
        items = raw["response"]["body"]["items"]["item"]
        if isinstance(items, dict):  # 단건인 경우
            items = [items]
    except Exception:
        return []
    out = []
    for it in items or []:
        try:
            out.append({
                "id": it.get("contentid"),
                "title": it.get("title"),
                "mapx": float(it.get("mapx")),
                "mapy": float(it.get("mapy")),
                "addr1": it.get("addr1"),
                "tel": it.get("tel"),
                "firstimage": it.get("firstimage") or it.get("firstimage2"),
                "cat3": it.get("cat3"),
            })
        except Exception:
            continue
    return out

# ---------- OSM Overpass 폴백 ----------
OVERPASS_QUERY_TMPL = """
[out:json][timeout:25];
(
  node(around:{radius},{lat},{lon})["tourism"];
  node(around:{radius},{lat},{lon})["viewpoint"="yes"];
  node(around:{radius},{lat},{lon})["amenity"="museum"];
  way(around:{radius},{lat},{lon})["tourism"];
  rel(around:{radius},{lat},{lon})["tourism"];
);
out center 20;
"""

def overpass_nearby(lon, lat, radius=3000):
    q = OVERPASS_QUERY_TMPL.format(lon=lon, lat=lat, radius=radius)
    try:
        r = sess.post(OSM_OVERPASS, data={"data": q}, timeout=20)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log.warning("[Overpass] error: %s", e)
        return []
    out = []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        name = tags.get("name") or tags.get("name:ko") or tags.get("name:en")
        if not name:
            continue
        if el.get("type") == "node":
            lon_, lat_ = el["lon"], el["lat"]
        else:
            center = el.get("center")
            if not center:
                continue
            lon_, lat_ = center["lon"], center["lat"]
        out.append({
            "id": f"osm:{el['type']}:{el['id']}",
            "title": name,
            "mapx": lon_,
            "mapy": lat_,
            "addr1": tags.get("addr:full"),
            "tel": None,
            "firstimage": None,
            "cat3": tags.get("tourism") or tags.get("amenity") or tags.get("leisure"),
            "source": "osm",
        })
    return out

# ---------- 코어: 경로, 샘플, POI ----------
def ors_route(coordinates):
    headers = {"Authorization": ORS_KEY, "Content-Type": "application/json"}
    body = {"coordinates": coordinates, "instructions": False}
    r = sess.post(ORS_URL, headers=headers, json=body, timeout=30)
    r.raise_for_status()
    data = r.json()
    coords = data["features"][0]["geometry"]["coordinates"]  # [ [lon,lat], ... ]
    return coords

def collect_pois_along(route_coords, max_calls=12):
    # 경로 간략화 & 샘플링
    simplified = douglas_peucker(route_coords, tolerance_km=2.0)
    samples = sample_along(simplified, every_km=10.0)
    # 호출 수 제한
    if len(samples) > max_calls:
        step = math.ceil(len(samples) / max_calls)
        samples = samples[::step] + ([samples[-1]] if samples[-1] != samples[::step][-1] else [])
    log.info("[TourAPI] samples=%d", len(samples))

    all_pois = []
    tried_tour = False
    for radius in (4000, 8000, 15000):
        batch = []
        for (lon, lat) in samples:
            # TourAPI 우선
            raw, err = call_tour_location(lon, lat, radius)
            tried_tour = True
            if raw:
                items = parse_tour_items(raw)
                batch.extend(items)
            else:
                log.warning("[TourAPI] fail at (%.6f,%.6f,r=%d): %s", lon, lat, radius, err)
        if batch:
            all_pois = batch
            break

    # TourAPI가 전부 실패한 경우 -> OSM 폴백
    if not all_pois:
        osm_batch = []
        for (lon, lat) in samples:
            osm_batch.extend(overpass_nearby(lon, lat, radius=2500))
        all_pois = osm_batch

    # 고유화(제목+좌표 근접)
    uniq = []
    seen = set()
    for p in all_pois:
        key = (p.get("title"), round(p["mapx"], 4), round(p["mapy"], 4))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)

    # 경로거리 기준 정렬 + 상위 N개만
    def min_dist(p):
        # 경로 좌표와 최소 거리(km)
        dmin = 1e9
        for (lon, lat) in route_coords[::max(1, len(route_coords)//120)]:
            d = haversine_km(p["mapx"], p["mapy"], lon, lat)
            if d < dmin: dmin = d
        return dmin
    for p in uniq:
        p["dist_km"] = round(min_dist(p), 2)
    uniq.sort(key=lambda x: x["dist_km"])
    return uniq[:120]

# ---------- 기본 경유지 좌표(보정) ----------
# (lon, lat) — 대략치지만 ORS 경로에는 충분히 정확
BEACHES = {
    "남애해수욕장": (128.8338, 37.9573),
    "낙산해수욕장": (128.6286, 38.1218),
    "하조대해수욕장": (128.7475, 38.0196),
}
# 세종청사 근처, 속초 해수욕장 근처
DEFAULT_START = (127.2893, 36.4797)
DEFAULT_END = (128.6007, 38.2040)

# ---------- 라우트 ----------
@app.route("/")
def home():
    return render_template("index.html")

@app.route("/route", methods=["POST"])
def route():
    try:
        payload = request.get_json(force=True) or {}
    except Exception:
        payload = {}

    start = payload.get("start") or DEFAULT_START  # (lon, lat)
    end = payload.get("end") or DEFAULT_END
    via_names = payload.get("viaNames") or DEFAULT_VIAS

    # 이름 -> 좌표 변환
    vias = []
    for name in via_names:
        if name in BEACHES:
            vias.append(BEACHES[name])
    # 좌표계(ORS: [[lon, lat], ...]) 구성
    coordinates = [list(start)] + [list(v) for v in vias] + [list(end)]

    if not ORS_KEY:
        return jsonify({"ok": False, "error": "ORS_API_KEY missing"}), 500

    # 1) 경로
    coords = ors_route(coordinates)

    # 2) 경로 주변 POI
    pois = collect_pois_along(coords, max_calls=12)
    log.info("[TourAPI] collected total=%d", len(pois))

    return jsonify({
        "ok": True,
        "route": coords,  # [ [lon,lat], ... ]
        "pois": pois,
        "viaUsed": via_names
    })

if __name__ == "__main__":
    # Render의 기본 커맨드 python app.py 기준
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, debug=False)
