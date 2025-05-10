from flask import Flask, render_template, request, jsonify
import requests
import json
from shapely.geometry import Point
from shapely.ops import nearest_points
from shapely.geometry import shape
import geopandas as gpd

app = Flask(__name__)

NAVER_CLIENT_ID = "vsdzf1f4n5"
NAVER_CLIENT_SECRET = "0gzctO51PUTVv0gUZU025JYNHPTmVzLS9sGbfYBM"

@app.route("/")
def index():
    return render_template("index.html")

def get_coords_from_google(address):
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {
        "address": address,
        "key": "AIzaSyC9MSD-WhkqK_Og5YdVYfux21xiRjy2q1M"
    }
    response = requests.get(url, params=params).json()
    if response["status"] == "OK":
        location = response["results"][0]["geometry"]["location"]
        return location["lat"], location["lng"]
    return None, None

def find_nearest_waypoint(lat, lng):
    gdf = gpd.read_file("converted_coastline_points.geojson")
    target = Point(lng, lat)
    gdf["geometry"] = gdf["geometry"].apply(lambda geom: geom if geom is not None else Point(0, 0))
    nearest = gdf.geometry.apply(lambda p: p.distance(target)).idxmin()
    nearest_point = gdf.loc[nearest].geometry
    return nearest_point.y, nearest_point.x

@app.route("/route", methods=["POST"])
def route():
    data = request.get_json()
    start = data["start"]
    end = data["end"]

    start_lat, start_lng = get_coords_from_google(start)
    end_lat, end_lng = get_coords_from_google(end)

    if not all([start_lat, start_lng, end_lat, end_lng]):
        return jsonify({"error": "❌ 주소 인식 실패"}), 500

    waypoint_lat, waypoint_lng = find_nearest_waypoint((start_lat + end_lat) / 2, (start_lng + end_lng) / 2)

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

    response = requests.post("https://naveropenapi.apigw.ntruss.com/map-direction/v1/driving", headers=headers, json=body)
    if response.status_code != 200:
        return jsonify({"error": "❌ 경로 계산 실패"}), 500

    result = response.json()
    try:
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
        print("❌ 예외:", e)
        return jsonify({"error": "❌ 경로 계산 실패"}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000, debug=True)
