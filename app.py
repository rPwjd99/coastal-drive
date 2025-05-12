from flask import Flask, render_template, request, jsonify
import geopandas as gpd
from shapely.geometry import Point
import requests
import os

app = Flask(__name__)

# 경로 설정
COASTLINE_PATH = "coastal_route_result.geojson"
TARGET_CRS = "EPSG:5179"

# NAVER API
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID") or "vsdzf1f4n5"
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET") or "0gzctO51PUTVv0gUZU025JYNHPTmVzLS9sGbfYBM"

# 해안선 데이터 로드
coastline_gdf = gpd.read_file(COASTLINE_PATH)
coastline_gdf = coastline_gdf.to_crs(TARGET_CRS)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/route', methods=['POST'])
def route():
    data = request.json
    start = data['start']
    end = data['end']

    try:
        start_lat, start_lon = float(start['lat']), float(start['lon'])
        end_lat, end_lon = float(end['lat']), float(end['lon'])
    except Exception as e:
        return jsonify({'error': f"❌ 잘못된 좌표 형식: {e}"}), 400

    waypoint = find_nearest_accessible_coast(start_lon, start_lat, end_lon, end_lat)
    if not waypoint:
        return jsonify({'error': "❌ 해안 경유지 탐색 실패"}), 500

    route_data = get_naver_route(start=(start_lat, start_lon), waypoint=waypoint, end=(end_lat, end_lon))
    if route_data:
        return jsonify({'route': route_data, 'waypoint': {'lat': waypoint[0], 'lon': waypoint[1]}})
    else:
        return jsonify({'error': "❌ 경로 계산 실패"}), 500

def find_nearest_accessible_coast(start_lon, start_lat, end_lon, end_lat):
    start_pt = Point(start_lon, start_lat)
    end_pt = Point(end_lon, end_lat)
    start_proj = gpd.GeoSeries([start_pt], crs="EPSG:4326").to_crs(TARGET_CRS).iloc[0]
    end_proj = gpd.GeoSeries([end_pt], crs="EPSG:4326").to_crs(TARGET_CRS).iloc[0]

    candidates = coastline_gdf.copy()
    candidates["dist_to_start"] = candidates.geometry.distance(start_proj)
    candidates["dist_to_end"] = candidates.geometry.distance(end_proj)
    candidates["score"] = candidates["dist_to_start"] + candidates["dist_to_end"]
    top_candidates = candidates.sort_values("score").head(20)

    for idx, row in top_candidates.iterrows():
        coord = row.geometry.centroid
        lon, lat = gpd.GeoSeries([coord], crs=TARGET_CRS).to_crs("EPSG:4326").iloc[0].xy
        waypoint = (lat[0], lon[0])
        if test_route((start_lat, start_lon), waypoint, (end_lat, end_lon)):
            return waypoint
    return None

def test_route(start, waypoint, end):
    headers = {
        "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID,
        "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET,
        "Content-Type": "application/json"
    }
    data = {
        "start": {"x": str(start[1]), "y": str(start[0]), "name": "출발"},
        "goal": {"x": str(end[1]), "y": str(end[0]), "name": "도착"},
        "waypoints": [{"x": str(waypoint[1]), "y": str(waypoint[0]), "name": "해안"}],
        "option": "trafast"
    }
    try:
        res = requests.post("https://naveropenapi.apigw.ntruss.com/map-direction/v1/driving",
                            headers=headers, json=data)
        if res.status_code == 200 and "route" in res.json():
            return res.json()
    except Exception as e:
        print("❌ NAVER 경로 요청 실패:", e)
    return None

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port, debug=True)
