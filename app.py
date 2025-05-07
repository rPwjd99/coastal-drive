from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import requests
import json
import os
from shapely.geometry import shape, Point
import geopandas as gpd

app = Flask(__name__)
CORS(app)

VWORLD_API_KEY = "FA346133-805B-3BB4-B8C2-372973E3A4ED"
ORS_API_KEY = "5b3ce3597851110001cf62486d543846e80049df9c7a9e10ecef2953"
TOURAPI_KEY = "e1tU33wjMx2nynKjH8yDBm/S4YNne6B8mpCOWtzMH9TSONF71XG/xAwPqyv1fANpgeOvbPY+Le+gM6cYCnWV8w=="

@app.route("/")
def root():
    return send_file("index.html")

@app.route("/api/search")
def search():
    query = request.args.get("query")
    if not query:
        return jsonify({"error": "쿼리를 입력하세요."}), 400

    url = f"https://api.vworld.kr/req/search?key={VWORLD_API_KEY}&service=search&request=search&version=2.0&format=json&query={query}&type=road"
    try:
        res = requests.get(url, timeout=5)
        data = res.json()
        results = data.get("response", {}).get("result", {}).get("items", [])
        if not results:
            return jsonify([])

        coords = [
            {
                "title": item["title"],
                "x": float(item["point"]["x"]),
                "y": float(item["point"]["y"])
            }
            for item in results if "point" in item
        ]
        return jsonify(coords)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/route")
def route():
    start = request.args.get("start")
    end = request.args.get("end")
    if not start or not end:
        return jsonify({"error": "출발지와 도착지를 입력하세요."}), 400

    def geocode(address):
        url = f"https://api.vworld.kr/req/address?service=address&request=getcoord&format=json&type=road&address={address}&key={VWORLD_API_KEY}"
        res = requests.get(url, timeout=5).json()
        try:
            point = res["response"]["result"]["point"]
            return float(point["x"]), float(point["y"])
        except:
            return None

    start_coord = geocode(start)
    end_coord = geocode(end)
    if not start_coord or not end_coord:
        return jsonify({"error": "좌표를 찾을 수 없습니다. 주소를 확인하세요."}), 400

    start_x, start_y = start_coord
    end_x, end_y = end_coord

    # 임시: start와 end 위경도만 경유 없이 경로 계산 (추후 해안 우회 로직 삽입 가능)
    url = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
    headers = {"Authorization": ORS_API_KEY, "Content-Type": "application/json"}
    body = {
        "coordinates": [[start_x, start_y], [end_x, end_y]]
    }

    try:
        res = requests.post(url, headers=headers, json=body, timeout=10)
        if res.status_code != 200:
            return jsonify({"error": "OpenRouteService 요청 실패"}), 500
        return jsonify({"route": res.json()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/tourspot")
def tourspot():
    end = request.args.get("end")
    if not end:
        return jsonify([])

    # 목적지 좌표 얻기
    try:
        geo_url = f"https://api.vworld.kr/req/address?service=address&request=getcoord&format=json&type=road&address={end}&key={VWORLD_API_KEY}"
        geo_res = requests.get(geo_url, timeout=5).json()
        point = geo_res["response"]["result"]["point"]
        lon, lat = float(point["x"]), float(point["y"])
    except:
        return jsonify([])

    tour_url = (
        f"http://apis.data.go.kr/B551011/KorService1/locationBasedList1"
        f"?serviceKey={TOURAPI_KEY}&numOfRows=10&pageNo=1&MobileOS=ETC&MobileApp=SeaRouteApp&_type=json"
        f"&mapX={lon}&mapY={lat}&radius=5000"
    )
    try:
        tour_res = requests.get(tour_url, timeout=5).json()
        items = tour_res.get("response", {}).get("body", {}).get("items", {}).get("item", [])
        return jsonify(items)
    except:
        return jsonify([])

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
