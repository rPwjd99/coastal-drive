# app.py
# 실행:
#  Windows: set PORT=10000 && python app.py
#  macOS/Linux: export PORT=10000 && python app.py
#  Render:
#    gunicorn -w 1 -k gthread --threads 8 --timeout 120 --keep-alive 30 -b 0.0.0.0:$PORT app:app

import os, math, logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from functools import lru_cache
from html import escape
from urllib.parse import quote, unquote

import requests
from flask import Flask, request, jsonify, send_from_directory, redirect, Response
from flask_cors import CORS

# --- .env (선택) ---
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
TOURAPI_KEY_RAW = os.getenv("TOURAPI_KEY") or os.getenv("TOUR_API_KEY")

# ---------------- index.html 서빙 ----------------
def _find_index_html() -> Optional[Path]:
    for p in [APP_DIR/"index.html", APP_DIR/"templates"/"index.html", APP_DIR/"static"/"index.html"]:
        if p.is_file(): return p
    return None

@app.route("/", methods=["GET","HEAD"])
def index():
    p = _find_index_html()
    if p: return send_from_directory(p.parent.as_posix(), p.name)
    return Response("<!doctype html><meta charset='utf-8'><p>index.html을 같은 폴더에 두세요.</p>", mimetype="text/html")

@app.route("/favicon.ico")
def favicon(): return "", 204

@app.route("/healthz")
def healthz(): return jsonify({"ok": True})

# ---------------- 공통 유틸 ----------------
def haversine(lat1, lon1, lat2, lon2) -> float:
    R=6371.0
    dlat=math.radians(lat2-lat1); dlon=math.radians(lon2-lon1)
    a=math.sin(dlat/2)**2 + math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(dlon/2)**2
    return 2*R*math.asin(math.sqrt(a))

def _to_float(x: Any) -> Optional[float]:
    try: return float(x)
    except Exception: return None

def _fmt_dist_m(m):
    try: m=float(m)
    except Exception: return ""
    return f"{m/1000:.1f} km" if m>=1000 else f"{int(round(m))} m"

