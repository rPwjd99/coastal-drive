# app.py
# Flask 백엔드: / (index), /api/route (경로 계산), /route (호환용 별칭)
# - JSON/폼/URL-encoded 모두 파싱
# - NAVER Geocoding + Directions 5 연동
# - /route → /api/route와 동일 동작
# 주의: 환경변수에 키를 넣고 Render 대시보드에 등록하세요.
#   NAVER_CLIENT_ID, NAVER_CLIENT_SECRET  (NAVER Maps)
#   TOURAPI_KEY    (선택: 관광/맛집 API 사용 시)
# 실행: (Windows) set PORT=10000 && python app.py
#       (macOS/Linux) export PORT=10000 && python app.py

import os
import json
import logging
from typing import Any, Dict, List, Optional, Tuple, Union

import requests
from flask import Flask, request, jsonify, send_from_directory, redirect

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("coastal-drive")

NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")

GEOCODE_URL = "https://naveropenapi.apigw.ntruss.com/map-geocode/v2/geocode"
# Directions 5: 문서 예시 엔드포인트는 아래와 같습니다.
DIRECTIONS_URL = "https://naveropenapi.apigw.ntruss.com/map-direction/v1/driving"

# 프런트엔드에서 간혹 "route"로 보낼 수 있으므로 동일 동작 별칭 제공
ROUTE_ALIASES = ["/route", "/api/route"]

app = Flask(__name__, static_folder="static")


def _json_error(message: str, status: int = 400, **extra):
    payload = {"ok": False, "error": message}
    if extra:
        payload.update(extra)
    return jsonify(payload), status


def _get_json() -> Dict[str, Any]:
    """
    요청 본문을 유연하게 파싱:
    - JSON(Content-Type 없어도) → request.get_json(silent=True, force=True)
    - 폼(request.form), 쿼리(request.args)도 보조
    """
    data = request.get_json(silent=True, force=True)
    if isinstance(data, dict):
        return data

    # 폼이나 쿼리로 들어온 경우 key-value → dict
    if request.form:
        return {k: request.form.get(k) for k in request.form.keys()}
    if request.args:
        return {k: request.args.get(k) for k in request.args.keys()}
    return {}


def _pick_first(*vals):
    for v in vals:
        if v is not None:
            return v
    return None


def _as_str(v: Any) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip()
        return s if s else None
    return None


def _is_number(x: Any) -> bool:
    try:
        float(x)
        return True
    except Exception:
        return False


def _parse_coord(value: Any) -> Optional[Tuple[float, float]]:
    """
    다양한 형태의 좌표 입력 허용:
    - [lon, lat] 또는 [lat, lon] (경도/위도 순서 자동 추정)
    - {"lat": .., "lng": ..} or {"latitude": .., "longitude": ..}
    반환: (lon, lat)
    """
    if isinstance(value, (list, tuple)) and len(value) == 2 and all(_is_number(v) for v in value):
        a, b = float(value[0]), float(value[1])
        # 한국 권역 경도는 120~132 근방, 위도는 33~39 근방 → 대략적 판별
        # a가 100 이상이면 경도일 확률이 큼 → (lon, lat) = (a, b)
        if abs(a) > 90 or abs(b) <= 90:
            return (a, b)
        # 그 외 경우 [lat, lon]로 들어왔다고 가정
        return (b, a)

    if isinstance(value, dict):
        lat = value.get("lat") or value.get("latitude")
        lon = value.get("lng") or value.get("lon") or value.get("longitude") or value.get("x")
        if _is_number(lat) and _is_number(lon):
            return (float(lon), float(lat))
    return None


def _naver_headers() -> Dict[str, str]:
    if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
        raise RuntimeError("NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 환경변수를 설정하세요.")
    return {
        "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID,
        "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET,
        "Accept": "application/json",
    }


def geocode_address(address: str) -> Tuple[float, float]:
    """
    주소 → 좌표(lon, lat) 변환
    - 주 엔드포인트: /map-geocode/v2/geocode?query=...
    - 응답 구조: addresses[0].x, addresses[0].y (경/위도) 또는 result.items[0].point.x/y (레거시)
    """
    params = {"query": address}
    r = requests.get(GEOCODE_URL, headers=_naver_headers(), params=params, timeout=15)
    if r.status_code != 200:
        raise RuntimeError(f"Geocoding 실패({r.status_code}): {r.text}")
    j = r.json()

    # 신규 형태
    if isinstance(j, dict) and "addresses" in j and j["addresses"]:
        x = j["addresses"][0].get("x")
        y = j["addresses"][0].get("y")
        if _is_number(x) and _is_number(y):
            return (float(x), float(y))

    # JS 튜토리얼 계열(레거시) 대응
    if "result" in j and j["result"].get("items"):
        pt = j["result"]["items"][0].get("point", {})
        x = pt.get("x")
        y = pt.get("y")
        if _is_number(x) and _is_number(y):
            return (float(x), float(y))

    raise RuntimeError(f"주소를 좌표로 변환하지 못했습니다: {address}")


