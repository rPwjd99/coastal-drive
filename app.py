# -*- coding: utf-8 -*-
"""
CoastalDrive Flask App (Render-safe)
- Serves index.html robustly at "/": project root -> templates/ fallback -> minimal inline fallback (200)
- NAVER Geocoding & Directions
- TourAPI 관광지/맛집/카페 조회
- Two coastal waypoint selection with robust fallbacks
- Safe error handling so the app won't crash on API failures
"""
import os
import math
from typing import List, Tuple, Optional, Dict, Any

import requests
from flask import Flask, request, jsonify, send_file, Response

# -----------------------------------------------------------------------------
# App setup
# -----------------------------------------------------------------------------
app = Flask(
    __name__,
    static_folder='static',
    template_folder='templates'
)

# ENV keys (user-provided defaults kept for convenience)
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID", "vsdzf1f4n5")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET", "0gzctO51PUTVv0gUZU025JYNHPTmVzLS9sGbfYBM")
TOURAPI_KEY = os.getenv("TOURAPI_KEY", "e1tU33wjMx2nynKjH8yDBm/S4YNne6B8mpCOWtzMH9TSONF71XG/xAwPqyv1fANpgeOvbPY+Le+gM6cYCnWV8w==")

# -----------------------------------------------------------------------------
# Utilities
# -----------------------------------------------------------------------------
def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0088
    from math import radians, sin, cos, asin, sqrt
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2.0) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2.0) ** 2
    c = 2 * asin(sqrt(a))
    return R * c

def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t

def interpolate_point(lat1: float, lon1: float, lat2: float, lon2: float, t: float) -> Tuple[float, float]:
    return (lerp(lat1, lat2, t), lerp(lon1, lon2, t))

def ok_num(v, default=None, cast=float):
    try:
        return cast(v)
    except Exception:
        return default

def naver_headers() -> Dict[str, str]:
    return {
        "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID,
        "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET
    }

# -----------------------------------------------------------------------------
# External API wrappers (robust)
# -----------------------------------------------------------------------------
def geocode_naver(query: str) -> Optional[Dict[str, Any]]:
    if not query:
        return None
    try:
        url = "https://naveropenapi.apigw.ntruss.com/map-geocode/v2/geocode"
        params = {"query": query}
        r = requests.get(url, headers=naver_headers(), params=params, timeout=8)
        if r.status_code != 200:
            return None
        data = r.json()
        addrs = data.get("addresses") or []
        if not addrs:
            return None
        a = addrs[0]
        lon = ok_num(a.get("x"))
        lat = ok_num(a.get("y"))
        if lat is None or lon is None:
            return None
        return {"lat": lat, "lon": lon, "address": a.get("roadAddress") or a.get("jibunAddress") or query}
    except Exception:
        return None

def naver_route_segment(start_lon: float, start_lat: float, goal_lon: float, goal_lat: float) -> Optional[List[List[float]]]:
    try:
        url = "https://naveropenapi.apigw.ntruss.com/map-direction-15/v1/driving"
        params = {
            "start": f"{start_lon},{start_lat}",
            "goal": f"{goal_lon},{goal_lat}",
            "option": "traoptimal"
        }
        r = requests.get(url, headers=naver_headers(), params=params, timeout=12)
        if r.status_code != 200:
            return None
        data = r.json()
        route = (data.get("route") or {})
        for key in ["traoptimal", "trafast", "tracomfort", "traoptimal5"]:
            arr = route.get(key)
            if isinstance(arr, list) and arr:
                path = arr[0].get("path")
                if isinstance(path, list) and path:
                    cleaned = []
                    for p in path:
                        if isinstance(p, list) and len(p) >= 2:
                            plon = ok_num(p[0])
                            plat = ok_num(p[1])
                            if plon is not None and plat is not None:
                                cleaned.append([plon, plat])
                    if len(cleaned) >= 2:
                        return cleaned
        return None
    except Exception:
        return None

def naver_route_full(start: Tuple[float, float], goals: List[Tuple[float, float]]) -> Optional[List[List[float]]]:
    try:
        if not goals:
            return None
        path_all: List[List[float]] = []
        cur_lon, cur_lat = start[1], start[0]
        for (lat, lon) in goals:
            seg = naver_route_segment(cur_lon, cur_lat, lon, lat)
            if not seg:
                return None
            if path_all and path_all[-1] == seg[0]:
                seg = seg[1:]
            path_all.extend(seg)
            cur_lon, cur_lat = lon, lat
        return path_all if len(path_all) >= 2 else None
    except Exception:
        return None

