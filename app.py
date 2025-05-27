from flask import Flask, request, jsonify, render_template
import os
import requests
from dotenv import load_dotenv
from beaches_coordinates import beach_coords

load_dotenv()
app = Flask(__name__)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
ORS_API_KEY = os.getenv("ORS_API_KEY")
TOURAPI_KEY = "e1tU33wjMx2nynKjH8yDBm/S4YNne6B8mpCOWtzMH9TSONF71XG/xAwPqyv1fANpgeOvbPY+Le+gM6cYCnWV8w=="

def haversine(lat1, lon1, lat2, lon2):
    from math import radians, cos, sin, asin, sqrt
    R = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2)**2
    return 2 * R * asin(sqrt(a))

def geocode_google(address):
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    res = requests.get(url, params={"address": address, "key": GOOGLE_API_KEY})
    try:
        location = res.json()["results"][0]["geometry"]["location"]
        return location["lat"], location["lng"]
    except:
        return None

def reverse_geocode_google(lat, lon):
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    res = requests.get(url, params={"latlng": f"{lat},{lon}", "key": GOOGLE_API_KEY})
    try:
        return res.json()["results"][0]["formatted_address"]
    except:
        return "주소 불러오기 실패"

def is_in_coastal_bounds(lat, lon):
    return (
        (35 <= lat <= 38 and 128 <= lon <= 131) or
        (33 <= lat <= 35 and 126 <= lon <= 129) or
        (34 <= lat <= 38 and 124 <= lon <= 126)
    )

def find_best_beach_waypoint(start, end):
    start_lat, start_lon = start
    end_lat, end_lon = end
    lat_candidates, lon_candidates = [], []
    for name, (lon, lat) in beach_coords.items():
        if not is_in_coastal_bounds(lat, lon): continue
        if abs(lat - start_lat) < 0.2 and (end_lon - start_lon) * (lon - start_lon) > 0:
            lat_candidates.append((name, lat, lon, haversine(end_lat, end_lon, lat, lon)))
        if abs(lon - start_lon) < 0.2 and (end_lat - start_lat) * (lat - start_lat) > 0:
            lon_candidates.append((name, lat, lon, haversine(end_lat, end_lon, lat, lon)))
    best_lat = min(lat_candidates, key=lambda x: x[3]) if lat_candidates else None
    best_lon = min(lon_candidates, key=lambda x: x[3]) if lon_candidates else None
    return best_lat if best_lat and (not best_lon or best_lat[3] < best_lon[3]) else best_lon

def get_ors_route(start, waypoint, end):
    url = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
    headers = {"Authorization": ORS_API_KEY, "Content-Type": "application/json"}
    body = {
        "coordinates": [
            [start[1], start[0]],
            [waypoint[2], waypoint[1]],
            [end[1], end[0]]
        ]
    }
    res = requests.post(url, headers=headers, json=body)
    try:
        return res.json(), res.status_code
    except Exception as e:
        return {"error": str(e)}, 500

def search_tour_spots_along_route(geojson):
    coords = geojson['features'][0]['geometry']['coordinates']
    spots, seen_ids = [], set()
    for lon, lat in coords[::10]:
        try:
            url = "http://apis.data.go.kr/B551011/KorService1/locationBasedList1"
            params = {
                "serviceKey": TOURAPI_KEY,
                "mapX": lon,
                "mapY": lat,
                "radius": 5000,
                "listYN": "Y",
                "arrange": "E",
                "numOfRows": 10,
                "pageNo": 1,
                "MobileOS": "ETC",
                "MobileApp": "SeaRoute",
                "_type": "json"
            }
            res = requests.get(url, params=params)
            items = res.json().get("response", {}).get("body", {}).get("items", {}).get("item", [])
            for item in items:
                cid = item.get("contentid")
                if cid and cid not in seen_ids:
                    seen_ids.add(cid)
                    spots.append({
                        "title": item.get("title"),
                        "addr1": item.get("addr1"),
                        "mapx": item.get("mapx"),
                        "mapy": item.get("mapy"),
                        "firstimage": item.get("firstimage"),
                        "homepage": item.get("homepage", "")
                    })
        except:
            continue
    return spots

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/route", methods=["POST"])
def route():
    try:
        data = request.get_json()
        start = geocode_google(data.get("start"))
        end = geocode_google(data.get("end"))
        if not start or not end:
            return jsonify({"error": "❌ 주소 변환 실패"}), 400
        waypoint = find_best_beach_waypoint(start, end)
        if not waypoint:
            return jsonify({"error": "❌ 경유지 탐색 실패"}), 500
        route_data, status = get_ors_route(start, waypoint, end)
        if "error" in route_data:
            return jsonify({"error": route_data["error"]}), status
        spots = search_tour_spots_along_route(route_data)
        waypoint_addr = reverse_geocode_google(waypoint[1], waypoint[2])
        return jsonify({
            "route": route_data,
            "waypoint": {
                "name": waypoint[0],
                "lat": waypoint[1],
                "lon": waypoint[2],
                "address": waypoint_addr
            },
            "spots": spots or []
        })
    except Exception as e:
        return jsonify({"error": f"❌ 서버 오류: {str(e)}"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
