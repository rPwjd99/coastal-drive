from flask import Flask, request, jsonify, render_template
from geopy.distance import geodesic
from beaches_coordinates import beach_coords
import requests
import os

app = Flask(__name__)

# Google Maps Geocoding API 키 환경변수에서 가져오기
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# 주소 → 위도/경도 변환 함수
def geocode_address(address):
    url = f"https://maps.googleapis.com/maps/api/geocode/json?address={address}&key={GOOGLE_API_KEY}"
    response = requests.get(url)
    if response.status_code == 200:
        results = response.json()["results"]
        if results:
            location = results[0]["geometry"]["location"]
            return location["lat"], location["lng"]
    return None

# 가장 가까운 해안 좌표 계산 함수
def find_best_waypoint(start_coords, end_coords):
    start_lat, start_lng = start_coords
    lat_candidates = []
    lng_candidates = []

    for name, (lng, lat) in beach_coords.items():
        if abs(lat - start_lat) < 0.2:
            lat_candidates.append((name, (lat, lng)))
        if abs(lng - start_lng) < 0.2:
            lng_candidates.append((name, (lat, lng)))

    def closest(candidates):
        return min(candidates, key=lambda item: geodesic(start_coords, item[1]).km, default=None)

    best_lat = closest(lat_candidates)
    best_lng = closest(lng_candidates)

    if not best_lat and not best_lng:
        return None
    elif best_lat and not best_lng:
        return best_lat[1]
    elif best_lng and not best_lat:
        return best_lng[1]
    else:
        dist_lat = geodesic(start_coords, best_lat[1]).km
        dist_lng = geodesic(start_coords, best_lng[1]).km
        return best_lat[1] if dist_lat <= dist_lng else best_lng[1]

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/route", methods=["POST"])
def route():
    data = request.get_json()
    start = data.get("start")
    end = data.get("end")

    start_coords = geocode_address(start)
    end_coords = geocode_address(end)

    if not start_coords or not end_coords:
        return jsonify({"error": "주소 변환 실패"}), 400

    waypoint = find_best_waypoint(start_coords, end_coords)
    if not waypoint:
        return jsonify({"error": "근처 해수욕장을 찾지 못했습니다."}), 400

    result = {
        "start": {"lat": start_coords[0], "lng": start_coords[1]},
        "waypoint": {"lat": waypoint[0], "lng": waypoint[1]},
        "end": {"lat": end_coords[0], "lng": end_coords[1]}
    }
    return jsonify(result)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
