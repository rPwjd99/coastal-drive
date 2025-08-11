from flask import Flask, request, jsonify, render_template
import os
import requests
from dotenv import load_dotenv
from beaches_coordinates import beach_coords

load_dotenv()
app = Flask(__name__)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
ORS_API_KEY = os.getenv("ORS_API_KEY")
TOURAPI_KEY = "e1tU33wjMx2nynKjH8yDBm/S4YNne6B8mpCOWtzMH9TSONF71XG/xAwPqyv1fANpgeOvbPY+Le+gM6cYCnWV8w=="

def haversine(lat1, lon1, lat2, lon2):
    from math import radians, cos, sin, asin, sqrt
    R = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2)**2
    return 2 * R * asin(sqrt(a))

def geocode_google(address):
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    res = requests.get(url, params={"address": address, "key": GOOGLE_API_KEY})
    try:
        location = res.json()["results"][0]["geometry"]["location"]
        return location["lat"], location["lng"]
    except:
        return None

def reverse_geocode_google(lat, lon):
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    res = requests.get(url, params={"latlng": f"{lat},{lon}", "key": GOOGLE_API_KEY})
    try:
        return res.json()["results"][0]["formatted_address"]
    except:
        return "주소 불러오기 실패"

def is_in_coastal_bounds(lat, lon):
    return (
        (35 <= lat <= 38 and 128 <= lon <= 131) or
        (33 <= lat <= 35 and 126 <= lon <= 129) or
        (34 <= lat <= 38 and 124 <= lon <= 126)
    )

# ---------------------------
# 첫 번째 경유지: 기존 방식
# ---------------------------
def find_best_beach_waypoint(start, end):
    start_lat, start_lon = start
    end_lat, end_lon = end
    lat_candidates, lon_candidates = [], []
    for name, (lon, lat) in beach_coords.items():
        if not is_in_coastal_bounds(lat, lon):
            continue
        # 위도 정렬 + 진행방향 체크(경도 방향)
        if abs(lat - start_lat) < 0.2 and (end_lon - start_lon) * (lon - start_lon) > 0:
            lat_candidates.append((name, lat, lon, haversine(end_lat, end_lon, lat, lon)))
        # 경도 정렬 + 진행방향 체크(위도 방향)
        if abs(lon - start_lon) < 0.2 and (end_lat - start_lat) * (lat - start_lat) > 0:
            lon_candidates.append((name, lat, lon, haversine(end_lat, end_lon, lat, lon)))
    best_lat = min(lat_candidates, key=lambda x: x[3]) if lat_candidates else None
    best_lon = min(lon_candidates, key=lambda x: x[3]) if lon_candidates else None
    return best_lat if best_lat and (not best_lon or best_lat[3] < best_lon[3]) else best_lon

# ------------------------------------------
# 두 번째 경유지: "도착지 방향"에 있는 해수욕장
# ------------------------------------------
def find_second_beach_waypoint(first_wp, end):
    """
    first_wp: (name, lat, lon, dist_to_end)
    end: (lat, lon)
    규칙:
      1) first → dest 방향 벡터(vd)와 first → candidate 벡터(vc)의 내적이 양수(앞쪽)
      2) 각도 차(acos) 최소 우선
      3) 동률이면 first와의 거리 짧은 순
      4) 아무 후보도 없으면 '도착지에 더 가까운' 해수욕장(첫 경유지 제외) fallback
    """
    from math import radians, cos, sin, sqrt, acos

    if not first_wp:
        return None

    f_lat, f_lon = first_wp[1], first_wp[2]
    d_lat, d_lon = end

    # 위경도 단순 평면화 (경도에 cos(lat) 가중)
    def to_vec(ax_lat, ax_lon, bx_lat, bx_lon, ref_lat):
        k = cos(radians(ref_lat))
        return ((bx_lon - ax_lon) * k, (bx_lat - ax_lat))

    vd = to_vec(f_lat, f_lon, d_lat, d_lon, (f_lat + d_lat) / 2.0)

    def angle(u, v):
        ux, uy = u
        vx, vy = v
        nu = sqrt(ux*ux + uy*uy)
        nv = sqrt(vx*vx + vy*vy)
        if nu == 0 or nv == 0:
            return float("inf")
        dot = (ux*vx + uy*vy) / (nu*nv)
        dot = max(-1.0, min(1.0, dot))
        return acos(dot)

    candidates = []
    for name, (lon, lat) in beach_coords.items():
        if not is_in_coastal_bounds(lat, lon):
            continue
        if name == first_wp[0]:
            continue
        vc = to_vec(f_lat, f_lon, lat, lon, (f_lat + lat) / 2.0)
        # forward: 내적 > 0
        forward = vc[0]*vd[0] + vc[1]*vd[1]
        ang = angle(vc, vd)
        d_first = haversine(f_lat, f_lon, lat, lon)
        d_end = haversine(d_lat, d_lon, lat, lon)
        # 정렬키: 앞으로(내적) 큰 것 우선 => -forward, 각도(작은 값 우선), first와의 거리, end까지 거리
        candidates.append((-forward, ang, d_first, d_end, (name, lat, lon, d_end)))

    # 앞쪽/각도 기반 후보
    candidates.sort(key=lambda t: (t[0], t[1], t[2], t[3]))
    for _negfwd, _ang, _df, _de, packed in candidates:
        # 내적이 양수인 경우만 우선 채택
        if _negfwd < 0:
            return packed

    # fallback: 도착지에 더 가까운 순(첫 경유지 제외)
    fallback = []
    for name, (lon, lat) in beach_coords.items():
        if name == first_wp[0]:
            continue
        fallback.append((haversine(d_lat, d_lon, lat, lon), (name, lat, lon, haversine(d_lat, d_lon, lat, lon))))
    fallback.sort(key=lambda x: x[0])
    return fallback[0][1] if fallback else None

