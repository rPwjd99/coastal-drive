# app.py

from flask import Flask, request, jsonify, render_template
import os
import requests
from dotenv import load_dotenv
from beaches_coordinates import beach_coords
from math import radians, cos, sin, asin, sqrt

load_dotenv()

ORS_API_KEY = os.getenv("ORS_API_KEY")
TOURAPI_KEY = os.getenv("TOURAPI_KEY")

app = Flask(__name__)

# 위경도 거리 계산 함수
def haversine(lon1, lat1, lon2, lat2):
    lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * asin(sqrt(a))
    return 6371 * c

# 경유지 2개 선택
def get_two_nearest_beaches(start):
    start_lon, start_lat = start
    distances = []
    for beach in beach_coords:
        dist = haversine(start_lon, start_lat, beach["lon"], beach["lat"])
        distances.append((dist, beach))
    distances.sort(key=lambda x: x[0])
    return [distances[0][1], distances[1][1]]

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/route", methods=["POST"])
def route():
    data = request.json
    start = data["start"]
    end = data["end"]

    waypoints = get_two_nearest_beaches(start)
    coords = [start, [waypoints[0]["lon"], waypoints[0]["lat"]], [waypoints[1]["lon"], waypoints[1]["lat"]], end]

    ors_url = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
    headers = {"Authorization": ORS_API_KEY, "Content-Type": "application/json"}
    body = {
        "coordinates": coords,
        "instructions": False
    }

    res = requests.post(ors_url, headers=headers, json=body)
    route_data = res.json()

    return jsonify({
        "route": route_data,
        "waypoints": waypoints
    })

@app.route("/tourspot", methods=["POST"])
def tourspot():
    data = request.json
    lat = data["lat"]
    lon = data["lon"]

    results = []

    for content_type, label in [("39", "restaurant"), ("38", "cafe"), ("12", "tourist")]:
        url = "http://apis.data.go.kr/B551011/KorService1/locationBasedList1"
        params = {
            "serviceKey": TOURAPI_KEY,
            "MobileOS": "ETC",
            "MobileApp": "coastalDrive",
            "mapX": lon,
            "mapY": lat,
            "radius": 5000,
            "contentTypeId": content_type,
            "_type": "json"
        }

        res = requests.get(url, params=params)
        try:
            items = res.json()["response"]["body"]["items"]["item"]
        except Exception:
            items = []

        for item in items:
            results.append({
                "name": item.get("title"),
                "addr": item.get("addr1"),
                "lat": item.get("mapY"),
                "lon": item.get("mapX"),
                "img": item.get("firstimage"),
                "type": label,
                "info": f"{item.get('parking', '')} {item.get('usetime', '')}".strip()
            })

    return jsonify(results)

if __name__ == "__main__":
    app.run(debug=True)
