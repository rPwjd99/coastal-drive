# app.py
# 실행 예:
#   Windows: set PORT=10000 && python app.py
#   macOS/Linux: export PORT=10000 && python app.py
#   Render(권장): gunicorn -w 1 -k gthread --threads 8 --timeout 120 --keep-alive 30 -b 0.0.0.0:$PORT app:app

import os
import math
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from functools import lru_cache
from html import escape
import urllib.parse

import requests
from flask import Flask, request, jsonify, send_from_directory, redirect, Response
from flask_cors import CORS

# .env (선택)
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# beaches_coordinates.py: beach_coords = {"해변명": (lon, lat), ...}
try:
    from beaches_coordinates import beach_coords  # type: ignore
except Exception:
    beach_coords = {}

app = Flask(__name__, static_folder="static")
CORS(app)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("coastal-drive")
APP_DIR = Path(__file__).resolve().parent

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
ORS_API_KEY     = os.getenv("ORS_API_KEY")
TOURAPI_KEY_RAW = os.getenv("TOURAPI_KEY") or os.getenv("TOUR_API_KEY") or ""

# ------------------------ index.html 서빙 ------------------------
def _find_index_html() -> Optional[Path]:
    for p in [APP_DIR / "index.html", APP_DIR / "templates" / "index.html", APP_DIR / "static" / "index.html"]:
        if p.is_file():
            return p
    return None

@app.route("/", methods=["GET", "HEAD"])
def index():
    p = _find_index_html()
    if p:
        return send_from_directory(p.parent.as_posix(), p.name)
    return Response("<!doctype html><meta charset='utf-8'><p>index.html을 같은 폴더에 두세요.</p>", mimetype="text/html")

@app.route("/favicon.ico")
def favicon():
    return "", 204

@app.route("/healthz")
def healthz():
    return jsonify({"ok": True})

# ------------------------ 공통 유틸 ------------------------
def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1); dlon = math.radians(lat2 - lon1)
    a = math.sin(dlat/2.0)**2 + math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(dlon/2.0)**2
    return 2.0 * R * math.asin(math.sqrt(a))

def _coerce_json() -> Dict[str, Any]:
    j = request.get_json(silent=True, force=True)
    if isinstance(j, dict): return j
    if request.form: return {k: request.form.get(k) for k in request.form}
    if request.args: return {k: request.args.get(k) for k in request.args}
    return {}

def _to_float(x: Any) -> Optional[float]:
    try: return float(x)
    except Exception: return None

def _fmt_dist_m(m: float) -> str:
    try: m = float(m)
    except Exception: return ""
    return f"{m/1000:.1f} km" if m >= 1000 else f"{int(round(m))} m"