def tourapi_location_based(lat: float, lon: float, radius_m: int, content_type_id: Optional[int] = None, rows: int = 50) -> List[Dict[str, Any]]:
    try:
        base = "https://apis.data.go.kr/B551011/KorService1/locationBasedList1"
        params = {
            "serviceKey": TOURAPI_KEY,
            "MobileOS": "ETC",
            "MobileApp": "CoastalDrive",
            "mapX": lon,
            "mapY": lat,
            "radius": radius_m,
            "listYN": "Y",
            "arrange": "E",
            "pageNo": 1,
            "numOfRows": rows,
            "_type": "json"
        }
        if content_type_id:
            params["contentTypeId"] = content_type_id
        r = requests.get(base, params=params, timeout=10)
        if r.status_code != 200:
            return []
        js = r.json()
        items = (((js.get("response") or {}).get("body") or {}).get("items") or {}).get("item") or []
        if isinstance(items, dict):
            items = [items]
        out = []
        for it in items:
            title = it.get("title") or ""
            addr = it.get("addr1") or ""
            mapx = ok_num(it.get("mapx"))
            mapy = ok_num(it.get("mapy"))
            img = it.get("firstimage") or it.get("firstimage2") or ""
            if mapx is None or mapy is None:
                continue
            out.append({
                "title": title,
                "address": addr,
                "lon": mapx,
                "lat": mapy,
                "image": img
            })
        return out
    except Exception:
        return []

def pick_beach_near(lat: float, lon: float, search_radius_m: int = 30000) -> Optional[Dict[str, Any]]:
    spots = tourapi_location_based(lat, lon, search_radius_m, content_type_id=12, rows=80)
    if not spots:
        return None
    beaches = []
    for s in spots:
        t = s["title"]
        if ("해수욕장" in t) or ("비치" in t) or ("Beach" in t):
            beaches.append(s)
    if not beaches:
        return None
    beaches.sort(key=lambda s: haversine_km(lat, lon, s["lat"], s["lon"]))
    return beaches[0]

def find_accessible_waypoint(origin: Tuple[float, float], candidate: Tuple[float, float]) -> Optional[Tuple[float, float]]:
    o_lat, o_lon = origin
    c_lat, c_lon = candidate
    if naver_route_segment(o_lon, o_lat, c_lon, c_lat):
        return (c_lat, c_lon)
    offsets = [0.01, -0.01, 0.015, -0.015, 0.02, -0.02]
    for dlat in offsets:
        for dlon in offsets:
            lat2 = c_lat + dlat
            lon2 = c_lon + dlon
            if naver_route_segment(o_lon, o_lat, lon2, lat2):
                return (lat2, lon2)
    return None

def select_two_beach_waypoints(o_lat: float, o_lon: float, d_lat: float, d_lon: float) -> List[Dict[str, Any]]:
    waypoints: List[Dict[str, Any]] = []
    for frac in (0.33, 0.66):
        lat_s, lon_s = interpolate_point(o_lat, o_lon, d_lat, d_lon, frac)
        beach = pick_beach_near(lat_s, lon_s, search_radius_m=30000)
        if beach:
            waypoints.append({"name": beach["title"], "lat": beach["lat"], "lon": beach["lon"], "source": "tourapi"})
    deduped = []
    for w in waypoints:
        if all(haversine_km(w["lat"], w["lon"], x["lat"], x["lon"]) > 2.0 for x in deduped):
            deduped.append(w)
    waypoints = deduped[:2]
    if len(waypoints) < 2:
        synthetic_candidates = []
        if abs(d_lat - o_lat) >= abs(d_lon - o_lon):
            synthetic_candidates.append((o_lat, d_lon))
            synthetic_candidates.append((d_lat, o_lon))
        else:
            synthetic_candidates.append((d_lat, o_lon))
            synthetic_candidates.append((o_lat, d_lon))
        origin = (o_lat, o_lon)
        for cand in synthetic_candidates:
            acc = find_accessible_waypoint(origin, cand)
            if acc:
                waypoints.append({"name": "Coastal WP", "lat": acc[0], "lon": acc[1], "source": "synthetic"})
            if len(waypoints) >= 2:
                break
    return waypoints[:2]

# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------
@app.route("/", methods=["GET", "HEAD"])
def root():
    """
    Serve index.html robustly:
    1) <project root>/index.html
    2) <project root>/templates/index.html
    3) minimal inline HTML fallback (200), to avoid Render health checks failing with 404
    """
    try_paths = [
        os.path.join(app.root_path, "index.html"),
        os.path.join(app.root_path, "templates", "index.html"),
    ]
    for p in try_paths:
        if os.path.exists(p):
            return send_file(p, mimetype="text/html; charset=utf-8")
    fallback = """<!doctype html>
<html><head><meta charset="utf-8"><title>CoastalDrive</title></head>
<body><h1>CoastalDrive: index.html not found</h1>
<p>배포는 정상입니다. 저장소 루트 또는 templates/에 index.html이 있는지 확인하세요.</p>
<p>API 헬스체크: <a href="/health">/health</a></p>
</body></html>"""
    return Response(fallback, mimetype="text/html")

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True})

