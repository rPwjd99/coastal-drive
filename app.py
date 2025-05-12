import os
import json
import requests
import geopandas as gpd
import pandas as pd
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID", "vsdzf1f4n5")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET", "0gzctO51PUTVv0gUZU025JYNHPTmVzLS9sGbfYBM")

# -------------------- 기본 도구 --------------------
def geocode_google(address):
    url = f"https://maps.googleapis.com/maps/api/geocode/json?address={address}&key=AIzaSyC9MSD-WhkqK_Og5YdVYfux21xiRjy2q1M"
    res = requests.get(url)
    data = res.json()
    if data['status'] == 'OK':
        location = data['results'][0]['geometry']['location']
        return location['lat'], location['lng']
    raise ValueError("Google Geocoding API 실패")

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
        "option": "trafast"
    }
    res = requests.get(url, headers=headers, params=params)
    if res.status_code == 200:
        return res.json()
    print("❌ Naver Directions API 실패:", res.status_code, res.text)
    return None

def extract_representative_points(gdf):
    return gdf.geometry.representative_point()

# -------------------- 해안 경유지 자동 탐색 --------------------
def find_nearest_waypoint(start_lat, start_lon, end_lat, end_lon):
    print("📍 해안선 경유지 자동 탐색 중...")
    try:
        coastline = gpd.read_file("coastal_route_result.geojson")
        coastline = coastline.to_crs(epsg=4326)
        coastline["point"] = extract_representative_points(coastline)
        coastline_points = coastline.set_geometry("point")

        lat_sorted = coastline_points.iloc[
            (coastline_points.geometry.y - start_lat).abs().argsort()[:20]
        ]
        lon_sorted = coastline_points.iloc[
            (coastline_points.geometry.x - start_lon).abs().argsort()[:20]
        ]

        candidates = pd.concat([lat_sorted, lon_sorted]).drop_duplicates()

        print(f"✅ 후보 좌표 개수: {len(candidates)}")

        for _, row in candidates.iterrows():
            waypoint = [row.geometry.y, row.geometry.x]
            print("🧪 시도 중:", waypoint)
            route = get_naver_route([start_lat, start_lon], waypoint, [end_lat, end_lon])
            if route:
                print("✅ 성공한 경유지:", waypoint)
                return waypoint
        raise Exception("❌ 해안 경유지 탐색 실패: 경로 연결 안됨")
    except Exception as e:
        print("❌ 예외 발생:", e)
        raise

# -------------------- Flask 라우팅 --------------------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/route", methods=["POST"])
def route():
    try:
        data = request.get_json()
        start_address = data.get("start")
        end_address = data.get("end")

        print("📮 출발지:", start_address)
        print("📮 도착지:", end_address)

        start = geocode_google(start_address)
        end = geocode_google(end_address)

        waypoint = find_nearest_waypoint(start[0], start[1], end[0], end[1])

        route_data = get_naver_route(start, waypoint, end)
        if not route_data:
            return jsonify({"error": "❌ 경로 계산 실패"}), 500

        return jsonify({
            "start": start,
            "end": end,
            "waypoint": waypoint,
            "route": route_data
        })
    except Exception as e:
        print("❌ 서버 오류 발생:", e)
        return jsonify({"error": f"❌ 서버 오류: {e}"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=True)
