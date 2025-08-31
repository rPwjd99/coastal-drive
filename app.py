# app.py
# Flask 백엔드: ORS 경유지(해수욕장 1~3개) 자동선정 + TourAPI POI 팝업 정보
# 규칙:
# 1) 방향성: 출발→도착 벡터에 대한 선분 투영값 t(0~1) 필터 + t 단조 증가(정렬)
# 2) 라인 근접도: 출발-도착 직선에서 측면 이탈 거리 <= corridor_km(기본 30km)
# 3) 우회비용 제한: 각 경유 추가 시 detour_km > detour_abs_km(기본 50) 또는 detour_ratio > detour_rel(기본 0.35)이면 제외
# 4) 개수 가변: 후보가 3개 이상이면 3개까지, 2개면 2개, 1개면 1개, 0개면 최소 우회 1개 강제(옵션)
# 5) 순서 고정: ORS 요청 순서 = 출발 → 해1 → 해2 → 해3 → 도착
#
# 출력:
# /api/route  -> { route_geojson, waypoints, direct_km, final_km, detour_km, pois_geojson }
#
# 의존성: Flask, flask_cors, requests
# 좌표계: EPSG:4326 (lon, lat)

import os
import math
import json
import time
from typing import List, Dict, Tuple, Any
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# -----------------------------
# 환경변수 및 기본값
# -----------------------------
ORS_API_KEY = os.getenv("ORS_API_KEY", "5b3ce3597851110001cf62486d543846e80049df9c7a9e10ecef2953")
VWORLD_API_KEY = os.getenv("VWORLD_API_KEY", "9E77283D-954A-3077-B7C8-9BD5ADB33255")
TOURAPI_KEY = os.getenv("TOURAPI_KEY", "e1tU33wjMx2nynKjH8yDBm/S4YNne6B8mpCOWtzMH9TSONF71XG/xAwPqyv1fANpgeOvbPY+Le+gM6cYCnWV8w==")

# -----------------------------
# 상수(디폴트). 요청별로 값 바꾸고 싶으면 api_route에서 지역 변수로 덮어써 인자로 전달함
# -----------------------------
DEFAULT_CORRIDOR_KM = 30.0
DEFAULT_DETOUR_ABS_KM = 50.0
DEFAULT_DETOUR_REL = 0.35
DEFAULT_MAX_WAYPOINTS = 3
FORCE_ONE_WAYPOINT = True

SAMPLE_POINTS = 9                 # 선형 샘플링 개수(해수욕장 수집용)
POI_RADIUS_M = 5000               # 경로 주변 POI 탐색 반경(5km)
BEACH_SEARCH_RADIUS_M = 30000     # 해수욕장 키워드 탐색 반경(30km)

# -----------------------------
# 도우미: 지리계산(근사)
# -----------------------------
EARTH_RADIUS_KM = 6371.0088

def deg2rad(d: float) -> float:
    return d * math.pi / 180.0

def lonlat_to_xy_km(lon: float, lat: float, lat_ref: float) -> Tuple[float, float]:
    x = EARTH_RADIUS_KM * deg2rad(lon) * math.cos(deg2rad(lat_ref))
    y = EARTH_RADIUS_KM * deg2rad(lat)
    return x, y

def haversine_km(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    lon1, lat1 = a
    lon2, lat2 = b
    dlon = deg2rad(lon2 - lon1)
    dlat = deg2rad(lat2 - lat1)
    la1 = deg2rad(lat1)
    la2 = deg2rad(lat2)
    h = math.sin(dlat/2)**2 + math.cos(la1) * math.cos(la2) * math.sin(dlon/2)**2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))

