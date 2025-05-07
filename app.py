from flask import Flask, request, jsonify
import requests
import geopandas as gpd
from shapely.geometry import Point
import math
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

VWORLD_KEY = "FA346133-805B-3BB4-B8C2-372973E3A4ED"
ORS_KEY = "5b3ce3597851110001cf62486d543846e80049df9c7a9e10ecef2953"
TOURAPI_KEY = "e1tU33wjMx2nynKjH8yDBm/S4YNne6B8mpCOWtzMH9TSONF71XG/xAwPqyv1fANpgeOvbPY+Le+gM6cYCnWV8w=="

COAST_FILE = "해안선_국가기본도.geojson"

def geocode(address):
    url = "https://api.vworld.kr/req/address"
    params = {
        "service": "address", "request": "getcoord", "format": "json",
        "type": "both", "address": address, "key": VWORLD_KEY
    }
    res = requests.get(url, params=params).json()
    try:
        pt = res['response']['result']['point']
        return [float(pt['x']), float(pt['y'])]
    except:
        print("Geocode fail:", address)
        return None

def haversine(p1, p2):
    R = 6371
    lon1, lat1, lon2, lat2 = map(math.radians, [*p1, *p2])
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))

def find_coast(start, end):
    gdf = gpd.read_file(COAST_FILE)
    gdf['centroid'] = gdf['geometry'].centroid
    gdf['dist'] = gdf['centroid'].apply(lambda x: haversine((x.x, x.y), ((start[0]+end[0])/2, (start[1]+end[1])/2)))
    best = gdf.sort_values("dist").iloc[0]['centroid']
    return [best.x, best.y]

def route(coords):
    url = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
    headers = {"Authorization": ORS_KEY, "Content-Type": "application/json"}
    body = {"coordinates": coords}
    res = requests.post(url, json=body, headers=headers)
    return res.json()

@app.route("/api/route")
def api_route():
    start = request.args.get("start")
    end = request.args.get("end")
    s = geocode(start)
    e = geocode(end)
    if not s or not e:
        return jsonify({"error": "좌표를 찾을 수 없습니다. 주소를 확인하세요."})
    c = find_coast(s, e)
    geojson = route([s, c, e])
    return jsonify(geojson)

@app.route("/api/tourspot")
def api_tour():
    lat = request.args.get("lat")
    lon = request.args.get("lon")
    url = "http://apis.data.go.kr/B551011/KorService1/locationBasedList1"
    params = {
        "serviceKey": TOURAPI_KEY,
        "mapX": lon, "mapY": lat, "radius": 5000,
        "MobileOS": "ETC", "MobileApp": "test", "_type": "json"
    }
    res = requests.get(url, params=params).json()
    try:
        items = res['response']['body']['items']['item']
        return jsonify([{"title": i['title'], "mapx": i['mapx'], "mapy": i['mapy']} for i in items])
    except:
        return jsonify([])

@app.route("/")
def root():
    return "SeaRoute Flask API Active"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
