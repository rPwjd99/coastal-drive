from flask import Flask, request, jsonify, render_template
import requests
import geopandas as gpd
from shapely.geometry import Point
import os

app = Flask(__name__)

# API KEY 설정
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")

# 해안선 파일 경로
COASTLINE_PATH = "coastal_route_result.geojson"

# 해안선 데이터 미리 로딩
def load_coastline():
    gdf = gpd.read_file(COASTLINE_PATH)
    if gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(epsg=4326)
    gdf = gdf[gdf.geometry.type == 'Point']
    gdf["lat"] = gdf.geometry.y
    gdf["lon"] = gdf.geometry.x
    return gdf

coastline = load_coastline()

# 주소 -> 좌표 (Google)
def geocode_google(address):
    try:
        url = f"https://maps.googleapis.com/maps/api/geocode/json?address={address}&key={GOOGLE_API_KEY}"
        res = requests.get(url)
        res.raise_for_status()
        data = res.json()
        location = data['results'][0]['geometry']['location']
        return location['lat'], location['lng']
    except Exception as e:
        print("❌ 구글 주소 변환 실패:", e)
        return None

# 경로 계산 (Naver)
def get_naver_route(start, waypoint, end):
    try:
        headers = {
            "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID,
            "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET,
            "Content-Type": "application/json",
        }
        body = {
            "start": {"x": str(start[1]), "y": str(start[0]), "name": "출발"},
            "goal": {"x": str(end[1]), "y": str(end[0]), "name": "도착"},
            "waypoints": [{"x": str(waypoint[1]), "y": str(waypoint[0]), "name": "경유"}],
            "option": "traoptimal"
        }
        res = requests.post("https://naveropenapi.apigw.ntruss.com/map-direction/v1/driving", headers=headers, json=body)
        res.raise_for_status()
        return res.json()
    except Exception as e:
        print("❌ 네이버 경로 탐색 실패:", e)
        return None

# 가장 가까운 경유지 찾기
def find_nearest_waypoint(start_lat, start_lon):
    try:
        ref_point = Point(start_lon, start_lat)
        coastline["distance"] = coastline.geometry.distance(ref_point)
        nearest = coastline.sort_values("distance").iloc[0]
        return nearest["lat"], nearest["lon"]
    except Exception as e:
        print("❌ 경유지 탐색 실패:", e)
        return None

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/route", methods=["POST"])
def route():
    try:
        data = request.get_json()
        origin = data.get("origin")
        destination = data.get("destination")

        if not origin or not destination:
            return jsonify({"error": "출발지와 목적지를 입력하세요."}), 400

        start = geocode_google(origin)
        end = geocode_google(destination)

        if not start or not end:
            return jsonify({"error": "❌ 주소 변환 실패"}), 500

        waypoint = find_nearest_waypoint(start[0], start[1])

        if not waypoint:
            return jsonify({"error": "❌ 해안 경유지 탐색 실패"}), 404

        route_data = get_naver_route(start, waypoint, end)

        if not route_data or 'route' not in route_data:
            return jsonify({"error": "❌ 경로 탐색 실패"}), 500

        return jsonify(route_data)

    except Exception as e:
        print("❌ 서버 오류:", e)
        return jsonify({"error": f"❌ 서버 오류: {str(e)}"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