def projection_t_and_cross_km(s: Tuple[float, float], d: Tuple[float, float], p: Tuple[float, float]) -> Tuple[float, float]:
    lon_s, lat_s = s
    lon_d, lat_d = d
    lon_p, lat_p = p
    lat_ref = (lat_s + lat_d + lat_p) / 3.0
    sx, sy = lonlat_to_xy_km(lon_s, lat_s, lat_ref)
    dx, dy = lonlat_to_xy_km(lon_d, lat_d, lat_ref)
    px, py = lonlat_to_xy_km(lon_p, lat_p, lat_ref)
    vx, vy = dx - sx, dy - sy
    wx, wy = px - sx, py - sy
    v2 = vx*vx + vy*vy
    t = 0.0 if v2 == 0 else (wx*vx + wy*vy) / v2
    cross = abs(vx*wy - vy*wx)
    vlen = math.sqrt(v2) if v2 > 0 else 1e-9
    dist_perp = cross / vlen
    return t, dist_perp

# -----------------------------
# 외부 API: VWorld 지오코딩
# -----------------------------
def geocode_vworld(addr: str) -> Tuple[float, float]:
    url = "https://api.vworld.kr/req/address"
    params = {
        "service": "address",
        "request": "getCoord",
        "version": "2.0",
        "crs": "EPSG:4326",
        "address": addr,
        "refine": "true",
        "simple": "false",
        "format": "json",
        "type": "ROAD",
        "key": VWORLD_API_KEY
    }
    r = requests.get(url, params=params, timeout=10)
    data = r.json()
    try:
        if data.get("response", {}).get("status") == "OK":
            point = data["response"]["result"]["point"]
            lon = float(point["x"])
            lat = float(point["y"])
            return lon, lat
    except Exception:
        pass
    params["type"] = "PARCEL"
    r = requests.get(url, params=params, timeout=10)
    data = r.json()
    if data.get("response", {}).get("status") == "OK":
        point = data["response"]["result"]["point"]
        lon = float(point["x"])
        lat = float(point["y"])
        return lon, lat
    raise ValueError(f"VWorld geocoding failed for address: {addr}")

# -----------------------------
# 외부 API: TourAPI
# -----------------------------
def tourapi_search_keyword_near(lon: float, lat: float, keyword: str, radius_m: int, num_rows: int = 30) -> List[Dict[str, Any]]:
    base = "https://apis.data.go.kr/B551011/KorService1/searchKeyword1"
    params = {
        "serviceKey": TOURAPI_KEY,
        "MobileOS": "ETC",
        "MobileApp": "CoastalDrive",
        "keyword": keyword,
        "mapX": f"{lon:.6f}",
        "mapY": f"{lat:.6f}",
        "radius": radius_m,
        "listYN": "Y",
        "arrange": "E",
        "numOfRows": num_rows,
        "pageNo": 1,
        "_type": "json"
    }
    r = requests.get(base, params=params, timeout=10)
    try:
        items = r.json().get("response", {}).get("body", {}).get("items", {}).get("item", [])
        return items if isinstance(items, list) else ([items] if items else [])
    except Exception:
        return []

def tourapi_location_based(lon: float, lat: float, radius_m: int, content_type_id: int, num_rows: int = 30) -> List[Dict[str, Any]]:
    base = "https://apis.data.go.kr/B551011/KorService1/locationBasedList1"
    params = {
        "serviceKey": TOURAPI_KEY,
        "MobileOS": "ETC",
        "MobileApp": "CoastalDrive",
        "mapX": f"{lon:.6f}",
        "mapY": f"{lat:.6f}",
        "radius": radius_m,
        "contentTypeId": content_type_id,
        "listYN": "Y",
        "arrange": "E",
        "numOfRows": num_rows,
        "pageNo": 1,
        "_type": "json"
    }
    r = requests.get(base, params=params, timeout=10)
    try:
        items = r.json().get("response", {}).get("body", {}).get("items", {}).get("item", [])
        return items if isinstance(items, list) else ([items] if items else [])
    except Exception:
        return []

def tourapi_detail_intro(content_id: str, content_type_id: str) -> Dict[str, Any]:
    base = "https://apis.data.go.kr/B551011/KorService1/detailIntro1"
    params = {
        "serviceKey": TOURAPI_KEY,
        "MobileOS": "ETC",
        "MobileApp": "CoastalDrive",
        "contentId": content_id,
        "contentTypeId": content_type_id,
        "_type": "json"
    }
    r = requests.get(base, params=params, timeout=10)
    try:
        items = r.json().get("response", {}).get("body", {}).get("items", {}).get("item", [])
        if isinstance(items, list) and items:
            return items[0]
    except Exception:
        pass
    return {}

