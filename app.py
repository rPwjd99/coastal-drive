import os
import json
import requests
import geopandas as gpd
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from shapely.geometry import Point

app = Flask(__name__)
CORS(app)

# 🔧 네이버 API 키
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID", "vsdzf1f4n5")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET", "0gzctO51PUTVv0gUZU025JYNHPTmVzLS9sGbfYBM")

# 📍 해안선 파일 경로
GEOJSON_PATH = os.path.join(os.path.dirname(__file__), "coastal_route_result.geojson")

def geocode_google(address):
    url = f"https://maps.googleapis.com/maps/api/geocode/json?address={address}&key=AIzaSyC9MSD-WhkqK_Og5YdVYfux21xiRjy2q1M"
    res = requests.get(url)
    data = res.json()
    if data["status"] == "OK":
        location = data["results"][0]["geometry"]["location"]
        return location["lat"], location["lng"]
    else:
        raise Exception("주소 변환 실패: " + address)

def naver_directions(start, end, waypoint=None):
    headers = {
        "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID,
        "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET,
        "Content-Type": "application/json"
    }
    url = "https://naveropenapi.apigw.ntruss.com/map-direction/v1/driving"
    params = {
        "start": f"{start[1]},{start[0]}",
        "goal": f"{end[1]},{end[0]}",
        "option": "trafast"
    }
    if waypoint:
        params["waypoints"] = f"{waypoint[1]},{waypoint[0]}"
    res = requests.get(url, headers=headers, params=params)
    return res.json()

def extract_centroid_coords(geom):
    if geom.geom_type == 'Point':
        return geom.y, geom.x
    else:
        centroid = geom.centroid
        return centroid.y, centroid.x

def find_nearest_waypoint(start_lat, start_lng, end_lat, end_lng):
    coastline = gpd.read_file(GEOJSON_PATH)
    coastline = coastline[coastline.geometry.is_valid]

    if coastline.empty:
        raise Exception("❌ 해안선 데이터가 비어 있습니다.")

    # 중심 좌표 추출
    coastline["lat"] = coastline.geometry.apply(lambda g: extract_centroid_coords(g)[0])
    coastline["lng"] = coastline.geometry.apply(lambda g: extract_centroid_coords(g)[1])

    # 위도 기준
    lat_sorted = coastline.iloc[(coastline["lat"] - start_lat).abs().argsort()[:20]]
    lng_sorted = coastline.iloc[(coastline["lng"] - start_lng).abs().argsort()[:20]]

    # 거리 기반 판단
    lat_dist = abs(lat_sorted.iloc[0]["lat"] - start_lat)
    lng_dist = abs(lng_sorted.iloc[0]["lng"] - start_lng)

    if lat_dist <= lng_dist:
        target = lat_sorted.iloc[0]
    else:
        target = lng_sorted.iloc[0]

    return (target["lat"], target["lng"])

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/route", methods=["POST"])
def route():
    try:
        data = request.get_json()
        start_address = data["start"]
        end_address = data["end"]

        start = geocode_google(start_address)
        end = geocode_google(end_address)

        waypoint = find_nearest_waypoint(start[0], start[1], end[0], end[1])

        nav_result = naver_directions(start, end, waypoint)

        if "route" not in nav_result:
            raise Exception("❌ 경로 탐색 실패")

        path = nav_result["route"]["trafast"][0]["path"]

        return jsonify({
            "route": path,
            "waypoint": waypoint
        })

    except Exception as e:
        return jsonify({"error": f"❌ 서버 오류: {str(e)}"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=True)