@app.route("/api/geocode", methods=["POST"])
def api_geocode():
    data = request.get_json(silent=True) or {}
    query = (data.get("query") or "").strip()
    res = geocode_naver(query)
    if not res:
        return jsonify({"ok": False, "error": "Geocoding failed"}), 400
    return jsonify({"ok": True, "result": res})

@app.route("/api/route", methods=["POST"])
def api_route():
    body = request.get_json(silent=True) or {}
    origin_q = (body.get("origin") or "").strip()
    dest_q = (body.get("destination") or "").strip()
    if not origin_q or not dest_q:
        return jsonify({"ok": False, "error": "origin/destination required"}), 400

    o = geocode_naver(origin_q)
    d = geocode_naver(dest_q)
    if not o or not d:
        return jsonify({"ok": False, "error": "geocoding failed"}), 400

    o_lat, o_lon = o["lat"], o["lon"]
    d_lat, d_lon = d["lat"], d["lon"]

    waypoints = select_two_beach_waypoints(o_lat, o_lon, d_lat, d_lon)

    goals: List[Tuple[float, float]] = []
    for w in waypoints:
        goals.append((w["lat"], w["lon"]))
    goals.append((d_lat, d_lon))

    path = naver_route_full((o_lat, o_lon), goals)
    if not path:
        fallback = naver_route_segment(o_lon, o_lat, d_lon, d_lat)
        if fallback:
            path = fallback
            waypoints = []
        else:
            return jsonify({"ok": False, "error": "routing failed"}), 502

    feature = {
        "type": "Feature",
        "properties": {
            "name": "CoastalDrive",
            "segments": len(goals)
        },
        "geometry": {
            "type": "LineString",
            "coordinates": path
        }
    }

    return jsonify({
        "ok": True,
        "origin": {"query": origin_q, "lat": o_lat, "lon": o_lon, "address": o["address"]},
        "destination": {"query": dest_q, "lat": d_lat, "lon": d_lon, "address": d["address"]},
        "waypoints": waypoints,
        "geojson": {"type": "FeatureCollection", "features": [feature]}
    })

@app.route("/api/tourspot", methods=["GET"])
def api_tourspot():
    lat = ok_num(request.args.get("lat"), None)
    lon = ok_num(request.args.get("lon"), None)
    radius_km = ok_num(request.args.get("radius_km"), 5, float)
    if lat is None or lon is None:
        return jsonify({"ok": False, "error": "lat/lon required"}), 400
    spots = tourapi_location_based(lat, lon, int(radius_km * 1000), content_type_id=12, rows=80)
    return jsonify({"ok": True, "results": spots})

@app.route("/api/food", methods=["GET"])
def api_food():
    lat = ok_num(request.args.get("lat"), None)
    lon = ok_num(request.args.get("lon"), None)
    radius_km = ok_num(request.args.get("radius_km"), 5, float)
    if lat is None or lon is None:
        return jsonify({"ok": False, "error": "lat/lon required"}), 400
    spots = tourapi_location_based(lat, lon, int(radius_km * 1000), content_type_id=39, rows=120)
    return jsonify({"ok": True, "results": spots})

@app.route("/api/cafe", methods=["GET"])
def api_cafe():
    lat = ok_num(request.args.get("lat"), None)
    lon = ok_num(request.args.get("lon"), None)
    radius_km = ok_num(request.args.get("radius_km"), 5, float)
    if lat is None or lon is None:
        return jsonify({"ok": False, "error": "lat/lon required"}), 400
    items = tourapi_location_based(lat, lon, int(radius_km * 1000), content_type_id=39, rows=120)
    cafes = [s for s in items if "카페" in (s.get("title") or "")]
    return jsonify({"ok": True, "results": cafes})

@app.route("/index.html", methods=["GET", "HEAD"])
def get_index_html():
    # explicit path access, also robust
    for p in [
        os.path.join(app.root_path, "index.html"),
        os.path.join(app.root_path, "templates", "index.html"),
    ]:
        if os.path.exists(p):
            return send_file(p, mimetype="text/html; charset=utf-8")
    return Response("<h1>index.html not found</h1>", mimetype="text/html")

@app.route("/favicon.ico")
def favicon():
    try:
        p = os.path.join(app.root_path, "static", "favicon.ico")
        if os.path.exists(p):
            return send_file(p)
        return ("", 204)
    except Exception:
        return ("", 204)

# -----------------------------------------------------------------------------
# Entrypoint
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, debug=False)