def ensure_coord(val: Union[str, Dict, List]) -> Tuple[float, float]:
    """
    입력이 주소 문자열이면 지오코딩, 좌표형이면 좌표로 통일 (lon, lat)
    """
    # 좌표형?
    coord = _parse_coord(val)
    if coord:
        return coord

    # 주소형?
    s = _as_str(val)
    if s:
        return geocode_address(s)

    raise RuntimeError("좌표/주소 입력 형식이 올바르지 않습니다.")


def call_naver_directions(start_ll: Tuple[float, float],
                          goal_ll: Tuple[float, float],
                          waypoints_ll: Optional[List[Tuple[float, float]]] = None,
                          option: str = "traoptimal") -> Dict[str, Any]:
    """
    네이버 길찾기 Directions 5 호출
    - 엔드포인트: /map-direction/v1/driving
    - 파라미터: start=lon,lat / goal=lon,lat / waypoints=lon,lat|lon,lat ...
    - option: trafast/traoptimal/tracomfort 등
    반환: {'path': [[lon,lat], ...], 'summary': {...}, 'profile_key': 'trafast|traoptimal|...'}
    """
    params = {
        "start": f"{start_ll[0]},{start_ll[1]}",
        "goal": f"{goal_ll[0]},{goal_ll[1]}",
        "option": option,
    }
    if waypoints_ll:
        wp = "|".join([f"{w[0]},{w[1]}" for w in waypoints_ll])
        params["waypoints"] = wp

    r = requests.get(DIRECTIONS_URL, headers=_naver_headers(), params=params, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"Directions 실패({r.status_code}): {r.text}")

    data = r.json()
    route = data.get("route") or {}
    # 기본이 traoptimal, 상황에 따라 trafast, tracomfort 존재
    for key in ("traoptimal", "trafast", "tracomfort"):
        if key in route and isinstance(route[key], list) and route[key]:
            item = route[key][0]
            path = item.get("path") or []
            summary = item.get("summary") or {}
            if not path:
                raise RuntimeError("경로 path가 비었습니다.")
            return {"path": path, "summary": summary, "profile_key": key}

    raise RuntimeError("유효한 경로를 찾지 못했습니다.")


def to_geojson_line(path: List[List[float]]) -> Dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "geometry": {"type": "LineString", "coordinates": path},
             "properties": {"name": "route"}}
        ],
    }


@app.route("/", methods=["GET"])
def index():
    # 리포에 index.html이 루트에 있다면 그대로 서빙
    return send_from_directory(".", "index.html")


@app.route("/healthz", methods=["GET"])
def health():
    return jsonify({"ok": True})


# /api/route 및 /route 모두 허용
@app.route("/api/route", methods=["POST"])
def api_route():
    try:
        data = _get_json()
        if not data:
            return _json_error("본문이 비었습니다. JSON 또는 form-data로 'origin'과 'destination'을 보내세요.", 400)

        # 키 이름 유연 처리
        origin = _pick_first(
            data.get("origin"), data.get("start"), data.get("from"), data.get("departure")
        )
        destination = _pick_first(
            data.get("destination"), data.get("end"), data.get("to"), data.get("arrival")
        )
        waypoints = data.get("waypoints") or data.get("vias") or data.get("via")

        if not origin or not destination:
            return _json_error("필수 입력 누락: origin/start, destination/end", 422, received=list(data.keys()))

        # 좌표/주소 자동 처리
        start_ll = ensure_coord(origin)
        goal_ll = ensure_coord(destination)

        wps: Optional[List[Tuple[float, float]]] = None
        if waypoints:
            wps = []
            # 문자열 1개 또는 배열 지원
            if isinstance(waypoints, str):
                items = [s for s in waypoints.split("|") if s.strip()]
            elif isinstance(waypoints, list):
                items = waypoints
            else:
                items = [waypoints]
            for w in items:
                wps.append(ensure_coord(w))

        # 네이버 길찾기 호출
        result = call_naver_directions(start_ll, goal_ll, wps)
        path = result["path"]
        summary = result["summary"]
        profile = result["profile_key"]

        # GeoJSON으로 응답
        return jsonify({
            "ok": True,
            "profile": profile,
            "distance_m": summary.get("distance"),
            "duration_s": summary.get("duration"),
            "tollFare": summary.get("tollFare"),
            "fuelPrice": summary.get("fuelPrice"),
            "route_geojson": to_geojson_line(path),
            "start": {"lon": start_ll[0], "lat": start_ll[1]},
            "goal": {"lon": goal_ll[0], "lat": goal_ll[1]},
            "waypoints_used": [{"lon": w[0], "lat": w[1]} for w in (wps or [])],
        }), 200

    except Exception as e:
        logger.exception("Route error")
        return _json_error(str(e), 500)


# /route → /api/route 로 완전 동일 동작(별칭)
@app.route("/route", methods=["POST"])
def route_alias():
    return api_route()


if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
