from flask import Flask, render_template, request, jsonify
import requests
import os
import json
from shapely.geometry import Point
import geopandas as gpd

app = Flask(__name__)

VWORLD_API_KEY = os.environ.get("VWORLD_API_KEY")
ORS_API_KEY = os.environ.get("ORS_API_KEY")
TOURAPI_KEY = os.environ.get("TOURAPI_KEY")

coast_gdf = gpd.read_file("static/해안선.geojson")

@app.route("/")
def index():
    return render_template("index.html")


def normalize_address(address):
    table = {
        "서울시": "서울특별시",
        "부산시": "부산광역시",
        "대구시": "대구광역시",
        "세종시": "세종특별자치시",
        "광주시": "광주광역시",
        "대전시": "대전광역시",
        "울산시": "울산광역시"
    }
    for short, full in table.items():
        address = address.replace(short, full)
    return address


def safe_json(res):
    try:
        return res.json()
    except Exception as e:
        print("[ERROR] JSON decoding failed:", e)
        print("[ERROR] Raw response:", res.text)
        return {}


def geocode(address):
    address = normalize_address(address)

    url_road = f"https://api.vworld.kr/req/address?service=address&request=getcoord&format=json&type=road&address={address}&key={VWORLD_API_KEY}"
    res = requests.get(url_road)
    data = safe_json(res)
    result = data.get('response', {}).get('result', [])
    if result:
        point = result[0]['point']
        return [float(point['x']), float(point['y'])]

    url_parcel = f"https://api.vworld.kr/req/address?service=address&request=getcoord&format=json&type=parcel&address={address}&key={VWORLD_API_KEY}"
    res = requests.get(url_parcel)
    data = safe_json(res)
    result = data.get('response', {}).get('result', [])
    if result:
        point = result[0]['point']
        return [float(point['x']), float(point['y'])]

    return search_poi(address)


def search_poi(query):
    url = f"https://api.vworld.kr/req/search?service=search&request=search&format=json&key={VWORLD_API_KEY}&query={query}"
    try:
        res = requests.get(url)
        data = safe_json(res)
        items = data.get('response', {}).get('result', {}).get('items', [])
        if items:
            point = items[0]['point']
            return [float(point['x']), float(point['y'])]
    except:
        return None


def test_route(coords):
    try:
        url = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
        headers = {"Authorization": ORS_API_KEY, "Content-Type": "application/json"}
        body = {"coordinates": coords}
        res = requests.post(url, headers=headers, json=body)
        return res.status_code == 200
    except:
        return False


def find_nearest_coast_point(start, end):
    start_pt = Point(start)
    end_pt = Point(end)
    coast_coords = []
    for geom in coast_gdf.geometry:
        if geom.geom_type == 'MultiLineString':
            for line in geom:
                coast_coords.extend(list(line.coords))
        elif geom.geom_type == 'LineString':
            coast_coords.extend(list(geom.coords))

    candidates = []
    for lon, lat in coast_coords:
        total_dist = start_pt.distance(Point(lon, lat)) + Point(lon, lat).distance(end_pt)
        candidates.append(((lon, lat), total_dist))

    candidates.sort(key=lambda x: x[1])
    for (lon, lat), _ in candidates:
        if test_route([start, [lon, lat], end]):
            return [lon, lat]
    return None


def get_route(coords):
    url = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
    headers = {"Authorization": ORS_API_KEY, "Content-Type": "application/json"}
    body = {"coordinates": coords}
    res = requests.post(url, headers=headers, json=body)
    return safe_json(res)


@app.route("/api/route")
def route():
    try:
        start_addr = request.args.get("start")
        end_addr = request.args.get("end")
        start_coord = geocode(start_addr)
        end_coord = geocode(end_addr)

        if not start_coord or not end_coord:
            return jsonify({"error": "주소 변환 실패", "start": start_addr, "end": end_addr}), 400

        waypoint = find_nearest_coast_point(start_coord, end_coord)
        coords = [start_coord, waypoint, end_coord] if waypoint else [start_coord, end_coord]
        route = get_route(coords)
        return jsonify(route)
    except Exception as e:
        return jsonify({"error": f"서버 내부 오류: {str(e)}"}), 500


@app.route("/api/tourspot")
def tourspot():
    try:
        lon = request.args.get("lon")
        lat = request.args.get("lat")
        url = f"http://apis.data.go.kr/B551011/KorService1/locationBasedList1?MobileOS=ETC&MobileApp=test&mapX={lon}&mapY={lat}&radius=5000&arrange=E&numOfRows=10&pageNo=1&_type=json&serviceKey={TOURAPI_KEY}"
        res = requests.get(url)
        items = safe_json(res).get("response", {}).get("body", {}).get("items", {}).get("item", [])
        result = []
        for item in items:
            result.append({
                "title": item.get("title"),
                "addr1": item.get("addr1"),
                "mapx": float(item.get("mapx", 0)),
                "mapy": float(item.get("mapy", 0)),
                "firstimage": item.get("firstimage")
            })
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": f"TourAPI 호출 실패: {str(e)}"}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
