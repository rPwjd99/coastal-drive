import os
import requests
import pandas as pd
import geopandas as gpd
from flask import Flask, request, jsonify, render_template
from shapely.geometry import Point, LineString
from math import radians, cos, sin, asin, sqrt
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
ORS_API_KEY = os.getenv("ORS_API_KEY")

print("🔑 ORS 키 앞:", ORS_API_KEY[:6] if ORS_API_KEY else "❌ 없음", flush=True)

# 파일 경로
COASTLINE_PATH = os.path.join(os.path.dirname(__file__), "coastal_route_result.geojson")
ROAD_CSV_PATH = os.path.join(os.path.dirname(__file__), "road_endpoints_reduced.csv")

# 해안선 vertex 추출
try:
    coastline_gdf = gpd.read_file(COASTLINE_PATH).to_crs(epsg=4326)
    coastline_vertices = []

    for geom in coastline_gdf.geometry:
        if geom.geom_type == 'LineString':
            coords = list(geom.coords)
            coastline_vertices.extend(coords)
        elif geom.geom_type == 'MultiLineString':
            for line in geom:
                coords = list(line.coords)
                coastline_vertices.extend(coords)

    print(f"📍 해안선 vertex 개수: {len(coastline_vertices)}", flush=True)
except Exception as e:
    print("❌ 해안선 로딩 오류:", str(e), flush=True)
    coastline_vertices = []

# 도로 점 로드
try:
    road_points = pd.read_csv(ROAD_CSV_PATH, low_memory=False)
except Exception as e:
    print("❌ 도로 CSV 로딩 실패:", str(e), flush=True)
    road_points = pd.DataFrame()

# 거리 계산 함수
def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
    return 2 * R * asin(sqrt(a))

# 주소 → 좌표
def geocode_google(address):
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    res = requests.get(url, params={"address": address, "key": GOOGLE_API_KEY})
    try:
        location = res.json()["results"][0]["geometry"]["location"]
        print("📍 주소 변환 성공:", address, "→", location, flush=True)
        return location["lat"], location["lng"]
    except:
        print("❌ 주소 변환 실패:", address, flush=True)
        return None

# 해안 근접 도로점 → waypoint 선정
def find_best_waypoint(start, end):
    if road_points.empty or not coastline_vertices:
        print("❌ 데이터 없음", flush=True)
        return None

    # 해안선 5km 이내 도로점 필터링
    def near_coast(row):
        for lon, lat in coastline_vertices:
            if haversine(row["y"], row["x"], lat, lon) <= 5:
                return True
        return False

    coastal_candidates = road_points[road_points.apply(near_coast, axis=1)].copy()
    print(f"📍 해안 근접 도로 후보: {len(coastal_candidates)}", flush=True)

    if coastal_candidates.empty:
        print("❌ 조건에 맞는 해안도로 없음", flush=True)
        return None

    # 최종목적지 방향성과 거리 기준으로 선택
    start_lat, start_lon = start
    end_lat, end_lon = end
    lat_diff = abs(start_lat - end_lat)
    lon_diff = abs(start_lon - end_lon)
    use_lat = lat_diff > lon_diff

    def is_in_direction(row):
        if use_lat:
            return (end_lon - start_lon) * (row["x"] - start_lon) > 0
        else:
            return (end_lat - start_lat) * (row["y"] - start_lat) > 0

    candidates = coastal_candidates[coastal_candidates.apply(is_in_direction, axis=1)]

    if candidates.empty:
        return None

    candidates["dir_diff"] = abs(candidates["y"] - start_lat) if use_lat else abs(candidates["x"] - start_lon)
    candidates["dist_to_end"] = candidates.apply(
        lambda row: haversine(row["y"], row["x"], end_lat, end_lon), axis=1
    )
    best = candidates.sort_values(["dir_diff", "dist_to_end"]).iloc[0]
    print("📍 선택된 waypoint:", best["y"], best["x"], flush=True)
    return best["y"], best["x"]

# ORS 경로 요청
def get_ors_route(start, waypoint, end):
    url = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
    headers = {
        "Authorization": ORS_API_KEY,
        "Content-Type": "application/json"
    }
    body = {
        "coordinates": [
            [start[1], start[0]],
            [waypoint[1], waypoint[0]],
            [end[1], end[0]]
        ]
    }
    res = requests.post(url, headers=headers, json=body)
    print("📡 ORS 응답코드:", res.status_code, flush=True)
    try:
        return res.json(), res.status_code
    except Exception as e:
        return {"error": str(e)}, 500

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

        waypoint = find_best_waypoint(start, end)
        if not waypoint:
            return jsonify({"error": "❌ 해안 경유지 탐색 실패"}), 500

        route_data, status = get_ors_route(start, waypoint, end)
        if "error" in route_data:
            return jsonify({"error": route_data.get("error")}), status
        return jsonify(route_data)
    except Exception as e:
        print("❌ 서버 오류:", str(e), flush=True)
        return jsonify({"error": f"❌ 서버 내부 오류: {str(e)}"}), 500

# ✅ Render 포트 설정
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print("✅ 실행 포트:", port, flush=True)
    app.run(host="0.0.0.0", port=port)