def _fmt_dur_s(sec: float) -> str:
    try: sec = float(sec)
    except Exception: return ""
    h = int(sec // 3600); m = int(round((sec % 3600) / 60))
    return (f"{h}시간 " if h else "") + f"{m}분"

# ------------------------ 지오코딩 ------------------------
def geocode_google(address: str) -> Optional[Tuple[float, float]]:
    if not GOOGLE_API_KEY or not address: return None
    try:
        r = requests.get("https://maps.googleapis.com/maps/api/geocode/json",
            params={"address": address, "key": GOOGLE_API_KEY}, timeout=8)
        loc = r.json()["results"][0]["geometry"]["location"]
        return float(loc["lat"]), float(loc["lng"])
    except Exception as e:
        log.warning("geocode_google fail: %s", e)
        return None

@lru_cache(maxsize=2048)
def reverse_geocode_google(lat: float, lon: float) -> str:
    if not GOOGLE_API_KEY: return ""
    try:
        r = requests.get("https://maps.googleapis.com/maps/api/geocode/json",
            params={"latlng": f"{lat},{lon}", "key": GOOGLE_API_KEY}, timeout=8)
        return r.json()["results"][0]["formatted_address"]
    except Exception:
        return ""

# ------------------------ 경유지(해변) 자동 선택 ------------------------
def _ll_to_xy_km(lat: float, lon: float, lat0: float, lon0: float) -> Tuple[float, float]:
    x = (lon - lon0) * math.cos(math.radians(lat0)) * 111.32
    y = (lat - lat0) * 110.57
    return x, y

def _projection_metrics(start: Tuple[float, float], end: Tuple[float, float], p: Tuple[float, float]) -> Tuple[float, float]:
    (slat, slon), (elat, elon), (plat, plon) = start, end, p
    lat0 = (slat + elat) / 2.0; lon0 = (slon + elon) / 2.0
    sx, sy = _ll_to_xy_km(slat, slon, lat0, lon0)
    ex, ey = _ll_to_xy_km(elat, elon, lat0, lon0)
    px, py = _ll_to_xy_km(plat, plon, lat0, lon0)
    vx, vy = (ex - sx), (ey - sy); ux, uy = (px - sx), (py - sy)
    denom = vx*vx + vy*vy
    if denom <= 0: return 0.0, float("inf")
    t = (ux*vx + uy*vy) / denom
    cross = abs(vx*uy - vy*ux)
    vnorm = math.sqrt(denom)
    perp_km = cross / vnorm if vnorm > 0 else float("inf")
    return t, perp_km

def _approx_chain_length(points: List[Tuple[float, float]], end: Tuple[float, float]) -> float:
    seq = points + [end]; total = 0.0
    for i in range(len(seq)-1):
        a, b = seq[i], seq[i+1]
        total += haversine(a[0], a[1], b[0], b[1])
    return total

def _max_direct(val: float) -> float:
    return max(val, 1e-6)

def find_waypoints_along_direction(
    start: Tuple[float, float],
    end: Tuple[float, float],
    max_n: int = 3,
    corridor_km: float = 30.0,
    max_abs_detour_km: float = 50.0,
    max_rel_detour: float = 0.35,
) -> List[Tuple[str, float, float, float]]:
    if not beach_coords: return []
    cands: List[Tuple[float, str, float, float]] = []
    for name, (lon, lat) in beach_coords.items():
        t, offset = _projection_metrics(start, end, (lat, lon))
        if 0.0 < t < 1.0 and offset <= corridor_km:
            cands.append((t, name, lat, lon))
    cands.sort(key=lambda x: x[0])
    if not cands: return []
    sel: List[Tuple[str, float, float, float]] = []
    base_direct = haversine(start[0], start[1], end[0], end[1])
    chain_points: List[Tuple[float, float]] = [start]
    for t, name, lat, lon in cands:
        tentative_points = chain_points + [(lat, lon)]
        chain_len = _approx_chain_length(tentative_points, end)
        detour = chain_len - base_direct
        if detour <= max_abs_detour_km and (detour / _max_direct(base_direct)) <= max_rel_detour:
            sel.append((name, lat, lon, t))
            chain_points.append((lat, lon))
            if len(sel) >= max_n: break
    return sel

# ------------------------ ORS 라우팅 ------------------------
def get_ors_route_multi(points: List[Tuple[float, float]]) -> Tuple[Dict[str, Any], int]:
    if not ORS_API_KEY: return {"error": "ORS_API_KEY is missing"}, 500
    coords = [[lon, lat] for (lat, lon) in points]  # ORS는 [lon,lat]
    try:
        r = requests.post(
            "https://api.openrouteservice.org/v2/directions/driving-car/geojson",
            headers={"Authorization": ORS_API_KEY, "Content-Type": "application/json"},
            json={"coordinates": coords},
            timeout=20,
        )
        return r.json(), r.status_code
    except Exception as e:
        return {"error": str(e)}, 500

# ------------------------ TourAPI (코리도 30km + 키 자동호환) ------------------------
BASE_TOUR = "https://apis.data.go.kr/B551011/KorService1"

def _tourapi_key_variants() -> List[Tuple[str, str]]:
    """입력된 키를 raw/decoded/encoded 세 가지 버전으로 시도"""
    raw = (TOURAPI_KEY_RAW or "").strip()
    variants: List[Tuple[str,str]] = []
    seen = set()

    def add(lbl: str, val: str):
        if not val: return
        if val in seen: return
        seen.add(val); variants.append((lbl, val))

    add("raw", raw)
    try:
        dec = urllib.parse.unquote(raw)
        if dec != raw: add("decoded", dec)
    except Exception:
        pass
    try:
        enc = urllib.parse.quote(raw, safe="")
        if enc != raw: add("encoded", enc)
    except Exception:
        pass
    return variants or [("empty", "")]

def _tourapi_request(path: str, params: Dict[str, Any]) -> Tuple[Dict[str, Any], int, str, str]:
    """키 여러 버전으로 순차 시도. (data, status, variant_label, result_code)"""
    last_data, last_status, used_label, rcode = {}, 500, "", ""
    for label, key in _tourapi_key_variants():
        p = dict(params); p["serviceKey"] = key
        try:
            r = requests.get(f"{BASE_TOUR}/{path}", params=p, timeout=10)
            ctype = (r.headers.get("content-type") or "").lower()
            data = r.json() if "application/json" in ctype else {}
            header = (data.get("response", {}) or {}).get("header", {}) or {}
            rcode = str(header.get("resultCode") or "")
            if r.status_code == 200 and (rcode == "0000" or "items" in (data.get("response", {}).get("body", {}) or {})):
                return data, r.status_code, label, rcode or "0000"
            log.warning("TourAPI fail [%s]: HTTP %s, resultCode=%s", label, r.status_code, rcode)
            last_data, last_status, used_label = data, r.status_code, label
        except Exception as e:
            log.warning("TourAPI exception [%s]: %s", label, e)
            last_data, last_status, used_label = {"error": str(e)}, 500, label
    return last_data, last_status, used_label, rcode

def _tourapi_location_based(lon: float, lat: float, content_type_id: int, radius_m: int = 20000, rows: int = 30) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    params = {
        "mapX": lon, "mapY": lat,
        "radius": min(int(radius_m), 20000),  # API 제한
        "listYN": "Y", "arrange": "E",
        "numOfRows": rows, "pageNo": 1,
        "MobileOS": "ETC", "MobileApp": "CoastalDrive",
        "_type": "json", "contentTypeId": content_type_id,
    }
    data, status, vlabel, rcode = _tourapi_request("locationBasedList1", params)
    meta = {"status": status, "variant": vlabel, "resultCode": rcode}
    if status != 200:
        return [], meta
    try:
        items = (data.get("response", {}).get("body", {}).get("items", {}).get("item", []) or [])
        return (items if isinstance(items, list) else [items]), meta
    except Exception:
        return [], meta

@lru_cache(maxsize=4096)
def _tourapi_detail_intro_cached(content_id: str, content_type_id: int) -> Tuple[Dict[str, Any], int]:
    params = {
        "contentId": content_id, "contentTypeId": content_type_id,
        "_type": "json", "MobileOS": "ETC", "MobileApp": "CoastalDrive",
    }
    data, status, _, _ = _tourapi_request("detailIntro1", params)
    return data, status

def _tourapi_detail_intro(content_id: str, content_type_id: int) -> Dict[str, Any]:
    data, status = _tourapi_detail_intro_cached(content_id, int(content_type_id))
    if status != 200: return {}
    try:
        items = (data.get("response", {}).get("body", {}).get("items", {}).get("item", []) or [])
        return items[0] if items else {}
    except Exception:
        return {}

@lru_cache(maxsize=4096)
def _tourapi_detail_common_cached(content_id: str) -> Tuple[Dict[str, Any], int]:
    params = {
        "contentId": content_id,
        "defaultYN": "Y", "overviewYN": "Y",
        "addrinfoYN": "Y", "mapinfoYN": "Y",
        "firstImageYN": "Y", "_type": "json",
        "MobileOS": "ETC", "MobileApp": "CoastalDrive",
    }
    data, status, _, _ = _tourapi_request("detailCommon1", params)
    return data, status

def _tourapi_detail_common(content_id: str) -> Dict[str, Any]:
    data, status = _tourapi_detail_common_cached(content_id)
    if status != 200: return {}
    try:
        items = (data.get("response", {}).get("body", {}).get("items", {}).get("item", []) or [])
        return items[0] if items else {}
    except Exception:
        return {}

def _normalize_detail(item: Dict[str, Any], intro: Dict[str, Any], common: Dict[str, Any],
                      category: str, src_lat: float, src_lon: float, ctype: int) -> Dict[str, Any]:
    mapx = _to_float(item.get("mapx")); mapy = _to_float(item.get("mapy"))
    dist_km = ""
    if mapx is not None and mapy is not None:
        dist_km = f"{haversine(src_lat, src_lon, mapy, mapx):.2f}"

    res = {
        "contentid": str(item.get("contentid") or ""),
        "contenttypeid": int(ctype),
        "title": item.get("title") or "",
        "addr1": item.get("addr1") or "",
        "mapx": mapx if mapx is not None else 0.0,
        "mapy": mapy if mapy is not None else 0.0,
        "firstimage": item.get("firstimage") or common.get("firstimage") or "",
        "tel": item.get("tel") or common.get("tel") or "",
        "homepage": item.get("homepage") or common.get("homepage") or "",
        "category": category,
        "distance_km": dist_km,
        "openhour": "", "restday": "", "parking_info": "",
    }
    if category == "tour":
        res["openhour"] = intro.get("usetime") or ""
        res["restday"]  = intro.get("restdate") or ""
        res["parking_info"] = intro.get("parking") or ""
    else:
        res["openhour"] = intro.get("opentimefood") or ""
        res["restday"]  = intro.get("restdatefood") or ""
        res["parking_info"] = intro.get("parkingfood") or ""
    p = (res["parking_info"] or "").strip()
    res["has_parking"] = bool(p) and ("불가" not in p and "없" not in p)
    return res

def _sample_indices_by_distance(coords: List[List[float]], interval_km: float, max_samples: int = 100) -> List[int]:
    if not coords: return []
    idxs = [0]; accum = 0.0
    last_lon, last_lat = coords[0]; last_pick = 0
    for i in range(1, len(coords)):
        lon, lat = coords[i]
        accum += haversine(last_lat, last_lon, lat, lon)
        last_lon, last_lat = lon, lat
        if (accum >= interval_km and i - last_pick >= 1) or (len(idxs) < 4 and i % max(1, len(coords)//4) == 0):
            idxs.append(i); accum = 0.0; last_pick = i
        if len(idxs) >= max_samples: break
    if idxs[-1] != len(coords) - 1: idxs.append(len(coords) - 1)
    return sorted(set(idxs))

def search_tour_items_along_route(geojson: Dict[str, Any], corridor_km: float = 30.0, limit_each: int = 60) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, Any]]:
    """경로 주변 30km: 반경 20km × 촘촘 샘플링으로 합집합 구성. debug_meta에 호출 결과 기록."""
    try:
        coords = geojson["features"][0]["geometry"]["coordinates"]
    except Exception:
        return {"tour": [], "food": [], "all": []}, {"error": "no_route"}

    radius_m = 20000
    interval_km = 12.0 if corridor_km >= 25.0 else max(8.0, corridor_km * 0.4)
    idxs = _sample_indices_by_distance(coords, interval_km, max_samples=100)

    seen: set = set()
    tours: List[Dict[str, Any]] = []
    foods: List[Dict[str, Any]] = []
    debug_meta = {"tour": [], "food": []}

    # 관광지(12)
    for i in idxs:
        lon, lat = coords[i]
        items, meta = _tourapi_location_based(lon, lat, content_type_id=12, radius_m=radius_m, rows=30)
        debug_meta["tour"].append(meta)
        for it in items:
            cid = str(it.get("contentid") or "")
            if not cid or cid in seen: continue
            seen.add(cid)
            intro  = _tourapi_detail_intro(cid, 12)
            common = _tourapi_detail_common(cid)
            norm = _normalize_detail(it, intro, common, "tour", lat, lon, 12)
            if _to_float(norm.get("mapx")) is None or _to_float(norm.get("mapy")) is None: continue
            tours.append(norm)
            if len(tours) >= limit_each: break
        if len(tours) >= limit_each: break

    # 맛집(39)
    for i in idxs:
        lon, lat = coords[i]
        items, meta = _tourapi_location_based(lon, lat, content_type_id=39, radius_m=radius_m, rows=30)
        debug_meta["food"].append(meta)
        for it in items:
            cid = str(it.get("contentid") or "")
            if not cid or cid in seen: continue
            seen.add(cid)
            intro  = _tourapi_detail_intro(cid, 39)
            common = _tourapi_detail_common(cid)
            norm = _normalize_detail(it, intro, common, "food", lat, lon, 39)
            if _to_float(norm.get("mapx")) is None or _to_float(norm.get("mapy")) is None: continue
            foods.append(norm)
            if len(foods) >= limit_each: break
        if len(foods) >= limit_each: break

    return {"tour": tours, "food": foods, "all": tours + foods}, debug_meta

# ------------------------ 라우팅 핸들러 ------------------------
def _handle_route():
    if not ORS_API_KEY:      return jsonify({"error": "ORS_API_KEY not set"}), 500
    if not TOURAPI_KEY_RAW:  return jsonify({"error": "TOURAPI_KEY not set"}), 500

    data = _coerce_json()
    start_in = data.get("start") or data.get("origin") or data.get("from")
    end_in   = data.get("end") or data.get("destination") or data.get("to")
    max_wps  = int(data.get("max_waypoints") or 3); max_wps  = max(0, min(3, max_wps))

    try: corridor_km = float(data.get("corridor_km") or 30.0)
    except Exception: corridor_km = 30.0
    corridor_km = max(5.0, min(50.0, corridor_km))

    if not start_in or not end_in:
        return jsonify({"error": "start/end 누락"}), 400

    start = geocode_google(start_in) if isinstance(start_in, str) else tuple(start_in) if isinstance(start_in, (list, tuple)) else None
    end   = geocode_google(end_in)   if isinstance(end_in,   str) else tuple(end_in)   if isinstance(end_in,   (list, tuple)) else None
    if not start or not end:
        return jsonify({"error": "주소 변환 실패"}), 400

    # 경유지 자동 선택
    way_sel = find_waypoints_along_direction(start, end, max_n=max_wps)

    # ORS 라우팅
    points = [start] + [(lat, lon) for (_, lat, lon, _) in way_sel] + [end]
    route_data, status = get_ors_route_multi(points)
    if status != 200 or "error" in route_data:
        return jsonify({"error": route_data.get("error", f"OpenRouteService 실패({status})")}), status

    # 경로 요약
    try:
        summary = route_data["features"][0]["properties"]["summary"]
        dist_m  = float(summary.get("distance", 0.0))
        dur_s   = float(summary.get("duration", 0.0))
        route_summary = {
            "distance_m": dist_m, "duration_s": dur_s,
            "distance_text": _fmt_dist_m(dist_m), "duration_text": _fmt_dur_s(dur_s),
        }
    except Exception:
        route_summary = {"distance_m": 0.0, "duration_s": 0.0, "distance_text": "", "duration_text": ""}

    # 경로 주변 TourAPI 수집 (+ 디버그 메타)
    spots, debug_meta = search_tour_items_along_route(route_data, corridor_km=corridor_km, limit_each=int(data.get("limit_each") or 60))
    counts = {"tour": len(spots["tour"]), "food": len(spots["food"]), "all": len(spots["all"])}

    # 경유지 응답
    wp_objs = []
    for i, (name, lat, lon, t) in enumerate(way_sel, start=1):
        wp_objs.append({
            "order": i, "name": name, "lat": lat, "lon": lon, "t": t,
            "address": reverse_geocode_google(lat, lon) or ""
        })

    resp: Dict[str, Any] = {
        "route": route_data,
        "route_summary": route_summary,
        "waypoints_used": wp_objs,
        "spots": spots["all"],              # 팝업용
        "spots_grouped": {"tour": spots["tour"], "food": spots["food"]},
        "spot_counts": counts,
        "corridor_km": corridor_km,
        "tourapi_debug": debug_meta,        # ← 원인 파악용
    }
    if wp_objs:
        resp["waypoint"] = {k: wp_objs[0][k] for k in ("name","lat","lon","address")}

    return jsonify(resp), 200

@app.route("/route", methods=["POST", "GET"])
def route():
    if request.method == "GET": return redirect("/")
    return _handle_route()

@app.route("/api/route", methods=["POST"])
def api_route():
    return _handle_route()

# ------------------------ 상세페이지(간단 HTML) ------------------------
@app.route("/tour_detail/<contentid>")
def tour_detail(contentid: str):
    common = _tourapi_detail_common(contentid) or {}
    try:
        ctype = int(common.get("contenttypeid") or 12)
    except Exception:
        ctype = 12
    intro = _tourapi_detail_intro(contentid, ctype) or {}

    title = escape(common.get("title") or "상세정보")
    addr1 = escape(common.get("addr1") or "")
    tel   = escape(common.get("tel") or "")
    hp    = escape(common.get("homepage") or "")
    img   = escape(common.get("firstimage") or "")
    ovw   = common.get("overview") or ""
    ovw_safe = escape(ovw).replace("\n", "<br>")

    if ctype == 39:
        openhour = intro.get("opentimefood") or ""
        restday  = intro.get("restdatefood") or ""
        park     = intro.get("parkingfood") or ""
    else:
        openhour = intro.get("usetime") or ""
        restday  = intro.get("restdate") or ""
        park     = intro.get("parking") or ""

    html = f"""
<!doctype html><html lang="ko"><meta charset="utf-8"><title>{title}</title>
<style>body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,'Noto Sans KR',sans-serif;margin:24px;line-height:1.6}}
h1{{margin:0 0 8px}} .row{{margin:6px 0}} img{{max-width:640px;height:auto;border-radius:8px}} a{{color:#1565c0}}</style>
<h1>{title}</h1>
<div class="row">{addr1}</div>
<div class="row">{("☎ "+tel) if tel else ""}</div>
<div class="row">{('<a href="'+hp+'" target="_blank" rel="noopener">홈페이지</a>') if hp else ""}</div>
<div class="row">{("운영시간: "+escape(openhour)) if openhour else ""}</div>
<div class="row">{("휴무: "+escape(restday)) if restday else ""}</div>
<div class="row">{("주차: "+escape(park)) if park else ""}</div>
{"<p><img src='"+img+"'></p>" if img else ""}
<hr><div>{ovw_safe}</div>
</html>"""
    return Response(html, mimetype="text/html")

# ------------------------ 디버그/진단 ------------------------
@app.route("/debug/env")
def debug_env():
    return jsonify({
        "ORS_API_KEY": bool(ORS_API_KEY),
        "GOOGLE_API_KEY": bool(GOOGLE_API_KEY),
        "TOURAPI_KEY_present": bool(TOURAPI_KEY_RAW),
        "TOURAPI_key_variants": [lbl for (lbl, _) in _tourapi_key_variants()],
    })

@app.route("/debug/tourapi/ping")
def debug_tourapi_ping():
    mapX, mapY = 126.9780, 37.5665  # 서울시청
    out = {}
    for ctype, label in [(12,"tour"), (39,"food")]:
        items, meta = _tourapi_location_based(mapX, mapY, content_type_id=ctype, radius_m=5000, rows=10)
        out[label] = {"count": len(items), **meta}
    return jsonify({"ok": True, "point": {"mapX": mapX, "mapY": mapY}, "result": out})

@app.route("/debug/tourapi/around")
def debug_tourapi_around():
    try:
        mapX = float(request.args.get("mapX", ""))
        mapY = float(request.args.get("mapY", ""))
    except Exception:
        return jsonify({"error": "mapX/mapY 필요"}), 400
    out = {}
    for ctype, label in [(12,"tour"), (39,"food")]:
        items, meta = _tourapi_location_based(mapX, mapY, content_type_id=ctype, radius_m=20000, rows=30)
        out[label] = {"count": len(items), **meta}
    return jsonify({"ok": True, "point": {"mapX": mapX, "mapY": mapY}, "result": out})

if __name__ == "__main__":
    # Render에서는 PORT를 직접 주입하므로, 로컬이 아니면 PORT 설정을 추가하지 마세요.
    port = int(os.environ.get("PORT", "10000"))
    log.info(f"Starting on 0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port)
