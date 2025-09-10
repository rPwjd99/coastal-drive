# -*- coding: utf-8 -*-
"""
CoastalDrive Flask App
- Renders project-root index.html at "/"
- NAVER Geocoding & Directions
- TourAPI 관광지/맛집/카페 조회
- Two coastal waypoint selection with robust fallbacks
- Safe error handling so the app won't crash on API failures
"""
import os
import math
import json
from typing import List, Tuple, Optional, Dict, Any
from urllib.parse import urlencode
import requests

from flask import Flask, request, jsonify, send_from_directory

# -----------------------------------------------------------------------------
# App setup
# -----------------------------------------------------------------------------
# Keep user's preferred structure: index.html at project root
app = Flask(
    __name__,
    static_folder='static',
    template_folder='templates'
)

# Read keys from environment with safe fallbacks (user-provided keys)
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID", "vsdzf1f4n5")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET", "0gzctO51PUTVv0gUZU025JYNHPTmVzLS9sGbfYBM")
TOURAPI_KEY = os.getenv("TOURAPI_KEY", "e1tU33wjMx2nynKjH8yDBm/S4YNne6B8mpCOWtzMH9TSONF71XG/xAwPqyv1fANpgeOvbPY+Le+gM6cYCnWV8w==")

# -----------------------------------------------------------------------------
# Utilities
# -----------------------------------------------------------------------------
def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0088
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2.0) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2.0) ** 2
    c = 2 * math.asin(math.sqrt(a))
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
# External API wrappers (robust, defensive)
# -----------------------------------------------------------------------------
def geocode_naver(query: str) -> Optional[Dict[str, Any]]:
    """
    NAVER Geocoding API
    Returns: { 'lat': float, 'lon': float, 'address': str } or None
    """
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
    """
    NAVER Directions (one segment). Returns list of [lon, lat] or None.
    """
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
        # Try typical keys in order
        for key in ["traoptimal", "trafast", "tracomfort", "traoptimal5"]:
            arr = route.get(key)
            if isinstance(arr, list) and arr:
                path = arr[0].get("path")
                if isinstance(path, list) and path:
                    # path: [[lon, lat], ...]
                    # Sanity check on coordinates
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
    """
    Build route by chaining segments (start -> g1 -> g2 -> ...).
    Returns combined path or None.
    """
    try:
        if not goals:
            return None
        path_all: List[List[float]] = []
        cur_lon, cur_lat = start[1], start[0]
        for (lat, lon) in goals:
            seg = naver_route_segment(cur_lon, cur_lat, lon, lat)
            if not seg:
                return None
            if path_all:
                # avoid duplicate point at stitching
                if path_all[-1] == seg[0]:
                    seg = seg[1:]
            path_all.extend(seg)
            cur_lon, cur_lat = lon, lat
        return path_all if len(path_all) >= 2 else None
    except Exception:
        return None

def tourapi_location_based(lat: float, lon: float, radius_m: int, content_type_id: Optional[int] = None, rows: int = 50) -> List[Dict[str, Any]]:
    """
    TourAPI locationBasedList (JSON). Returns list of simplified items.
    content_type_id: 12(관광지), 39(음식), etc. If None, returns mixed.
    """
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
    """
    Pick nearest "해수욕장"/"비치" around given point using TourAPI.
    """
    spots = tourapi_location_based(lat, lon, search_radius_m, content_type_id=12, rows=80)
    if not spots:
        return None
    # keep only those that look like beaches
    beaches = []
    for s in spots:
        t = s["title"]
        if ("해수욕장" in t) or ("비치" in t) or ("Beach" in t):
            beaches.append(s)
    if not beaches:
        return None
    # nearest by Haversine
    beaches.sort(key=lambda s: haversine_km(lat, lon, s["lat"], s["lon"]))
    return beaches[0]

def find_accessible_waypoint(origin: Tuple[float, float], candidate: Tuple[float, float]) -> Optional[Tuple[float, float]]:
    """
    Validates road connectivity to candidate from origin using NAVER directions.
    Falls back to nearby offsets if needed.
    """
    o_lat, o_lon = origin
    c_lat, c_lon = candidate

    # First try the candidate itself
    if naver_route_segment(o_lon, o_lat, c_lon, c_lat):
        return (c_lat, c_lon)

    # Nearby offsets (approx <= ~2km). Try 8-neighbors then 16 neighbors.
    offsets = [0.01, -0.01, 0.015, -0.015, 0.02, -0.02]
    for dlat in offsets:
        for dlon in offsets:
            lat2 = c_lat + dlat
            lon2 = c_lon + dlon
            if naver_route_segment(o_lon, o_lat, lon2, lat2):
                return (lat2, lon2)
    return None

