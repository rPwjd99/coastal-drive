import os
import requests
import pandas as pd
import geopandas as gpd
import numpy as np
from flask import Flask, request, jsonify, render_template
from shapely.geometry import Point
from dotenv import load_dotenv
from scipy.spatial import KDTree

load_dotenv()
app = Flask(__name__)

# API 키 로딩 확인
print("✅ API 키 로딩 확인")
print("GOOGLE_API_KEY:", bool(os.getenv("GOOGLE_API_KEY")))
print("NAVER_API_KEY_ID:", bool(os.getenv("NAVER_API_KEY_ID")))
print("NAVER_API_KEY_SECRET:", bool(os.getenv("NAVER_API_KEY_SECRET")))

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
NAVER_API_KEY_ID = os.getenv("NAVER_API_KEY_ID")
NAVER_API_KEY_SECRET = os.getenv("NAVER_API_KEY_SECRET")

road_points = pd.read_csv("road_endpoints_reduced.csv", low_memory=False)
coastline = gpd.read_file("coastal_route_result.geojson").to_crs(epsg=4326)

# coast_coords 안전하게 파싱
coast_coords = []
for geom in coastline.geometry:
    if geom.geom_type.startswith("Multi"):
        for part in geom.geoms:
            coast_coords.extend(list(part.coords))
    else:
        coast_coords.extend(list(geom.coords))
coast_coords = np.array(coast_coords)

coast_tree = KDTree(coast_coords)

def geocode_google(address):
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    res = requests.get(url, params={"address": address, "key": GOOGLE_API_KEY})
    try:
        result = res.json()["results"][0]
        location = result["geometry"]["location"]
        print("📍 주소 변환 성공:", address, "→", location)
        return location["lat"], location["lng"]
    except Exception as e:
        print("❌ 주소 변환 실패:", address, e)
        return None

def find_waypoint_near_coast(start, end, radius_km=3):
    start_lat, start_lon = start
    end_lat, end_lon = end
    use_lat = abs(start_lat - end_lat) > abs(start_lon - end_lon)

    # 해안선 3km 이내 도로점만 필터링
    candidates = []
    for _, row in road_points.iterrows():
        pt = np.array([row["x"], row["y"]])
        dist, _ = coast_tree.query(pt)
        if dist < radius_km / 111:  # 약 3km 이내
            candidates.append((row["y"], row["x"]))  # (lat, lon)

    if not candidates:
        print("❌ 3km 이내 웨이포인트 없음")
        return None

    # 방향성 필터
    candidates_df = pd.DataFrame(candidates, columns=["lat", "lon"])
    if use_lat:
        candidates_df["diff"] = abs(candidates_df["lat"] - start_lat)
    else:
        candidates_df["diff"] = abs(candidates_df["lon"] - start_lon)

    # 목적지 방향 필터링
    direction = (end_lon - start_lon) if not use_lat else (end_lat - start_lat)
    candidates_df = candidates_df[
        ((candidates_df["lon"] - start_lon) * direction > 0)
        if not use_lat else
        ((candidates_df["lat"] - start_lat) * direction > 0)
    ]

    if candidates_df.empty:
        print("❌ 방향성 있는 웨이포인트 없음")
        return None

    selected = candidates_df.sort_values("diff").iloc[0]
    print("✅ 선택된 waypoint:", selected.to_dict())
    return selected["lat"], selected["lon"]

def get_naver_route(start, waypoint, end):
    url = "https://naveropenapi.apigw.ntruss.com/map-direction/v1/driving"
    headers = {
        "X-NCP-APIGW-API-KEY-ID": NAVER_API_KEY_ID,
        "X-NCP-APIGW-API-KEY": NAVER_API_KEY_SECRET
    }
    params = {
        "start": f"{start[1]},{start[0]}",
        "goal": f"{end[1]},{end[0]}",
        "waypoints": f"{waypoint[1]},{waypoint[0]}",
        "option": "trafast",
        "format": "json"
    }
    res = requests.get(url, headers=headers, params=params)
    print("📡 NAVER 응답코드:", res.status_code)
    try:
        data = res.json()
        print("📄 NAVER 응답 일부:", str(data)[:300])
        return data, res.status_code
    except Exception as e:
        print("❌ NAVER JSON 파싱 실패:", e)
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

        waypoint = find_waypoint_near_coast(start, end)
        if not waypoint:
            return jsonify({"error": "❌ 경유지 탐색 실패"}), 500

        route_data, status = get_naver_route(start, waypoint, end)
        if "route" not in route_data:
            return jsonify({"error": "❌ 경로 없음"}), 400

        # LineString GeoJSON 변환
        coords = route_data["route"]["trafast"][0]["path"]
        geojson = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[c[0], c[1]] for c in coords]
                },
                "properties": {}
            }]
        }

        return jsonify(geojson)

    except Exception as e:
        print("❌ 서버 오류:", e)
        return jsonify({"error": f"❌ 서버 내부 오류: {str(e)}"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
