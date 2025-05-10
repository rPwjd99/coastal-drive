from flask import Flask, render_template, request, jsonify
import requests
import geopandas as gpd
from shapely.geometry import Point

app = Flask(__name__)

NAVER_CLIENT_ID = "your_client_id"
NAVER_CLIENT_SECRET = "your_client_secret"

@app.route("/")
def index():
    return render_template("index.html")

def get_coords_from_google(address):
    # 주소를 좌표로 변환하는 함수 구현
    pass

def find_nearest_waypoint(lat, lng):
    # 가장 가까운 해안선 포인트를 찾는 함수 구현
    pass

@app.route("/route", methods=["POST"])
def route():
    try:
        data = request.get_json()
        start = data["start"]
        end = data["end"]

        start_lat, start_lng = get_coords_from_google(start)
        end_lat, end_lng = get_coords_from_google(end)

        if not all([start_lat, start_lng, end_lat, end_lng]):
            return jsonify({"error": "❌ 주소 인식 실패"}), 400

        waypoint_lat, waypoint_lng = find_nearest_waypoint(
            (start_lat + end_lat) / 2, (start_lng + end_lng) / 2
        )

        headers = {
            "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID,
            "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET,
            "Content-Type": "application/json"
        }

        body = {
            "start": {"lat": start_lat, "lng": start_lng, "name": "출발지"},
            "goal": {"lat": end_lat, "lng": end_lng, "name": "도착지"},
            "waypoints": [{"lat": waypoint_lat, "lng": waypoint_lng}],
            "option": "trafast"
        }

        response = requests.post(
            "https://naveropenapi.apigw.ntruss.com/map-direction/v1/driving",
            headers=headers,
            json=body
        )

        if response.status_code != 200:
            return jsonify({"error": "❌ 경로 계산 실패"}), 500

        result = response.json()
        path = result["route"]["trafast"][0]["path"]
        geojson = {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [[p[1], p[0]] for p in path]
            },
            "properties": {}
        }

        return jsonify({
            "geojson": geojson,
            "start_corrected": start,
            "end_corrected": end
        })

    except Exception as e:
        app.logger.error(f"서버 오류 발생: {e}")
        return jsonify({"error": "❌ 서버 오류 발생"}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000, debug=True)