# -----------------------------
# 외부 API: OpenRouteService
# -----------------------------
def ors_route_distance_and_geojson(coords_lonlat: List[Tuple[float, float]]) -> Tuple[float, Dict[str, Any]]:
    url = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
    headers = {"Authorization": ORS_API_KEY, "Content-Type": "application/json"}
    body = {
        "coordinates": [[lon, lat] for lon, lat in coords_lonlat],
        "instructions": False
    }
    r = requests.post(url, headers=headers, data=json.dumps(body), timeout=20)
    data = r.json()
    total_m = 0.0
    try:
        for feat in data.get("features", []):
            total_m += feat.get("properties", {}).get("summary", {}).get("distance", 0.0)
    except Exception:
        pass
    return total_m / 1000.0, data

# -----------------------------
# 해수욕장 후보 수집 및 필터링
# -----------------------------
def collect_beach_candidates_on_line(
    start: Tuple[float,float],
    end: Tuple[float,float],
    corridor_km: float
) -> List[Dict[str, Any]]:
    s_lon, s_lat = start
    d_lon, d_lat = end
    candidates: Dict[str, Dict[str, Any]] = {}
    for i in range(SAMPLE_POINTS):
        t = (i + 1) / (SAMPLE_POINTS + 1)
        q_lon = s_lon + (d_lon - s_lon) * t
        q_lat = s_lat + (d_lat - s_lat) * t
        items = tourapi_search_keyword_near(q_lon, q_lat, "해수욕장", BEACH_SEARCH_RADIUS_M, num_rows=50)
        for it in items:
            try:
                title = (it.get("title") or "").strip()
                contentid = str(it.get("contentid"))
                mapx = float(it.get("mapx"))
                mapy = float(it.get("mapy"))
                if not contentid or not title:
                    continue
                t_proj, dist_perp = projection_t_and_cross_km(start, end, (mapx, mapy))
                if 0.0 <= t_proj <= 1.0 and dist_perp <= corridor_km:
                    key = contentid
                    prev = candidates.get(key)
                    if (prev is None) or (dist_perp < prev["dist_perp_km"]):
                        candidates[key] = {
                            "contentid": contentid,
                            "title": title,
                            "lon": mapx,
                            "lat": mapy,
                            "t": float(t_proj),
                            "dist_perp_km": float(dist_perp),
                            "contenttypeid": str(it.get("contenttypeid", "")),
                            "firstimage": it.get("firstimage", ""),
                            "addr1": it.get("addr1", ""),
                            "tel": it.get("tel", "")
                        }
            except Exception:
                continue
        time.sleep(0.15)
    lst = list(candidates.values())
    lst.sort(key=lambda x: x["t"])
    return lst

def greedy_pick_waypoints(
    start: Tuple[float,float],
    end: Tuple[float,float],
    candidates: List[Dict[str,Any]],
    direct_km: float,
    detour_abs_km: float,
    detour_rel: float,
    max_waypoints: int
) -> List[Dict[str,Any]]:
    chosen: List[Dict[str,Any]] = []
    cur_coords = [start]
    for cand in candidates:
        trial_coords = cur_coords + [(cand["lon"], cand["lat"])] + [end]
        dist_km, _ = ors_route_distance_and_geojson(trial_coords)
        detour_km = dist_km - direct_km
        detour_ratio = detour_km / max(direct_km, 1e-6)
        if detour_km <= detour_abs_km and detour_ratio <= detour_rel:
            chosen.append(cand)
            cur_coords.insert(len(cur_coords), (cand["lon"], cand["lat"]))
            if len(chosen) >= max_waypoints:
                break
        time.sleep(0.15)
    if not chosen and FORCE_ONE_WAYPOINT and candidates:
        best = None
        best_detour = 1e9
        for cand in candidates:
            trial_coords = [start, (cand["lon"], cand["lat"]), end]
            dist_km, _ = ors_route_distance_and_geojson(trial_coords)
            detour_km = dist_km - direct_km
            if detour_km < best_detour:
                best_detour = detour_km
                best = cand
            time.sleep(0.1)
        if best:
            chosen = [best]
    return chosen

