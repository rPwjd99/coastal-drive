import os
import requests
import pandas as pd
from flask import Flask, request, jsonify, render_template
from math import radians, cos, sin, asin, sqrt
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")

ROAD_CSV_PATH = os.path.join(os.path.dirname(__file__), "road_endpoints_reduced.csv")
road_points = pd.read_csv(ROAD_CSV_PATH, low_memory=False)


def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2)**2
    return 2 * R * asin(sqrt(a))


def geocode_google(address):
    if not address:
        return None
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    res = requests.get(url, params={"address": address, "key": GOOGLE_API_KEY})
    try:
        location = res.json()["results"][0]["geometry"]["location"]
        print(f"📍 주소 변환 성공: {address} → {location}", flush=True)
        return location["lat"], location["lng"]
    except Exception:
        print(f"❌ 주소 변환 실패: {address}", flush=True)
        return None


def find_waypoint(start_lat, start_lon, end_lat, end_lon):
    lat_diff = abs(start_lat - end_lat)
    lon_diff = abs(start_lon - end_lon)
    use_lat = lat_diff > lon_diff

    base = round(start_lat, 2) if use_lat else round(start_lon, 2)
    axis = "y" if use_lat else "x"
    candidates = road_points[road_points[axis].round(2) == base].copy()

    if candidates.empty:
        print("❌ 유사 좌표 도로점 없음", flush=True)
        return None

    candidates["dist_to_start"] = candidates.apply(
        lambda row: haversine(start_lat, start_lon, row["y"], row["x"]), axis=1
    )
    candidates["dist_to_end"] = candidates.apply(
        lambda row: haversine(end_lat, end_lon, row["y"], row["x"]), axis=1
    )

    # 5km 이내 + 방향성 고려
    direction_filter = (
        ((end_lat - start_lat) * (candidates["y"] - start_lat) > 0) if use_lat
        else ((end_lon - start_lon) * (candidates["x"] - start_lon) > 0)
    )
    candidates = candidates[direction_filter & (candidates["dist_to_start"] <= 5)]

    if candidates.empty:
        print("❌ 방향성 + 거리 조건 만족 도로점 없음", flush=True)
        return None

    selected = candidates.sort_values("dist_to_end").iloc[0]
    print(f"📍 선택된 waypoint: {selected['y']}, {selected['x']}", flush=True)
    return selected["y"], selected["x"]


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
        "option": "trafast",  # 빠른 길
        "output": "json"
    }
    res = requests.get(url, headers=headers, params=params)
    print(f"📡 NAVER 응답코드: {res.status_code}", flush=True)

    try:
        data = res.json()
        if "route" not in data:
            print("❌ 경로 응답 오류:", data, flush=True)
            return {"error": data.get("message", "경로 탐색 실패")}, 400
        return data, 200
    except Exception as e:
        return {"error": str(e)}, 500


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/route", methods=["POST"])
def route():
    data = request.get_json()
    start_addr = data.get("start")
    end_addr = data.get("end")

    start = geocode_google(start_addr)
    end = geocode_google(end_addr)

    if not start or not end:
        return jsonify({"error": "❌ 주소 변환 실패"}), 400

    waypoint = find_waypoint(start[0], start[1], end[0], end[1])
    if not waypoint:
        return jsonify({"error": "❌ 해안 경유지 탐색 실패"}), 404

    route_data, status = get_naver_route(start, waypoint, end)
    if "error" in route_data:
        return jsonify({"error": route_data["error"]}), status

    return jsonify(route_data)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
