# app.py  (Render 친화: 시간예산/타임아웃/동일도메인 index 서빙)
import os, math, json, time
from typing import List, Dict, Tuple, Any
import requests
from flask import Flask, request, jsonify, send_from_directory, render_template_string
from flask_cors import CORS

app = Flask(__name__, static_folder=None)
CORS(app)

# ====== 프로덕션 권장: gunicorn으로 구동 (Start Command 참고) ======

# --- 내장 index.html (파일 없을 때 사용) ---
INDEX_HTML = r"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8"/><title>바다따라</title>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/ol@7.3.0/ol.css">
<script src="https://cdn.jsdelivr.net/npm/ol@7.3.0/dist/ol.js"></script>
<style>
html,body{margin:0;height:100%;font-family:system-ui,-apple-system,Segoe UI,Roboto,Noto Sans KR,Arial,sans-serif}
.app{display:grid;grid-template-rows:auto 1fr;height:100%}
.toolbar{padding:10px 12px;display:grid;grid-template-columns:1.2fr 1.2fr auto auto auto auto;gap:8px;align-items:center;border-bottom:1px solid #e5e7eb;background:#fff;position:sticky;top:0;z-index:10}
.toolbar input[type="text"]{width:100%;padding:10px 12px;border:1px solid #cbd5e1;border-radius:10px;font-size:14px}
.toolbar input[type="number"]{width:90px;padding:8px;border:1px solid #cbd5e1;border-radius:10px;font-size:13px}
.toolbar button{padding:10px 14px;border:0;border-radius:12px;background:#111827;color:#fff;font-weight:600;cursor:pointer}
.toolbar button:disabled{opacity:.6;cursor:not-allowed}
#map{width:100%;height:100%}
.summary{font-size:13px;color:#374151;padding:4px 8px}
.legend{display:flex;gap:14px;align-items:center;font-size:12px;color:#374151}
.legend .dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px;vertical-align:middle}
.dot-route{background:#0b7285}.dot-beach{background:#1e3a8a}.dot-tour{background:#065f46}.dot-food{background:#92400e}
.error{color:#b91c1c;font-size:13px;padding-left:8px}
.ol-popup{position:absolute;background:#fff;box-shadow:0 10px 25px rgba(0,0,0,.15);padding:10px 12px;border-radius:12px;border:1px solid #e5e7eb;min-width:260px}
.ol-popup:after,.ol-popup:before{top:100%;border:solid transparent;content:" ";height:0;width:0;position:absolute;pointer-events:none}
.ol-popup:after{border-top-color:#fff;border-width:10px;left:24px;margin-left:-10px}
.ol-popup:before{border-top-color:rgba(0,0,0,.08);border-width:11px;left:24px;margin-left:-11px}
</style></head>
<body><div class="app">
  <div class="toolbar">
    <input id="origin" placeholder="출발지 (주소 or '127.1,37.4')"/>
    <input id="destination" placeholder="도착지 (주소 or '128.6,38.2')"/>
    <input id="corridor" type="number" value="30" step="1" min="5" title="코리도(km)"/>
    <input id="detourAbs" type="number" value="50" step="1" min="5" title="절대우회(km)"/>
    <input id="detourRel" type="number" value="0.35" step="0.01" min="0.05" title="상대우회배수"/>
    <button id="routeBtn">경로 계산</button>
    <div class="legend"><span><span class="dot dot-route"></span> 경로</span><span><span class="dot dot-beach"></span> 해수욕장</span><span><span class="dot dot-tour"></span> 관광지</span><span><span class="dot dot-food"></span> 맛집</span></div>
    <div id="errorBox" class="error"></div><div class="summary" id="summaryBox"></div>
  </div>
  <div id="map"></div>
</div>
<div id="popup" class="ol-popup" style="display:none;"></div>
<script>
const BASE_URL="";
const map=new ol.Map({target:'map',layers:[new ol.layer.Tile({source:new ol.source.OSM()})],view:new ol.View({center:ol.proj.fromLonLat([127.8,36.5]),zoom:7})});
const routeSource=new ol.source.Vector(), waypointsSource=new ol.source.Vector(), poisSource=new ol.source.Vector();
map.addLayer(new ol.layer.Vector({source:routeSource,style:new ol.style.Style({stroke:new ol.style.Stroke({color:'#0b7285',width:4})})}));
map.addLayer(new ol.layer.Vector({source:waypointsSource,style:(f)=>new ol.style.Style({image:new ol.style.Circle({radius:8,fill:new ol.style.Fill({color:'#1e3a8a'}),stroke:new ol.style.Stroke({color:'#fff',width:2})}),text:new ol.style.Text({text:String(f.get('order')||''),font:'bold 11px sans-serif',fill:new ol.style.Fill({color:'#fff'})})})}));
map.addLayer(new ol.layer.Vector({source:poisSource,style:(f)=>{const ct=f.get('contenttypeid');const fill=(ct==='12')?'#065f46':(ct==='39'?'#92400e':'#374151');return new ol.style.Style({image:new ol.style.Circle({radius:6,fill:new ol.style.Fill({color:fill}),stroke:new ol.style.Stroke({color:'#fff',width:2})})})}}));
const popupEl=document.getElementById('popup'), overlay=new ol.Overlay({element:popupEl,offset:[0,-14],positioning:'bottom-left',stopEvent:false}); map.addOverlay(overlay);
let hover=null; function showPopup(feat,coord){const p=feat.getProperties(); if(p.popup_html){popupEl.innerHTML=p.popup_html;}
else if(p.kind==='beach_waypoint'){const t=(typeof p.t==='number')?p.t.toFixed(3):p.t; const perp=(typeof p.perp_km==='number')?p.perp_km.toFixed(2):p.perp_km; popupEl.innerHTML=`<b>${p.title||'경유지(해수욕장)'}</b>${p.addr1?`<div>주소: ${p.addr1}</div>`:''}${p.tel?`<div>전화: ${p.tel}</div>`:''}<div>순서: ${p.order} · t=${t} · 측면이탈=${perp} km</div>`;}
else {popupEl.style.display='none'; return;} popupEl.style.display='block'; overlay.setPosition(coord);}
function hidePopup(){popupEl.style.display='none'; overlay.setPosition(undefined);}
map.on('pointermove',(e)=>{if(e.dragging)return; const f=map.forEachFeatureAtPixel(map.getEventPixel(e.originalEvent), x=>x); if(f){map.getTargetElement().style.cursor='pointer'; if(f!==hover){hover=f; showPopup(f,e.coordinate);} } else {map.getTargetElement().style.cursor=''; hover=null; hidePopup(); }});
const $o=document.getElementById('origin'),$d=document.getElementById('destination'),$c=document.getElementById('corridor'),$da=document.getElementById('detourAbs'),$dr=document.getElementById('detourRel'),$btn=document.getElementById('routeBtn'),$sum=document.getElementById('summaryBox'),$err=document.getElementById('errorBox');
function setBusy(b){$btn.disabled=b; $btn.textContent=b?'계산 중…':'경로 계산';} function setError(m){$err.textContent=m||'';}
function addGeoJSON(g,src){const f=new ol.format.GeoJSON(); const feats=f.readFeatures(g,{dataProjection:'EPSG:4326',featureProjection:'EPSG:3857'}); src.clear(true); src.addFeatures(feats);}
function fitAll(){const ex=ol.extent.createEmpty(); ol.extent.extend(ex,routeSource.getExtent()); ol.extent.extend(ex,waypointsSource.getExtent()); ol.extent.extend(ex,poisSource.getExtent()); if(ex && ex.every(v=>isFinite(v))) map.getView().fit(ex,{padding:[30,30,30,30],duration:500,maxZoom:14});}
async function go(){setError(''); $sum.textContent=''; setBusy(true);
 try{const o=$o.value.trim(), d=$d.value.trim(); if(!o||!d) throw new Error('출발지와 도착지를 입력하세요.');
   const payload={origin:o,destination:d,corridor_km:Number($c.value||30),detour_abs_km:Number($da.value||50),detour_rel:Number($dr.value||0.35),max_waypoints:3};
   const r=await fetch((BASE_URL||'')+'/api/route',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
   const data=await r.json(); if(!r.ok||!data.ok) throw new Error(data.error||'경로 계산 실패');
   addGeoJSON(data.route_geojson,routeSource); addGeoJSON(data.waypoints_geojson,waypointsSource); addGeoJSON(data.pois_geojson,poisSource);
   const s=data.summary||{}; $sum.textContent=`직행 ${s.direct_km}km → 최종 ${s.final_km}km (우회 +${s.detour_km}km) · 경유지 ${s.waypoints_count}개 · 코리도 ${s.corridor_km}km / 우회한도 ${s.detour_abs_km}km · 상대 ${s.detour_rel}`;
   fitAll();
 }catch(e){ setError(String(e.message||e)); } finally{ setBusy(false); }}
document.getElementById('routeBtn').addEventListener('click', go);
</script></body></html>
"""

# ====== 키 & 기본값 ======
ORS_API_KEY    = os.getenv("ORS_API_KEY",    "5b3ce3597851110001cf62486d543846e80049df9c7a9e10ecef2953")
VWORLD_API_KEY = os.getenv("VWORLD_API_KEY", "9E77283D-954A-3077-B7C8-9BD5ADB33255")
TOURAPI_KEY    = os.getenv("TOURAPI_KEY",    "e1tU33wjMx2nynKjH8yDBm/S4YNne6B8mpCOWtzMH9TSONF71XG/xAwPqyv1fANpgeOvbPY+Le+gM6cYCnWV8w==")

DEFAULT_CORRIDOR_KM   = 30.0
DEFAULT_DETOUR_ABS_KM = 50.0
DEFAULT_DETOUR_REL    = 0.35
DEFAULT_MAX_WAYPOINTS = 3
FORCE_ONE_WAYPOINT    = True

# ===== 성능/안정화 상수 =====
TIME_BUDGET_SEC   = 25          # 한 요청 최대 처리 시간(초)
SAMPLE_POINTS     = 6           # 해변 후보 샘플링 포인트 (기존 9 → 6)
POI_RADIUS_M      = 3000        # 경로 주변 POI 반경 축소 (5km → 3km)
BEACH_SEARCH_RADIUS_M = 25000   # 해수욕장 탐색 반경 약간 축소
HTTP = requests.Session()
TO = { "default": 8, "ors": 18, "tour": 8, "vworld": 8, "nominatim": 8 }  # 타임아웃(초)

EARTH_RADIUS_KM   = 6371.0088

# ===== 지리 계산 =====
def deg2rad(d: float) -> float: return d * math.pi / 180.0
def lonlat_to_xy_km(lon: float, lat: float, lat_ref: float) -> Tuple[float, float]:
    x = EARTH_RADIUS_KM * deg2rad(lon) * math.cos(deg2rad(lat_ref)); y = EARTH_RADIUS_KM * deg2rad(lat); return x, y
def haversine_km(a: Tuple[float,float], b: Tuple[float,float]) -> float:
    lon1,lat1=a; lon2,lat2=b; dlon=deg2rad(lon2-lon1); dlat=deg2rad(lat2-lat1); la1=deg2rad(lat1); la2=deg2rad(lat2)
    h=math.sin(dlat/2)**2+math.cos(la1)*math.cos(la2)*math.sin(dlon/2)**2; return 2*EARTH_RADIUS_KM*math.asin(math.sqrt(h))
def projection_t_and_cross_km(s,d,p)->Tuple[float,float]:
    lon_s,lat_s=s; lon_d,lat_d=d; lon_p,lat_p=p; lat_ref=(lat_s+lat_d+lat_p)/3.0
    sx,sy=lonlat_to_xy_km(lon_s,lat_s,lat_ref); dx,dy=lonlat_to_xy_km(lon_d,lat_d,lat_ref); px,py=lonlat_to_xy_km(lon_p,lat_p,lat_ref)
    vx,vy=dx-sx,dy-sy; wx,wy=px-sx,py-sy; v2=vx*vx+vy*vy; t=0.0 if v2==0 else (wx*vx+wy*vy)/v2
    cross=abs(vx*wy-vy*wx); vlen=math.sqrt(v2) if v2>0 else 1e-9; dist_perp=cross/vlen; return t,dist_perp

# ===== 지오코딩(다중 폴백) =====
def _json_or_raise(resp):
    try: return resp.json()
    except Exception:
        snippet=(resp.text or "")[:200].replace("\n"," ")
        raise RuntimeError(f"Non-JSON from {resp.url} (status {resp.status_code}): {snippet!r}")

def geocode_any(query: str)->Tuple[float,float]:
    # 좌표 문자열 허용
    if isinstance(query,str):
        q=query.strip()
        for sep in [","," "]:
            if sep in q:
                parts=[p for p in q.split(sep) if p]
                if len(parts)==2:
                    try:
                        lon=float(parts[0]); lat=float(parts[1])
                        if -180<=lon<=180 and -90<=lat<=90: return lon,lat
                    except: pass
                break
    # VWorld ROAD
    try:
        url="https://api.vworld.kr/req/address"; params={"service":"address","request":"getCoord","version":"2.0","crs":"EPSG:4326","address":query,"refine":"true","simple":"false","format":"json","type":"ROAD","key":VWORLD_API_KEY}
        r=HTTP.get(url,params=params,timeout=TO["vworld"]); data=_json_or_raise(r)
        if data.get("response",{}).get("status")=="OK":
            pt=data["response"]["result"]["point"]; return float(pt["x"]), float(pt["y"])
    except: pass
    # VWorld PARCEL
    try:
        url="https://api.vworld.kr/req/address"; params={"service":"address","request":"getCoord","version":"2.0","crs":"EPSG:4326","address":query,"refine":"true","simple":"false","format":"json","type":"PARCEL","key":VWORLD_API_KEY}
        r=HTTP.get(url,params=params,timeout=TO["vworld"]); data=_json_or_raise(r)
        if data.get("response",{}).get("status")=="OK":
            pt=data["response"]["result"]["point"]; return float(pt["x"]), float(pt["y"])
    except: pass
    # ORS
    try:
        url="https://api.openrouteservice.org/geocode/search"; params={"api_key":ORS_API_KEY,"text":query,"size":1}
        r=HTTP.get(url,params=params,timeout=TO["ors"]); data=_json_or_raise(r)
        feats=data.get("features") or []; if feats: c=feats[0]["geometry"]["coordinates"]; return float(c[0]), float(c[1])
    except: pass
    # Nominatim
    try:
        url="https://nominatim.openstreetmap.org/search"; params={"q":query,"format":"json","limit":1}
        headers={"User-Agent":"CoastalDrive/1.0 (+render)"}; r=HTTP.get(url,params=params,headers=headers,timeout=TO["nominatim"]); data=_json_or_raise(r)
        if isinstance(data,list) and data: return float(data[0]["lon"]), float(data[0]["lat"])
    except: pass
    raise ValueError(f"Geocoding failed for: {query}")

# ===== TourAPI =====
def tourapi_search_keyword_near(lon:float, lat:float, keyword:str, radius_m:int, num_rows:int=30)->List[Dict[str,Any]]:
    base="https://apis.data.go.kr/B551011/KorService1/searchKeyword1"
    params={"serviceKey":TOURAPI_KEY,"MobileOS":"ETC","MobileApp":"CoastalDrive","keyword":keyword,"mapX":f"{lon:.6f}","mapY":f"{lat:.6f}","radius":radius_m,"listYN":"Y","arrange":"E","numOfRows":num_rows,"pageNo":1,"_type":"json"}
    r=HTTP.get(base,params=params,timeout=TO["tour"])
    try:
        items=r.json().get("response",{}).get("body",{}).get("items",{}).get("item",[])
        return items if isinstance(items,list) else ([items] if items else [])
    except: return []

def tourapi_location_based(lon:float, lat:float, radius_m:int, content_type_id:int, num_rows:int=30)->List[Dict[str,Any]]:
    base="https://apis.data.go.kr/B551011/KorService1/locationBasedList1"
    params={"serviceKey":TOURAPI_KEY,"MobileOS":"ETC","MobileApp":"CoastalDrive","mapX":f"{lon:.6f}","mapY":f"{lat:.6f}","radius":radius_m,"contentTypeId":content_type_id,"listYN":"Y","arrange":"E","numOfRows":num_rows,"pageNo":1,"_type":"json"}
    r=HTTP.get(base,params=params,timeout=TO["tour"])
    try:
        items=r.json().get("response",{}).get("body",{}).get("items",{}).get("item",[])
        return items if isinstance(items,list) else ([items] if items else [])
    except: return []

def tourapi_detail_intro(content_id:str, content_type_id:str)->Dict[str,Any]:
    base="https://apis.data.go.kr/B551011/KorService1/detailIntro1"
    params={"serviceKey":TOURAPI_KEY,"MobileOS":"ETC","MobileApp":"CoastalDrive","contentId":content_id,"contentTypeId":content_type_id,"_type":"json"}
    r=HTTP.get(base,params=params,timeout=TO["tour"])
    try:
        items=r.json().get("response",{}).get("body",{}).get("items",{}).get("item",[])
        return items[0] if isinstance(items,list) and items else {}
    except: return {}

# ===== ORS 경로 =====
def ors_route_distance_and_geojson(coords_lonlat: List[Tuple[float,float]])->Tuple[float,Dict[str,Any]]:
    url="https://api.openrouteservice.org/v2/directions/driving-car/geojson"
    headers={"Authorization":ORS_API_KEY,"Content-Type":"application/json"}
    body={"coordinates":[[lon,lat] for lon,lat in coords_lonlat],"instructions":False}
    r=HTTP.post(url,headers=headers,data=json.dumps(body),timeout=TO["ors"]); data=r.json()
    total_m=0.0
    try:
        for feat in data.get("features",[]): total_m+=feat.get("properties",{}).get("summary",{}).get("distance",0.0)
    except: pass
    return total_m/1000.0, data

# ===== 후보 수집/선정 (시간예산 적용) =====
def collect_beach_candidates_on_line(start, end, corridor_km, deadline)->List[Dict[str,Any]]:
    s_lon,s_lat=start; d_lon,d_lat=end; candidates={}
    for i in range(SAMPLE_POINTS):
        if time.monotonic()>deadline: break
        t=(i+1)/(SAMPLE_POINTS+1); q_lon=s_lon+(d_lon-s_lon)*t; q_lat=s_lat+(d_lat-s_lat)*t
        items=tourapi_search_keyword_near(q_lon,q_lat,"해수욕장",BEACH_SEARCH_RADIUS_M,num_rows=30)
        for it in items:
            try:
                title=(it.get("title") or "").strip(); contentid=str(it.get("contentid"))
                mapx=float(it.get("mapx")); mapy=float(it.get("mapy"))
                if not contentid or not title: continue
                t_proj, dist_perp=projection_t_and_cross_km(start,end,(mapx,mapy))
                if 0.0<=t_proj<=1.0 and dist_perp<=corridor_km:
                    prev=candidates.get(contentid)
                    if (prev is None) or (dist_perp<prev["dist_perp_km"]):
                        candidates[contentid]={"contentid":contentid,"title":title,"lon":mapx,"lat":mapy,"t":float(t_proj),"dist_perp_km":float(dist_perp),
                                               "contenttypeid":str(it.get("contenttypeid","")),"firstimage":it.get("firstimage",""),
                                               "addr1":it.get("addr1",""),"tel":it.get("tel","")}
            except: continue
        time.sleep(0.08)
    lst=list(candidates.values()); lst.sort(key=lambda x:x["t"]); return lst

def greedy_pick_waypoints(start,end,candidates,direct_km,detour_abs_km,detour_rel,max_waypoints,deadline)->List[Dict[str,Any]]:
    chosen=[]; cur=[start]
    for cand in candidates:
        if time.monotonic()>deadline: break
        dist_km,_=ors_route_distance_and_geojson(cur+[(cand["lon"],cand["lat"])]+[end])
        detour_km=dist_km-direct_km; detour_ratio=detour_km/max(direct_km,1e-6)
        if detour_km<=detour_abs_km and detour_ratio<=detour_rel:
            chosen.append(cand); cur.insert(len(cur),(cand["lon"],cand["lat"]))
            if len(chosen)>=max_waypoints: break
        time.sleep(0.08)
    if not chosen and FORCE_ONE_WAYPOINT and candidates and time.monotonic()<=deadline:
        best=None; best_detour=1e9
        for cand in candidates:
            if time.monotonic()>deadline: break
            dist_km,_=ors_route_distance_and_geojson([start,(cand["lon"],cand["lat"]),end])
            d=dist_km-direct_km
            if d<best_detour: best_detour=d; best=cand
            time.sleep(0.05)
        if best: chosen=[best]
    return chosen

def nearest_distance_to_route_km(route_coords, pt)->float:
    best=1e9; step=max(1,len(route_coords)//10)   # 덜 촘촘히(부하↓)
    for lon,lat in route_coords[::step]:
        d=haversine_km((lon,lat),pt)
        if d<best: best=d
    return best

def collect_pois_along_route(route_coords, deadline)->Dict[str,Any]:
    seen={}; feats=[]
    step=max(1,len(route_coords)//8)  # 샘플 포인트 수 축소
    for i in range(0,len(route_coords),step):
        if time.monotonic()>deadline: break
        lon,lat=route_coords[i]
        for ctid in (12,39):
            items=tourapi_location_based(lon,lat,POI_RADIUS_M,ctid,num_rows=20)
            for it in items:
                try:
                    cid=str(it.get("contentid")); 
                    if not cid or cid in seen: continue
                    seen[cid]=True
                    intro=tourapi_detail_intro(cid,str(ctid)); it.update(intro)
                    poi_lon=float(it.get("mapx")); poi_lat=float(it.get("mapy"))
                    dist_km=nearest_distance_to_route_km(route_coords,(poi_lon,poi_lat))
                    props={"contentid":cid,"contenttypeid":str(ctid),"title":it.get("title",""),"addr1":it.get("addr1",""),"tel":it.get("tel",""),
                           "firstimage":it.get("firstimage",""),"parking":it.get("parking","") or it.get("parkingfood",""),
                           "opentime":it.get("opentime","") or it.get("opentimefood",""),"distance_km":dist_km}
                    props["popup_html"]=f"<b>{props['title']}</b>" + (f"<div style='margin:6px 0'><img src='{props['firstimage']}' style='width:220px;max-height:140px;object-fit:cover;border-radius:8px;border:1px solid #ddd'/></div>" if props["firstimage"] else "") + \
                                        (f"<br/>주소: {props['addr1']}" if props["addr1"] else "") + (f"<br/>전화: {props['tel']}" if props["tel"] else "") + \
                                        (f"<br/>영업시간: {props['opentime']}" if props["opentime"] else "") + (f"<br/>주차: {props['parking']}" if props["parking"] else "") + f"<br/>루트로부터 거리: {dist_km:.1f} km"
                    feats.append({"type":"Feature","geometry":{"type":"Point","coordinates":[poi_lon,poi_lat]},"properties":props})
                except: continue
        time.sleep(0.06)
    return {"type":"FeatureCollection","features":feats}

# ===== API =====
@app.route("/api/route", methods=["POST"])
def api_route():
    t0=time.monotonic(); deadline=t0+TIME_BUDGET_SEC
    data=request.get_json(force=True)
    origin=data.get("origin"); destination=data.get("destination")
    if not origin or not destination:
        return jsonify({"ok":False,"error":"origin/destination required"}), 400

    corridor_km=float(data.get("corridor_km",DEFAULT_CORRIDOR_KM))
    detour_abs_km=float(data.get("detour_abs_km",DEFAULT_DETOUR_ABS_KM))
    detour_rel=float(data.get("detour_rel",DEFAULT_DETOUR_REL))
    max_waypoints=max(1,min(3,int(data.get("max_waypoints",DEFAULT_MAX_WAYPOINTS))))

    def parse_point(obj):
        if isinstance(obj,dict) and "lon" in obj and "lat" in obj: return float(obj["lon"]), float(obj["lat"])
        elif isinstance(obj,str): return geocode_any(obj)
        else: raise ValueError("origin/destination must be address string or {'lon':..,'lat':..}")

    try:
        s_lon,s_lat=parse_point(origin); d_lon,d_lat=parse_point(destination)
    except Exception as e:
        return jsonify({"ok":False,"error":f"Geocoding/Parsing failed: {e}"}), 400

    start=(s_lon,s_lat); end=(d_lon,d_lat)

    try:
        direct_km,_=ors_route_distance_and_geojson([start,end])
    except Exception as e:
        return jsonify({"ok":False,"error":f"ORS direct route failed: {e}"}), 502

    try:
        cands=collect_beach_candidates_on_line(start,end,corridor_km,deadline)
    except Exception as e:
        cands=[]

    try:
        chosen=greedy_pick_waypoints(start,end,cands,direct_km,detour_abs_km,detour_rel,max_waypoints,deadline)
    except Exception as e:
        chosen=[]

    coords=[start]+[(c["lon"],c["lat"]) for c in chosen]+[end]
    final_km,route_geo=ors_route_distance_and_geojson(coords)
    detour_km=final_km-direct_km

    # 경로 좌표 간소 추출
    route_coords=[]
    try:
        for feat in route_geo.get("features",[]):
            if feat.get("geometry",{}).get("type")=="LineString":
                route_coords.extend(feat["geometry"]["coordinates"])
    except: pass
    route_coords=[(p[0],p[1]) for p in route_coords]

    pois_geo = collect_pois_along_route(route_coords, deadline) if time.monotonic()<=deadline else {"type":"FeatureCollection","features":[]}

    waypoints_geojson={"type":"FeatureCollection","features":[
        {"type":"Feature","geometry":{"type":"Point","coordinates":[c["lon"],c["lat"]]},
         "properties":{"kind":"beach_waypoint","order":i+1,"title":c["title"],"t":c["t"],"perp_km":c["dist_perp_km"],
                       "contentid":c["contentid"],"addr1":c.get("addr1",""),"tel":c.get("tel",""),"firstimage":c.get("firstimage","")}}
        for i,c in enumerate(chosen)
    ]}

    return jsonify({"ok":True,
        "summary":{"direct_km":round(direct_km,3),"final_km":round(final_km,3),"detour_km":round(detour_km,3),
                   "waypoints_count":len(chosen),"corridor_km":corridor_km,"detour_abs_km":detour_abs_km,"detour_rel":detour_rel},
        "route_geojson":route_geo,"waypoints_geojson":waypoints_geojson,"pois_geojson":pois_geo})

# ===== 루트: index.html 서빙 (파일 우선, 없으면 내장본) =====
@app.route("/")
def serve_index():
    try: return send_from_directory(".", "index.html")
    except Exception: return render_template_string(INDEX_HTML)

@app.route("/healthz")
def healthz(): return "OK"

if __name__=="__main__":
    port=int(os.environ.get("PORT","5000"))
    # 개발 로컬용; Render에서는 gunicorn으로 구동 권장
    app.run(host="0.0.0.0", port=port, debug=False)
