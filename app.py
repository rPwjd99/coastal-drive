# app.py
# Flask backend for Coastal Drive route + TourAPI POIs
# Requirements: Flask==3.0.3, flask-cors==5.0.1, requests==2.32.3, python-dotenv>=1,<2
# NOTE: 좌표는 모두 WGS84 (EPSG:4326), NAVER API는 "lng,lat" 순서입니다.

import os
import json
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv
import requests

load_dotenv()

NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID", "").strip()
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET", "").strip()
TOURAPI_KEY = os.getenv("TOURAPI_KEY", "").strip()

# CORS 설정
ALLOWED_ORIGIN = os.getenv("ALLOWED_ORIGIN", "").strip()  # 예: https://rpwjd99.github.io
cors_resources = {
    r"/api/*": {"origins": ALLOWED_ORIGIN or "*"},
    r"/health": {"origins": ALLOWED_ORIGIN or "*"},
    r"/": {"origins": ALLOWED_ORIGIN or "*"},
}

app = Flask(__name__, static_folder=".", static_url_path="")
CORS(app, resources=cors_resources)


# ------------------------
# 유틸
# ------------------------
def naver_headers():
    if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
        raise RuntimeError("NAVER API 키 누락: NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 를 .env에 설정하세요.")
    return {
        "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID,
        "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET,
    }


def json_error(message, status=400, extra=None):
    payload = {"ok": False, "error": message}
    if extra:
        payload["detail"] = extra
    return jsonify(payload), status


# ------------------------
# 라우트
# ------------------------
@app.get("/health")
def health():
    return jsonify({"ok": True, "status": "healthy"})


@app.get("/")
def root():
    # GitHub Pages에서 index.html을 사용한다면 이 엔드포인트는 없어도 됩니다.
    # Flask 단독 구동시 같은 디렉토리의 index.html을 서빙합니다.
    return send_from_directory(".", "index.html")


@app.post("/api/geocode")
def geocode():
    """
    body: { "query": "세종특별자치시청" }
    return: { ok, lat, lng, address, roadAddress }
    """
    try:
        data = request.get_json(force=True, silent=True) or {}
        query = (data.get("query") or "").strip()
        if not query:
            return json_error("query 파라미터가 비었습니다.", 422)

        url = "https://naveropenapi.apigw.ntruss.com/map-geocode/v2/geocode"
        res = requests.get(url, headers=naver_headers(), params={"query": query}, timeout=12)
        if res.status_code != 200:
            return json_error("NAVER 지오코딩 요청 실패", 502, {"status_code": res.status_code, "text": res.text})

        j = res.json()
        addrs = j.get("addresses", [])
        if not addrs:
            return json_error("검색 결과가 없습니다.", 404, {"query": query})

        a0 = addrs[0]
        lng = float(a0.get("x"))
        lat = float(a0.get("y"))
        return jsonify({
            "ok": True,
            "lat": lat,
            "lng": lng,
            "address": a0.get("jibunAddress") or a0.get("address") or query,
            "roadAddress": a0.get("roadAddress"),
        })
    except Exception as e:
        return json_error("지오코딩 처리 중 오류", 500, {"exc": str(e)})