# -----------------------------
# POI 수집 및 팝업 구성
# -----------------------------
def build_popup_html(item: Dict[str,Any], dist_km: float) -> str:
    title = item.get("title", "")
    addr = item.get("addr1", "")
    tel = item.get("tel", "")
    img = item.get("firstimage", "")
    parking = item.get("parking", "") or item.get("parkingfood", "")
    opentime = item.get("opentime", "") or item.get("opentimefood", "")
    lines = []
    lines.append(f"<b>{title}</b>")
    if img:
        lines.append(f'<div style="margin:6px 0"><img src="{img}" alt="{title}" style="width:220px;max-height:140px;object-fit:cover;border-radius:8px;border:1px solid #ddd"/></div>')
    if addr:
        lines.append(f"주소: {addr}")
    if tel:
        lines.append(f"전화: {tel}")
    if opentime:
        lines.append(f"영업시간: {opentime}")
    if parking:
        lines.append(f"주차: {parking}")
    lines.append(f"루트로부터 거리: {dist_km:.1f} km")
    return "<br/>".join(lines)

def nearest_distance_to_route_km(route_coords: List[Tuple[float,float]], pt: Tuple[float,float]) -> float:
    best = 1e9
    step = max(1, len(route_coords)//200)
    for lon, lat in route_coords[::step]:
        d = haversine_km((lon,lat), pt)
        if d < best:
            best = d
    return best

def collect_pois_along_route(route_coords: List[Tuple[float,float]]) -> Dict[str,Any]:
    seen = {}
    features = []
    step = max(1, len(route_coords)//25)
    for i in range(0, len(route_coords), step):
        lon, lat = route_coords[i]
        for ctid in (12, 39):
            items = tourapi_location_based(lon, lat, POI_RADIUS_M, ctid, num_rows=30)
            for it in items:
                try:
                    cid = str(it.get("contentid"))
                    if not cid or cid in seen:
                        continue
                    seen[cid] = True
                    intro = tourapi_detail_intro(cid, str(ctid))
                    it.update(intro)
                    poi_lon = float(it.get("mapx"))
                    poi_lat = float(it.get("mapy"))
                    dist_km = nearest_distance_to_route_km(route_coords, (poi_lon, poi_lat))
                    props = {
                        "contentid": cid,
                        "contenttypeid": str(ctid),
                        "title": it.get("title",""),
                        "addr1": it.get("addr1",""),
                        "tel": it.get("tel",""),
                        "firstimage": it.get("firstimage",""),
                        "parking": it.get("parking","") or it.get("parkingfood",""),
                        "opentime": it.get("opentime","") or it.get("opentimefood",""),
                        "distance_km": dist_km,
                    }
                    props["popup_html"] = build_popup_html(props, dist_km)
                    feat = {
                        "type": "Feature",
                        "geometry": {"type":"Point", "coordinates":[poi_lon, poi_lat]},
                        "properties": props
                    }
                    features.append(feat)
                except Exception:
                    continue
            time.sleep(0.12)
    pois_geojson = {"type":"FeatureCollection", "features": features}
    return pois_geojson

# -----------------------------
# 메인 엔드포인트
# -----------------------------
@app.route("/api/route", methods=["POST"])
def api_route():
    data = request.get_json(force=True)
    origin = data.get("origin")
    destination = data.get("destination")

    # 요청별 파라미터를 지역변수로만 사용
    corridor_km = float(data.get("corridor_km", DEFAULT_CORRIDOR_KM))
    detour_abs_km = float(data.get("detour_abs_km", DEFAULT_DETOUR_ABS_KM))
    detour_rel = float(data.get("detour_rel", DEFAULT_DETOUR_REL))
    max_waypoints = int(data.get("max_waypoints", DEFAULT_MAX_WAYPOINTS))
    max_waypoints = max(1, min(3, max_waypoints))

    # 출발/도착 좌표 파싱
    def parse_point(obj):
        if isinstance(obj, dict) and "lon" in obj and "lat" in obj:
            return float(obj["lon"]), float(obj["lat"])
        elif isinstance(obj, str):
            return geocode_vworld(obj)
        else:
            raise ValueError("origin/destination must be address string or {'lon':..,'lat':..}")

    try:
        s_lon, s_lat = parse_point(origin)
        d_lon, d_lat = parse_point(destination)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Geocoding/Parsing failed: {e}"}), 400

    start = (s_lon, s_lat)
    end = (d_lon, d_lat)

    # 직행 경로 거리
    try:
        direct_km, direct_geo = ors_route_distance_and_geojson([start, end])
    except Exception as e:
        return jsonify({"ok": False, "error": f"ORS direct route failed: {e}"}), 502

    # 해수욕장 후보 수집 및 t/코리도 필터
    try:
        candidates = collect_beach_candidates_on_line(start, end, corridor_km)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Collect beaches failed: {e}"}), 502

    # 그리디로 1~3개 선정(우회비용 제한)
    try:
        chosen = greedy_pick_waypoints(
            start, end, candidates, direct_km,
            detour_abs_km, detour_rel, max_waypoints
        )
    except Exception as e:
        return jsonify({"ok": False, "error": f"Greedy selection failed: {e}"}), 502

    # 최종 경로 계산(출발 → 해1 → 해2 → 해3 → 도착)
    coords = [start] + [(c["lon"], c["lat"]) for c in chosen] + [end]
    final_km, route_geo = ors_route_distance_and_geojson(coords)
    detour_km = final_km - direct_km

    # Route 좌표 배열 추출(POI 거리 계산용)
    route_coords = []
    try:
        for feat in route_geo.get("features", []):
            if feat.get("geometry", {}).get("type") == "LineString":
                route_coords.extend(feat["geometry"]["coordinates"])
    except Exception:
        pass
    route_coords = [(p[0], p[1]) for p in route_coords]

    # 경로 주변 POI 수집(관광지/맛집)
    pois_geo = collect_pois_along_route(route_coords)

    # 경유지 포인트 Feature
    wp_features = []
    for idx, c in enumerate(chosen, start=1):
        wp_features.append({
            "type": "Feature",
            "geometry": {"type":"Point", "coordinates":[c["lon"], c["lat"]]},
            "properties": {
                "kind": "beach_waypoint",
                "order": idx,
                "title": c["title"],
                "t": c["t"],
                "perp_km": c["dist_perp_km"],
                "contentid": c["contentid"],
                "addr1": c.get("addr1",""),
                "tel": c.get("tel",""),
                "firstimage": c.get("firstimage","")
            }
        })

    waypoints_geojson = {"type":"FeatureCollection", "features": wp_features}

    # 파일 저장(선택)
    try:
        with open("coastal_route_result.geojson", "w", encoding="utf-8") as f:
            json.dump(route_geo, f, ensure_ascii=False)
        with open("pois_result.geojson", "w", encoding="utf-8") as f:
            json.dump(pois_geo, f, ensure_ascii=False)
    except Exception:
        pass

    return jsonify({
        "ok": True,
        "summary": {
            "direct_km": round(direct_km, 3),
            "final_km": round(final_km, 3),
            "detour_km": round(detour_km, 3),
            "waypoints_count": len(chosen),
            "corridor_km": corridor_km,
            "detour_abs_km": detour_abs_km,
            "detour_rel": detour_rel
        },
        "route_geojson": route_geo,
        "waypoints_geojson": waypoints_geojson,
        "pois_geojson": pois_geo
    })

@app.route("/")
def health():
    return "CoastalDrive ORS+TourAPI backend OK"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
