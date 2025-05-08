# app.py
import os
import requests
import urllib.parse
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import geopandas as gpd
from shapely.geometry import Point

app = Flask(__name__)
CORS(app)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
ORS_API_KEY = os.getenv("ORS_API_KEY")

# 해안선 GeoJSON 불러오기
coastline_gdf = gpd.read_file("coastal_route_result.geojson").to_crs(epsg=4326)


def correct_address(address):
    if "세종" in address and "한누리대로" in address and "세종특별자치시" not in address:
        return "세종특별자치시 " + address
    return address


def geocode_address(address):
    address = correct_address(address)
    encoded_address = urllib.parse.quote(address)
    url = f"https://maps.googleapis.com/maps/api/geocode/json?address={encoded_address}&key={GOOGLE_API_KEY}"
    response = requests.get(url)
    if response.status_code != 200:
        return None
    data = response.json()
    if data['status'] == "OK":
        loc = data['results'][0]['geometry']['location']
        return loc['lat'], loc['lng']
    return None


def find_detour_point(departure, destination):
    dep_lat, dep_lon = departure
    dest_lat, dest_lon = destination

    coastline_gdf['lat_diff'] = (coastline_gdf.geometry.y - dep_lat).abs()
    coastline_gdf['lon_diff'] = (coastline_gdf.geometry.x - dep_lon).abs()

    by_lat = coastline_gdf.sort_values('lat_diff').iloc[0]
    by_lon = coastline_gdf.sort_values('lon_diff').iloc[0]

    dest_point = Point(dest_lon, dest_lat)
    dist_lat = dest_point.distance(Point(by_lat.geometry.x, by_lat.geometry.y))
    dist_lon = dest_point.distance(Point(by_lon.geometry.x, by_lon.geometry.y))

    if dist_lat <= dist_lon:
        selected = by_lat
    else:
        selected = by_lon

    return (selected.geometry.y, selected.geometry.x)


def get_route(coords):
    headers = {
        'Authorization': ORS_API_KEY,
        'Content-Type': 'application/json'
    }
    url = 'https://api.openrouteservice.org/v2/directions/driving-car/geojson'
    body = {
        "coordinates": coords
    }
    response = requests.post(url, json=body, headers=headers)
    return response.json() if response.status_code == 200 else None


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/geocode", methods=["POST"])
def geocode():
    data = request.get_json()
    address = data.get("address")
    coords = geocode_address(address)
    return jsonify({"coords": coords}) if coords else jsonify({"error": "Geocode API returned no results."})


@app.route("/route", methods=["POST"])
def route():
    data = request.get_json()
    dep_addr = data.get("departure")
    dest_addr = data.get("destination")

    dep_coords = geocode_address(dep_addr)
    dest_coords = geocode_address(dest_addr)

    if not dep_coords or not dest_coords:
        return jsonify({"error": "Invalid address."})

    detour = find_detour_point(dep_coords, dest_coords)
    route_geojson = get_route([
        [dep_coords[1], dep_coords[0]],
        [detour[1], detour[0]],
        [dest_coords[1], dest_coords[0]]
    ])

    return jsonify(route_geojson) if route_geojson else jsonify({"error": "Routing failed."})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)), debug=True)