def select_two_beach_waypoints(o_lat: float, o_lon: float, d_lat: float, d_lon: float) -> List[Dict[str, Any]]:
    """
    Strategy:
    1) Sample at 1/3 and 2/3 along O->D. For each sample, look for beaches via TourAPI.
    2) If none found, create lat/lon-aligned synthetic candidates and validate with NAVER directions.
    """
    waypoints: List[Dict[str, Any]] = []

    # 1) Try TourAPI beaches near 1/3 and 2/3 points
    for frac in (0.33, 0.66):
        lat_s, lon_s = interpolate_point(o_lat, o_lon, d_lat, d_lon, frac)
        beach = pick_beach_near(lat_s, lon_s, search_radius_m=30000)
        if beach:
            waypoints.append({"name": beach["title"], "lat": beach["lat"], "lon": beach["lon"], "source": "tourapi"})
    # Deduplicate close beaches
    deduped = []
    for w in waypoints:
        if all(haversine_km(w["lat"], w["lon"], x["lat"], x["lon"]) > 2.0 for x in deduped):
            deduped.append(w)
    waypoints = deduped[:2]

    # 2) Fallback if < 2 waypoints: build synthetic candidates and validate
    if len(waypoints) < 2:
        synthetic_candidates = []
        # align by latitude or longitude depending on greater axis difference
        if abs(d_lat - o_lat) >= abs(d_lon - o_lon):
            # more N-S movement: fix lon near coastline guess by combining
            synthetic_candidates.append((o_lat, d_lon))
            synthetic_candidates.append((d_lat, o_lon))
        else:
            # more E-W movement
            synthetic_candidates.append((d_lat, o_lon))
            synthetic_candidates.append((o_lat, d_lon))
        # Validate/snap to accessible roads
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
    # Serve index.html from project root to honor user's project layout
    return send_from_directory(".", "index.html")

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
    """
    Body: { "origin": "세종특별자치시청", "destination": "속초시청" }
    Returns: GeoJSON + points
    """
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

    # Select two coastal waypoints (TourAPI-first, synthetic fallback)
    waypoints = select_two_beach_waypoints(o_lat, o_lon, d_lat, d_lon)

    # Build route by chaining segments: origin -> wp1 -> wp2 -> dest
    goals: List[Tuple[float, float]] = []
    for w in waypoints:
        goals.append((w["lat"], w["lon"]))
    goals.append((d_lat, d_lon))

    path = naver_route_full((o_lat, o_lon), goals)
    if not path:
        # last resort: try direct origin->dest so user still sees a route
        fallback = naver_route_segment(o_lon, o_lat, d_lon, d_lat)
        if fallback:
            path = fallback
            waypoints = []  # indicate no usable waypoints
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
            "coordinates": path  # [lon, lat]
        }
    }

    return jsonify({
        "ok": True,
        "origin": {"query": origin_q, "lat": o_lat, "lon": o_lon, "address": o["address"]},
        "destination": {"query": dest_q, "lat": d_lat, "lon": d_lon, "address": d["address"]},
        "waypoints": waypoints,
        "geojson": {
            "type": "FeatureCollection",
            "features": [feature]
        }
    })

@app.route("/api/tourspot", methods=["GET"])
def api_tourspot():
    """
    Query: lat, lon, radius_km (default 5)
    Returns nearby 관광지(contentTypeId=12)
    """
    lat = ok_num(request.args.get("lat"), None)
    lon = ok_num(request.args.get("lon"), None)
    radius_km = ok_num(request.args.get("radius_km"), 5, float)
    if lat is None or lon is None:
        return jsonify({"ok": False, "error": "lat/lon required"}), 400
    spots = tourapi_location_based(lat, lon, int(radius_km * 1000), content_type_id=12, rows=80)
    return jsonify({"ok": True, "results": spots})

@app.route("/api/food", methods=["GET"])
def api_food():
    """
    Query: lat, lon, radius_km (default 5)
    Returns 음식점(contentTypeId=39)
    """
    lat = ok_num(request.args.get("lat"), None)
    lon = ok_num(request.args.get("lon"), None)
    radius_km = ok_num(request.args.get("radius_km"), 5, float)
    if lat is None or lon is None:
        return jsonify({"ok": False, "error": "lat/lon required"}), 400
    spots = tourapi_location_based(lat, lon, int(radius_km * 1000), content_type_id=39, rows=120)
    return jsonify({"ok": True, "results": spots})

@app.route("/api/cafe", methods=["GET"])
def api_cafe():
    """
    Query: lat, lon, radius_km (default 5)
    Returns subset of 음식점 that look like cafes (title contains '카페')
    """
    lat = ok_num(request.args.get("lat"), None)
    lon = ok_num(request.args.get("lon"), None)
    radius_km = ok_num(request.args.get("radius_km"), 5, float)
    if lat is None or lon is None:
        return jsonify({"ok": False, "error": "lat/lon required"}), 400
    items = tourapi_location_based(lat, lon, int(radius_km * 1000), content_type_id=39, rows=120)
    cafes = [s for s in items if "카페" in (s.get("title") or "")]
    return jsonify({"ok": True, "results": cafes})

# Serve index.html directly if requested explicitly
@app.route("/index.html", methods=["GET"])
def get_index_html():
    return send_from_directory(".", "index.html")

# Optional: serve simple favicon if present
@app.route("/favicon.ico")
def favicon():
    # If missing, ignore gracefully
    try:
        return send_from_directory("static", "favicon.ico")
    except Exception:
        return ("", 204)

# -----------------------------------------------------------------------------
# Entrypoint
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    # Bind to PORT for Render; default local 10000 (as in logs)
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, debug=False)
