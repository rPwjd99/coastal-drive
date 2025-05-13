from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import geopandas as gpd
import requests
import os
from shapely.geometry import Point

app = Flask(__name__)
CORS(app)

# 경로 설정
COASTLINE_PATH = "coastal_route_result.geojson"
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID", "vsdzf1f4n5")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET", "0gzctO51PUTVv0gUZU025JYNHPTmVzLS9sGbfYBM")

# GeoJSON 로딩 (최초 1회)
coastline = gpd.read_file(COASTLINE_PATH)
coastline = coastline.to_crs(epsg=4326)  # EPSG:4326 보장
coastline["centroid"] = coastline.geometry.centroid

# 주소 → 좌표 변환 (Google Geocode API 사용 가능)
def geocode_address(address):
    try:
        url = f"https://maps.googleapis.com/maps/api/geocode/json?address={address}&key=YOUR_GOOGLE_API_KEY"
        response = requests.get(url)
        data = response.json()
        if data['status'] == 'OK':
            location = data['results'][0]['geometry']['location']
            return location['lat'], location['lng']
    except Exception as e:
        print("❌ 주소 변환 실패:", e)
    return None, None

# 네이버 Directions API 호출 함수
def get_naver_route(start, waypoint, end):
    url = "https://naveropenapi.apigw.ntruss.com/map-direction/v1/driving"
    headers = {
        "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID,
        "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET
    }
    params = {
        "start": f"{start[1]},{start[0]}",
        "goal": f"{end[1]},{end[0]}",
        "waypoints": f"{waypoint[1]},{waypoint[0]}",
        "option": "traoptimal"
    }
    try:
        res = requests.get(url, headers=headers, params=params)
        return res.json()
    except Exception as e:
        print("❌ 네이버 경로 탐색 실패:", e)
        return None

# 해안선 경유지 탐색 함수 (위도/경도 기준)
def find_best_waypoint(start_lat, start_lon, end_lat, end_lon):
    try:
        centroid_df = coastline.copy()
        centroid_df = centroid_df[centroid_df['centroid'].geom_type == 'Point']
        centroid_df['distance_lat'] = (centroid_df.centroid.y - start_lat).abs()
        centroid_df['distance_lon'] = (centroid_df.centroid.x - start_lon).abs()

        # 방향성 반영 (위도 또는 경도 기준 선택)
        lat_candidate = centroid_df.sort_values("distance_lat").iloc[0]
        lon_candidate = centroid_df.sort_values("distance_lon").iloc[0]

        d_lat = abs(lat_candidate.centroid.y - start_lat) + abs(lat_candidate.centroid.y - end_lat)
        d_lon = abs(lon_candidate.centroid.x - start_lon) + abs(lon_candidate.centroid.x - end_lon)

        best = lat_candidate if d_lat < d_lon else lon_candidate
        return best.centroid.y, best.centroid.x

    except Exception as e:
        print("❌ 해안 경유지 탐색 실패:", e)
        return None

@app.route('/')
def index():
    return render_template("index.html")

@app.route('/route', methods=['POST'])
def route():
    try:
        data = request.get_json(force=True)
        print("📥 받은 요청:", data)

        start_address = data.get("start")
        end_address = data.get("end")
        if not start_address or not end_address:
            return jsonify({"error": "출발지 또는 목적지가 입력되지 않았습니다."}), 400

        start = geocode_address(start_address)
        end = geocode_address(end_address)
        if None in start or None in end:
            return jsonify({"error": "주소 → 좌표 변환 실패"}), 400

        waypoint = find_best_waypoint(start[0], start[1], end[0], end[1])
        if waypoint is None:
            return jsonify({"error": "❌ 해안 경유지 탐색 실패"}), 404

        route_data = get_naver_route(start, waypoint, end)
        if not route_data or 'route' not in route_data:
            return jsonify({"error": "❌ 경로 탐색 실패"}), 500

        return jsonify(route_data)

    except Exception as e:
        print("❌ 서버 처리 중 예외 발생:", e)
        return jsonify({"error": f"❌ 서버 오류: {str(e)}"}), 500

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=10000)
