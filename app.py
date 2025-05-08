import os
import json
import requests
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from shapely.geometry import Point
import geopandas as gpd
import math

app = Flask(__name__)
CORS(app)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
ORS_API_KEY = os.getenv("ORS_API_KEY")
VWORLD_API_KEY = os.getenv("VWORLD_API_KEY")
TOURAPI_KEY = os.getenv("TOURAPI_KEY")

coastline = gpd.read_file("해안선_국가기본도.geojson").to_crs(epsg=4326)
coords = []
for geom in coastline.explode(index_parts=False).geometry:
    if geom.geom_type == "LineString":
        coords.extend(list(geom.coords))
    elif geom.geom_type == "MultiLineString":
        for line in geom:
            coords.extend(list(line.coords))

def haversine(coord1, coord2):
    R = 6371
    lat1, lon1 = map(math.radians, coord1)
    lat2, lon2 = map(math.radians, coord2)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))

@app.route("/geocode", methods=["POST"])
def geocode():
    data = request.get_json()
    address = data["address"]
    url = f"https://maps.googleapis.com/maps/api/geocode/json?address={address}&key={GOOGLE_API_KEY}"
    response = requests.get(url)
    result = response.json()
    if result["status"] == "OK":
        loc = result["results"][0]["geometry"]["location"]
        return jsonify({"lat": loc["lat"], "lon": loc["lng"]})
    else:
        return jsonify({"error": result["status"]})

@app.route("/route", methods=["POST"])
def route():
    data = request.get_json()
    start = (data["start"][1], data["start"][0])  # (lat, lon)
    end = (data["end"][1], data["end"][0])        # (lat, lon)

    lat_candidates = [pt for pt in coords if abs(pt[1] - start[0]) < 0.1 and pt[0] > start[1]]
    lon_candidates = [pt for pt in coords if abs(pt[0] - start[1]) < 0.1 and pt[1] > start[0]]

    nearest_lat = min(lat_candidates, key=lambda x: haversine(start, (x[1], x[0]))) if lat_candidates else None
    nearest_lon = min(lon_candidates, key=lambda x: haversine(start, (x[1], x[0]))) if lon_candidates else None

    if nearest_lat and nearest_lon:
        dist_lat = haversine(start, (nearest_lat[1], nearest_lat[0]))
        dist_lon = haversine(start, (nearest_lon[1], nearest_lon[0]))
        waypoint = nearest_lat if dist_lat <= dist_lon else nearest_lon
    elif nearest_lat:
        waypoint = nearest_lat
    elif nearest_lon:
        waypoint = nearest_lon
    else:
        return jsonify({"error": "경유지 해안 좌표를 찾을 수 없습니다."})

    coords_list = [
        [start[1], start[0]],
        [waypoint[0], waypoint[1]],
        [end[1], end[0]]
    ]

    headers = {
        "Authorization": ORS_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "coordinates": coords_list,
        "format": "geojson"
    }

    res = requests.post("https://api.openrouteservice.org/v2/directions/driving-car/geojson", headers=headers, json=payload)
    if res.status_code != 200:
        return jsonify({"error": res.text})

    route = res.json()

    # 관광지
    spot_url = f"http://apis.data.go.kr/B551011/KorService1/locationBasedList1?MobileOS=ETC&MobileApp=AppTest&mapX={end[0]}&mapY={end[1]}&radius=5000&arrange=E&numOfRows=10&pageNo=1&_type=json&serviceKey={TOURAPI_KEY}"
    tour_res = requests.get(spot_url).json()
    items = tour_res.get("response", {}).get("body", {}).get("items", {}).get("item", [])
    tourspots = [{"title": i["title"], "mapx": float(i["mapx"]), "mapy": float(i["mapy"])} for i in items]

    return jsonify({"route": route, "tourspots": tourspots})

@app.route("/")
def serve_index():
    return send_from_directory(".", "index.html")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)), debug=True)