def _fmt_dur_s(sec):
    try: sec=float(sec)
    except Exception: return ""
    h=int(sec//3600); m=int(round((sec%3600)/60))
    return (f"{h}시간 " if h else "")+f"{m}분"

def _coerce_json()->Dict[str,Any]:
    j=request.get_json(silent=True, force=True)
    if isinstance(j,dict): return j
    if request.form: return {k:request.form.get(k) for k in request.form}
    if request.args: return {k:request.args.get(k) for k in request.args}
    return {}

# ---------------- 지오코딩 ----------------
def geocode_google(address: str) -> Optional[Tuple[float,float]]:
    if not GOOGLE_API_KEY or not address: return None
    try:
        r=requests.get("https://maps.googleapis.com/maps/api/geocode/json",
                       params={"address":address,"key":GOOGLE_API_KEY}, timeout=8)
        loc=r.json()["results"][0]["geometry"]["location"]
        return float(loc["lat"]), float(loc["lng"])
    except Exception as e:
        log.warning("geocode_google fail: %s", e); return None

@lru_cache(maxsize=2048)
def reverse_geocode_google(lat: float, lon: float)->str:
    if not GOOGLE_API_KEY: return ""
    try:
        r=requests.get("https://maps.googleapis.com/maps/api/geocode/json",
                       params={"latlng":f"{lat},{lon}","key":GOOGLE_API_KEY}, timeout=8)
        return r.json()["results"][0]["formatted_address"]
    except Exception:
        return ""

# ---------------- 경유지 자동 선택(해변) ----------------
def _ll_to_xy_km(lat: float, lon: float, lat0: float, lon0: float):
    x=(lon-lon0)*math.cos(math.radians(lat0))*111.32
    y=(lat-lat0)*110.57
    return x,y

def _projection_metrics(start, end, p):
    (slat,slon),(elat,elon),(plat,plon)=start,end,p
    lat0=(slat+elat)/2.0; lon0=(slon+elon)/2.0
    sx,sy=_ll_to_xy_km(slat,slon,lat0,lon0)
    ex,ey=_ll_to_xy_km(elat,elon,lat0,lon0)
    px,py=_ll_to_xy_km(plat,plon,lat0,lon0)
    vx,vy=(ex-sx),(ey-sy); ux,uy=(px-sx),(py-sy)
    denom=vx*vx+vy*vy
    if denom<=0: return 0.0,float("inf")
    t=(ux*vx+uy*vy)/denom
    cross=abs(vx*uy - vy*ux)
    vnorm=math.sqrt(denom)
    perp_km=cross/vnorm if vnorm>0 else float("inf")
    return t, perp_km

def _approx_chain_length(points, end):
    seq=points+[end]; tot=0.0
    for i in range(len(seq)-1):
        a,b=seq[i],seq[i+1]
        tot+=haversine(a[0],a[1],b[0],b[1])
    return tot

def _max_direct(v): return max(v,1e-6)

def find_waypoints_along_direction(start, end, max_n=3, corridor_km=30.0, max_abs_detour_km=50.0, max_rel_detour=0.35):
    if not beach_coords: return []
    cands=[]
    for name,(lon,lat) in beach_coords.items():
        t,off=_projection_metrics(start,end,(lat,lon))
        if 0.0<t<1.0 and off<=corridor_km: cands.append((t,name,lat,lon))
    cands.sort(key=lambda x:x[0])
    if not cands: return []
    sel=[]; base=haversine(start[0],start[1],end[0],end[1]); chain=[start]
    for t,name,lat,lon in cands:
        tentative=chain+[(lat,lon)]
        detour=_approx_chain_length(tentative,end)-base
        if detour<=max_abs_detour_km and detour/_max_direct(base)<=max_rel_detour:
            sel.append((name,lat,lon,t)); chain.append((lat,lon))
            if len(sel)>=max_n: break
    return sel

# ---------------- ORS 라우팅 ----------------
def get_ors_route_multi(points: List[Tuple[float,float]])->Tuple[Dict[str,Any], int]:
    if not ORS_API_KEY: return {"error":"ORS_API_KEY missing"}, 500
    coords=[[lon,lat] for (lat,lon) in points]
    try:
        r=requests.post("https://api.openrouteservice.org/v2/directions/driving-car/geojson",
                        headers={"Authorization":ORS_API_KEY,"Content-Type":"application/json"},
                        json={"coordinates":coords}, timeout=20)
        return r.json(), r.status_code
    except Exception as e:
        return {"error":str(e)}, 500

# ---------------- TourAPI 호출(안정화: 키/프로토콜 조합) ----------------
BASE_HOST="apis.data.go.kr"; BASE_SVC="B551011"; BASE_API="KorService1"

def _key_variants():
    if not TOURAPI_KEY_RAW: return []
    raw=TOURAPI_KEY_RAW
    decoded=unquote(raw)              # %2B -> +  등
    encoded=quote(decoded, safe="")   # 완전 인코딩
    uniq=[]
    for t,v in [("raw",raw),("decoded",decoded),("encoded",encoded)]:
        if v not in [vv for _,vv in uniq]: uniq.append((t,v))
    return uniq

def _tourapi_request(path: str, params_wo_key: Dict[str,Any], timeout=10):
    tries=[]
    if not TOURAPI_KEY_RAW:
        return None, {"tries":[{"error":"TOURAPI_KEY missing"}]}
    for scheme in ("https","http"):
        for key_type, key_val in _key_variants():
            p=dict(params_wo_key); p["serviceKey"]=key_val
            url=f"{scheme}://{BASE_HOST}/{BASE_SVC}/{BASE_API}/{path}"
            try:
                r=requests.get(url, params=p, timeout=timeout)
                status=r.status_code; j=None; result_code=result_msg=None
                items_cnt=None
                if status==200:
                    try:
                        j=r.json()
                        header=j.get("response",{}).get("header",{})
                        result_code=header.get("resultCode"); result_msg=header.get("resultMsg")
                        body=j.get("response",{}).get("body",{})
                        items=body.get("items",{}).get("item",[])
                        if isinstance(items,dict): items=[items]
                        items_cnt=len(items)
                        tries.append({"scheme":scheme,"key":key_type,"status":status,"resultCode":result_code,"resultMsg":result_msg,"items_count":items_cnt})
                        if result_code in (None,"0000"):
                            return j, {"tries":tries}
                    except Exception as pe:
                        tries.append({"scheme":scheme,"key":key_type,"status":status,"parse_error":str(pe)})
                else:
                    tries.append({"scheme":scheme,"key":key_type,"status":status,"error":"HTTP non-200"})
            except Exception as e:
                tries.append({"scheme":scheme,"key":key_type,"error":str(e)})
    return None, {"tries":tries}

def _location_based(lon: float, lat: float, ctype: int, radius_m: int, rows: int):
    params={
        "mapX":lon, "mapY":lat, "radius":min(int(radius_m),20000),
        "listYN":"Y", "numOfRows":rows, "pageNo":1,
        "MobileOS":"ETC", "MobileApp":"CoastalDrive", "_type":"json",
        "contentTypeId":ctype
    }
    j, meta = _tourapi_request("locationBasedList1", params, timeout=10)
    items=[]
    if j:
        try:
            items=j.get("response",{}).get("body",{}).get("items",{}).get("item",[]) or []
            if isinstance(items,dict): items=[items]
        except Exception:
            items=[]
    return items, meta

@lru_cache(maxsize=4096)
def _detail_intro(content_id: str, ctype: int):
    j,_=_tourapi_request("detailIntro1",{
        "contentId":content_id, "contentTypeId":ctype,
        "MobileOS":"ETC","MobileApp":"CoastalDrive","_type":"json"
    }, timeout=8)
    if not j: return {}
    try:
        items=j.get("response",{}).get("body",{}).get("items",{}).get("item",[]) or []
        return items[0] if isinstance(items,list) and items else (items if isinstance(items,dict) else {})
    except: return {}

@lru_cache(maxsize=4096)
def _detail_common(content_id: str):
    j,_=_tourapi_request("detailCommon1",{
        "contentId":content_id, "defaultYN":"Y","overviewYN":"Y","addrinfoYN":"Y",
        "mapinfoYN":"Y","firstImageYN":"Y","_type":"json",
        "MobileOS":"ETC","MobileApp":"CoastalDrive"
    }, timeout=8)
    if not j: return {}
    try:
        items=j.get("response",{}).get("body",{}).get("items",{}).get("item",[]) or []
        return items[0] if isinstance(items,list) and items else (items if isinstance(items,dict) else {})
    except: return {}

# ---------------- “경로-점” 최단거리(코리도 필터) ----------------
def _min_dist_point_to_polyline_km(lat: float, lon: float, route_coords: List[List[float]]) -> float:
    """
    route_coords: [[lon,lat], ...]
    평면 근사(국지 좌표계)로 각 세그먼트-점 거리의 최솟값(km)
    """
    if not route_coords or len(route_coords) < 2:
        return float("inf")
    dmin = float("inf")
    for i in range(len(route_coords) - 1):
        lon1, lat1 = route_coords[i]
        lon2, lat2 = route_coords[i+1]
        lat0 = (lat1 + lat2)/2.0; lon0 = (lon1 + lon2)/2.0
        ax, ay = _ll_to_xy_km(lat1, lon1, lat0, lon0)
        bx, by = _ll_to_xy_km(lat2, lon2, lat0, lon0)
        px, py = _ll_to_xy_km(lat, lon,  lat0, lon0)
        vx, vy = (bx-ax), (by-ay)
        wx, wy = (px-ax), (py-ay)
        denom = vx*vx + vy*vy
        if denom <= 0:
            # 퇴화세그먼트: 점-점 거리
            d = math.hypot(px-ax, py-ay)
        else:
            t = (wx*vx + wy*vy) / denom
            t = max(0.0, min(1.0, t))
            nx, ny = (ax + t*vx), (ay + t*vy)
            d = math.hypot(px-nx, py-ny)
        if d < dmin:
            dmin = d
    return dmin  # 이미 km 단위

# ---------------- 경로 주변 검색(코리도 30km) ----------------
def _sample_idxs_by_distance(coords: List[List[float]], interval_km: float, max_samples: int=120) -> List[int]:
    if not coords: return []
    idxs=[0]; acc=0.0; last=coords[0]; last_pick=0
    for i in range(1, len(coords)):
        lon,lat=coords[i]
        acc += haversine(last[1], last[0], lat, lon)
        last = coords[i]
        if (acc >= interval_km and i - last_pick >= 1) or (len(idxs) < 4 and i % max(1, len(coords)//4) == 0):
            idxs.append(i); acc=0.0; last_pick=i
        if len(idxs) >= max_samples: break
    if idxs[-1] != len(coords)-1: idxs.append(len(coords)-1)
    return sorted(set(idxs))

def _collect_raw_candidates_along_route(route_coords: List[List[float]],
                                        ctype: int, corridor_km: float,
                                        interval_km: float = 10.0,
                                        radii: List[int] = [5000,8000,12000,20000],
                                        rows: int = 40,
                                        max_samples: int = 120) -> Dict[str, Dict[str, Any]]:
    """
    경로 전체를 따라 샘플 포인트에서 locationBasedList1 호출 → 중복 제거 →
    경로-점 최단거리를 계산해 코리도 내 후보만 반환.
    return: {contentid: {"item": raw_item, "min_route_dist_km": float}}
    """
    if not route_coords: return {}
    idxs = _sample_idxs_by_distance(route_coords, interval_km, max_samples=max_samples)
    pool: Dict[str, Dict[str, Any]] = {}
    for r in radii:
        for i in idxs:
            lon, lat = route_coords[i]
            items, _ = _location_based(lon, lat, ctype=ctype, radius_m=r, rows=rows)
            for it in items:
                cid = str(it.get("contentid") or "")
                if not cid or cid in pool:
                    continue
                mx, my = _to_float(it.get("mapx")), _to_float(it.get("mapy"))
                if mx is None or my is None:
                    continue
                d = _min_dist_point_to_polyline_km(my, mx, route_coords)
                if d <= corridor_km:
                    pool[cid] = {"item": it, "min_route_dist_km": d}
    return pool

def search_tour_items_along_route(geojson: Dict[str,Any], corridor_km: float=30.0, limit_each: int=60) -> Dict[str, List[Dict[str,Any]]]:
    """
    1) 경로 전체 샘플링 + 반경 단계 상승으로 후보 수집
    2) 경로-점 최단거리로 코리도 내 선별
    3) 거리 가까운 순으로 정렬 후 detailIntro/detailCommon으로 보강하여 상위 N개만 반환
    """
    try:
        route_coords = geojson["features"][0]["geometry"]["coordinates"]  # [ [lon,lat], ... ]
    except Exception:
        return {"tour": [], "food": [], "all": []}

    # 후보 수집
    pool_tour = _collect_raw_candidates_along_route(route_coords, 12, corridor_km)
    pool_food = _collect_raw_candidates_along_route(route_coords, 39, corridor_km)

    # 가까운 순으로 정렬
    tour_sorted = sorted(pool_tour.values(), key=lambda x: x["min_route_dist_km"])[:max(0, limit_each)]
    food_sorted = sorted(pool_food.values(), key=lambda x: x["min_route_dist_km"])[:max(0, limit_each)]

    # 정상화(팝업용 필드 표준화)
    def normalize_batch(arr: List[Dict[str,Any]], category: str, ctype: int) -> List[Dict[str,Any]]:
        out=[]
        for rec in arr:
            it = rec["item"]
            intro  = _detail_intro(str(it.get("contentid")), ctype)
            common = _detail_common(str(it.get("contentid")))
            norm = {
                "contentid": str(it.get("contentid") or ""),
                "contenttypeid": int(ctype),
                "title": it.get("title") or "",
                "addr1": it.get("addr1") or "",
                "mapx": _to_float(it.get("mapx")) or 0.0,
                "mapy": _to_float(it.get("mapy")) or 0.0,
                "firstimage": it.get("firstimage") or common.get("firstimage") or "",
                "homepage": it.get("homepage") or common.get("homepage") or "",
                "tel": it.get("tel") or common.get("tel") or "",
                "category": category,
                "distance_km": f"{rec['min_route_dist_km']:.2f}",
                "openhour":  (intro.get("usetime") if category=="tour" else intro.get("opentimefood")) or "",
                "restday":   (intro.get("restdate") if category=="tour" else intro.get("restdatefood")) or "",
                "parking_info": (intro.get("parking") if category=="tour" else intro.get("parkingfood")) or "",
            }
            p=(norm["parking_info"] or "").strip()
            norm["has_parking"] = bool(p) and ("불가" not in p and "없" not in p)
            out.append(norm)
        return out

    tours = normalize_batch(tour_sorted, "tour", 12)
    foods = normalize_batch(food_sorted, "food", 39)
    return {"tour": tours, "food": foods, "all": tours + foods}

# ---------------- 라우팅 ----------------
def _handle_route():
    if not ORS_API_KEY:     return jsonify({"error":"ORS_API_KEY not set"}), 500
    if not TOURAPI_KEY_RAW: return jsonify({"error":"TOURAPI_KEY not set"}), 500

    data=_coerce_json()
    start_in=data.get("start") or data.get("origin") or data.get("from")
    end_in  =data.get("end")   or data.get("destination") or data.get("to")
    max_wps=int(data.get("max_waypoints") or 3); max_wps=max(0,min(3,max_wps))
    try: corridor_km=float(data.get("corridor_km") or 30.0)
    except Exception: corridor_km=30.0
    corridor_km=max(5.0, min(50.0, corridor_km))

    if not start_in or not end_in: return jsonify({"error":"start/end 누락"}), 400
    start=geocode_google(start_in) if isinstance(start_in,str) else tuple(start_in) if isinstance(start_in,(list,tuple)) else None
    end  =geocode_google(end_in)   if isinstance(end_in,  str) else tuple(end_in)   if isinstance(end_in,  (list,tuple)) else None
    if not start or not end: return jsonify({"error":"주소 변환 실패"}), 400

    way_sel=find_waypoints_along_direction(start, end, max_n=max_wps)

    # ORS 라우팅
    points=[start]+[(lat,lon) for (_,lat,lon,_) in way_sel]+[end]
    route_data,status=get_ors_route_multi(points)
    if status!=200 or "error" in route_data:
        return jsonify({"error":route_data.get("error",f"OpenRouteService 실패({status})")}), status

    # 요약
    try:
        summary=route_data["features"][0]["properties"]["summary"]
        dist_m=float(summary.get("distance",0.0)); dur_s=float(summary.get("duration",0.0))
        route_summary={"distance_m":dist_m,"duration_s":dur_s,
                       "distance_text":_fmt_dist_m(dist_m),"duration_text":_fmt_dur_s(dur_s)}
    except Exception:
        route_summary={"distance_m":0.0,"duration_s":0.0,"distance_text":"","duration_text":""}

    # 경로 주변 TourAPI 수집 (코리도 30km)
    spots=search_tour_items_along_route(route_data, corridor_km=corridor_km, limit_each=int(data.get("limit_each") or 60))
    counts={"tour":len(spots["tour"]), "food":len(spots["food"]), "all":len(spots["all"])}

    # 경유지 응답
    wp_objs=[]
    for i,(name,lat,lon,t) in enumerate(way_sel, start=1):
        wp_objs.append({"order":i,"name":name,"lat":lat,"lon":lon,"t":t,"address":reverse_geocode_google(lat,lon) or ""})

    resp={
        "route": route_data,
        "route_summary": route_summary,
        "waypoints_used": wp_objs,
        "spots": spots["all"],
        "spots_grouped": {"tour":spots["tour"], "food":spots["food"]},
        "spot_counts": counts,
        "corridor_km": corridor_km
    }
    if wp_objs:
        resp["waypoint"]={"name":wp_objs[0]["name"], "lat":wp_objs[0]["lat"], "lon":wp_objs[0]["lon"], "address":wp_objs[0]["address"]}
    return jsonify(resp), 200

@app.route("/route", methods=["POST","GET"])
def route():
    if request.method=="GET": return redirect("/")
    return _handle_route()

@app.route("/api/route", methods=["POST"])
def api_route(): return _handle_route()

# ---------------- 디버그: "경로 전체"에서 TourAPI 후보 수집 ----------------
@app.route("/debug/tourapi_along", methods=["POST"])
def debug_tourapi_along():
    """
    body: { "coords": [[lon,lat],...], "corridor_km": 30, "ctype": 12 }
    경로 전체를 샘플링해 후보를 모으고, 코리도 필터링 후 개수 리턴
    """
    try:
        data=request.get_json(force=True, silent=False) or {}
        coords=data.get("coords") or []
        corridor_km=float(data.get("corridor_km") or 30.0)
        ctype=int(data.get("ctype") or 12)
        assert isinstance(coords, list) and len(coords)>=2
    except Exception:
        return jsonify({"ok":False, "error":"잘못된 입력(coords/corridor_km/ctype)"}), 400

    pool=_collect_raw_candidates_along_route(coords, ctype, corridor_km)
    sample=[{"title":v["item"].get("title"), "addr1":v["item"].get("addr1"),
             "dist_km":f'{v["min_route_dist_km"]:.2f}', "mapx":v["item"].get("mapx"),
             "mapy":v["item"].get("mapy")} for v in list(pool.values())[:10]]
    return jsonify({"ok":True, "ctype":ctype, "corridor_km":corridor_km,
                    "cand_count": len(pool), "sample": sample})

# ---------------- 상세 페이지 ----------------
@app.route("/tour_detail/<contentid>")
def tour_detail(contentid: str):
    common=_detail_common(contentid) or {}
    try: ctype=int(common.get("contenttypeid") or 12)
    except Exception: ctype=12
    intro=_detail_intro(contentid, ctype) or {}

    title=escape(common.get("title") or "상세정보")
    addr1=escape(common.get("addr1") or ""); tel=escape(common.get("tel") or "")
    hp=escape(common.get("homepage") or ""); img=escape(common.get("firstimage") or "")
    ovw=common.get("overview") or ""; ovw_safe=escape(ovw).replace("\n","<br>")

    if ctype==39:
        openhour=intro.get("opentimefood") or ""; restday=intro.get("restdatefood") or ""; park=intro.get("parkingfood") or ""
    else:
        openhour=intro.get("usetime") or ""; restday=intro.get("restdate") or ""; park=intro.get("parking") or ""

    html=f"""<!doctype html><html lang="ko"><meta charset="utf-8"><title>{title}</title>
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

if __name__ == "__main__":
    port=int(os.environ.get("PORT","10000"))
    log.info(f"Starting on 0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port)