@app.post("/api/route")
def route():
    """
    body:
    {
      "start": {"lat": 37.5, "lng": 127.0},
      "end":   {"lat": 38.2, "lng": 128.6},
      "waypoints": [{"lat": 37.9, "lng": 128.0}]  # optional, 여러개 가능
    }
    return: { ok, geometry(LineString), summary(distance, duration) }
    """
    try:
        data = request.get_json(force=True, silent=True) or {}
        s = data.get("start") or {}
        g = data.get("end") or {}
        wps = data.get("waypoints") or []

        try:
            s_lng, s_lat = float(s["lng"]), float(s["lat"])
            g_lng, g_lat = float(g["lng"]), float(g["lat"])
        except Exception:
            return json_error("start/end 의 lat,lng가 올바르지 않습니다.", 422)

        params = {
            "start": f"{s_lng},{s_lat}",  # lng,lat
            "goal": f"{g_lng},{g_lat}",   # lng,lat
            "option": "trafast"
        }

        if wps:
            # "lng,lat|lng,lat" 형태
            wp_str = "|".join(f'{float(w["lng"])},{float(w["lat"])}' for w in wps if "lng" in w and "lat" in w)
            if wp_str:
                params["waypoints"] = wp_str

        url = "https://naveropenapi.apigw.ntruss.com/map-direction/v1/driving"
        res = requests.get(url, headers=naver_headers(), params=params, timeout=20)

        if res.status_code != 200:
            return json_error("NAVER 길찾기 요청 실패", 502, {"status_code": res.status_code, "text": res.text})

        j = res.json()
        route_obj = None
        for key in ("trafast", "tracomfort", "traoptimal"):
            cand = (j.get("route") or {}).get(key) or []
            if cand:
                route_obj = cand[0]
                break

        if not route_obj:
            return json_error("유효한 경로가 없습니다.", 404, {"api_response": j})

        path = route_obj.get("path") or []
        if not path:
            return json_error("경로 좌표가 비어있습니다.", 404)

        # NAVER path: [[lng,lat], ...]
        coords = [[float(p[0]), float(p[1])] for p in path]
        geometry = {"type": "LineString", "coordinates": coords}
        summary = route_obj.get("summary", {})
        return jsonify({"ok": True, "geometry": geometry, "summary": summary})
    except Exception as e:
        return json_error("경로 계산 중 오류", 500, {"exc": str(e)})


@app.get("/api/tourspot")
def tourspot():
    """
    /api/tourspot?lat=37.5&lng=127.0&radius=5000&contentTypeId=12,39
    - contentTypeId 생략 시 12(관광지)와 39(음식) 동시 조회
    return: GeoJSON FeatureCollection (관광지/맛집 POIs)
    """
    try:
        if not TOURAPI_KEY:
            return json_error("TOURAPI_KEY 누락: .env에 TOURAPI_KEY를 설정하세요.", 500)

        lat = request.args.get("lat", type=float)
        lng = request.args.get("lng", type=float)
        radius = request.args.get("radius", default=5000, type=int)
        cids = (request.args.get("contentTypeId") or "12,39").split(",")

        if lat is None or lng is None:
            return json_error("lat/lng 파라미터가 필요합니다.", 422)

        base = "https://apis.data.go.kr/B551011/KorService1/locationBasedList1"
        features = []

        for cid in [c.strip() for c in cids if c.strip()]:
            params = {
                "serviceKey": TOURAPI_KEY,
                "mapX": f"{lng}",  # TourAPI: mapX=경도(lng), mapY=위도(lat)
                "mapY": f"{lat}",
                "radius": radius,
                "contentTypeId": cid,   # 12=관광지, 39=음식
                "listYN": "Y",
                "MobileOS": "ETC",
                "MobileApp": "CoastalDrive",
                "arrange": "E",
                "numOfRows": 100,
                "pageNo": 1,
                "_type": "json",
            }
            r = requests.get(base, params=params, timeout=15)
            if r.status_code != 200:
                # 타입 하나 실패해도 나머지는 진행
                continue
            jr = r.json()
            item = (((jr.get("response") or {}).get("body") or {}).get("items") or {}).get("item") or []
            if isinstance(item, dict):
                item = [item]

            for it in item:
                try:
                    lon = float(it.get("mapx"))
                    lat2 = float(it.get("mapy"))
                except Exception:
                    continue
                props = {
                    "title": it.get("title"),
                    "addr1": it.get("addr1"),
                    "firstimage": it.get("firstimage"),
                    "tel": it.get("tel"),
                    "contentTypeId": int(cid) if cid.isdigit() else cid,
                    "contentid": it.get("contentid"),
                }
                features.append({
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [lon, lat2]},
                    "properties": props
                })

        return jsonify({
            "ok": True,
            "type": "FeatureCollection",
            "features": features
        })
    except Exception as e:
        return json_error("TourAPI 조회 중 오류", 500, {"exc": str(e)})


if __name__ == "__main__":
    # 실행 명령:
    # python app.py
    # 또는 배포 시:
    # gunicorn -w 2 -b 0.0.0.0:${PORT:-5000} app:app
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