def get_ors_route(start, waypoint1, waypoint2, end):
    url = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
    headers = {"Authorization": ORS_API_KEY, "Content-Type": "application/json"}
    body = {
        "coordinates": [
            [start[1], start[0]],
            [waypoint1[2], waypoint1[1]],
            [waypoint2[2], waypoint2[1]],
            [end[1], end[0]]
        ]
    }
    res = requests.post(url, headers=headers, json=body)
    try:
        return res.json(), res.status_code
    except Exception as e:
        return {"error": str(e)}, 500

def search_tour_spots_along_route(geojson):
    coords = geojson['features'][0]['geometry']['coordinates']
    spots, seen_ids = [], set()
    for lon, lat in coords[::10]:
        try:
            url = "http://apis.data.go.kr/B551011/KorService1/locationBasedList1"
            params = {
                "serviceKey": TOURAPI_KEY,
                "mapX": lon,
                "mapY": lat,
                "radius": 5000,
                "listYN": "Y",
                "arrange": "E",
                "numOfRows": 10,
                "pageNo": 1,
                "MobileOS": "ETC",
                "MobileApp": "SeaRoute",
                "_type": "json"
            }
            res = requests.get(url, params=params)
            items = res.json().get("response", {}).get("body", {}).get("items", {}).get("item", [])
            for item in items:
                cid = item.get("contentid")
                if cid and cid not in seen_ids:
                    seen_ids.add(cid)
                    spots.append({
                        "title": item.get("title"),
                        "addr1": item.get("addr1"),
                        "mapx": item.get("mapx"),
                        "mapy": item.get("mapy"),
                        "firstimage": item.get("firstimage"),
                        "homepage": item.get("homepage", ""),
                        # 있으면 보여주고, 없으면 빈값
                        "parking": item.get("parking", ""),
                        "usetime": item.get("usetime", "")
                    })
        except:
            continue
    return spots

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/route", methods=["POST"])
def route():
    try:
        data = request.get_json()
        start = geocode_google(data.get("start"))
        end = geocode_google(data.get("end"))
        if not start or not end:
            return jsonify({"error": "❌ 주소 변환 실패"}), 400

        # 1) 첫 번째 경유지: 기존 로직
        waypoint1 = find_best_beach_waypoint(start, end)
        if not waypoint1:
            return jsonify({"error": "❌ 1번 경유지 탐색 실패"}), 500

        # 2) 두 번째 경유지: 도착지 방향
        waypoint2 = find_second_beach_waypoint(waypoint1, end)
        if not waypoint2:
            return jsonify({"error": "❌ 2번 경유지 탐색 실패"}), 500

        route_data, status = get_ors_route(start, waypoint1, waypoint2, end)
        if "error" in route_data:
            return jsonify({"error": route_data["error"]}), status

        spots = search_tour_spots_along_route(route_data)

        waypoint1_addr = reverse_geocode_google(waypoint1[1], waypoint1[2])
        waypoint2_addr = reverse_geocode_google(waypoint2[1], waypoint2[2])

        return jsonify({
            "route": route_data,
            "waypoint1": {
                "name": waypoint1[0],
                "lat": waypoint1[1],
                "lon": waypoint1[2],
                "address": waypoint1_addr
            },
            "waypoint2": {
                "name": waypoint2[0],
                "lat": waypoint2[1],
                "lon": waypoint2[2],
                "address": waypoint2_addr
            },
            "spots": spots or []
        })
    except Exception as e:
        return jsonify({"error": f"❌ 서버 오류: {str(e)}"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
