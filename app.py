from flask import Flask, request, jsonify, render_template
import os
import requests
from dotenv import load_dotenv
from beaches_coordinates import beach_coords
from math import radians, cos, sin, asin, sqrt

load_dotenv()
app = Flask(__name__)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
ORS_API_KEY = os.getenv("ORS_API_KEY")
TOURAPI_KEY = os.getenv("TOURAPI_KEY")

def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2)**2
    return 2 * R * asin(sqrt(a))

def geocode_google(address):
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {"address": address, "key": GOOGLE_API_KEY}
    res = requests.get(url, params=params, timeout=5)
    try:
        location = res.json()["results"][0]["geometry"]["location"]
        return location["lat"], location["lng"]
    except:
        return None

def reverse_geocode_google(lat, lon):
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {"latlng": f"{lat},{lon}", "key": GOOGLE_API_KEY}
    res = requests.get(url, params=params, timeout=5)
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

def find_two_beach_waypoints(start, end):
    start_lat, start_lon = start
    end_lat, end_lon = end
    candidates = []
    for name, (lon, lat) in beach_coords.items():
        if not is_in_coastal_bounds(lat, lon):
            continue
        if (abs(lat - start_lat) < 0.2 or abs(lon - start_lon) < 0.2) and (end_lat - start_lat) * (lat - start_lat) > 0 and (end_lon - start_lon) * (lon - start_lon) > 0:
            dist = haversine(start_lat, start_lon, lat, lon)
            candidates.append((name, lat, lon, dist))
    candidates.sort(key=lambda x: x[3])
    return candidates[:2] if len(candidates) >= 2 else (candidates + [None])[:2]

def get_ors_route_multi(start, wp1, wp2, end):
    url = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
    headers = {"Authorization": ORS_API_KEY, "Content-Type": "application/json"}
    coordinates = [[start[1], start[0]]]
    if wp1: coordinates.append([wp1[2], wp1[1]])
    if wp2: coordinates.append([wp2[2], wp2[1]])
    coordinates.append([end[1], end[0]])
    body = {"coordinates": coordinates}
    res = requests.post(url, headers=headers, json=body, timeout=10)
    try:
        return res.json(), res.status_code
    except Exception as e:
        return {"error": str(e)}, 500

def get_detail_info(contentid):
    url = "http://apis.data.go.kr/B551011/KorService1/detailCommon1"
    params = {
        "serviceKey": TOURAPI_KEY,
        "MobileOS": "ETC",
        "MobileApp": "SeaRoute",
        "contentId": contentid,
        "overviewYN": "Y",
        "defaultYN": "Y",
        "firstImageYN": "Y",
        "addrinfoYN": "Y",
        "mapinfoYN": "Y",
        "usetimeYN": "Y",
        "parkingYN": "Y",
        "_type": "json"
    }
    try:
        res = requests.get(url, params=params, timeout=5)
        items = res.json().get("response", {}).get("body", {}).get("items", {}).get("item", [])
        return items[0] if items else {}
    except:
        return {}

def enrich_pois_with_details(pois):
    enriched = []
    for poi in pois:
        detail = get_detail_info(poi["contentid"])
        poi.update({
            "overview": detail.get("overview"),
            "usetime": detail.get("usetime"),
            "parking": detail.get("parking"),
            "homepage": detail.get("homepage"),
            "firstimage": detail.get("firstimage", poi.get("firstimage"))
        })
        enriched.append(poi)
    return enriched

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
                "radius": 10000,
                "listYN": "Y",
                "arrange": "E",
                "numOfRows": 10,
                "pageNo": 1,
                "MobileOS": "ETC",
                "MobileApp": "SeaRoute",
                "_type": "json"
            }
            res = requests.get(url, params=params, timeout=5)
            items = res.json().get("response", {}).get("body", {}).get("items", {}).get("item", [])
            for item in items:
                cid = item.get("contentid")
                if cid and cid not in seen_ids:
                    seen_ids.add(cid)
                    spots.append({
                        "contentid": cid,
                        "title": item.get("title"),
                        "addr1": item.get("addr1"),
                        "mapx": item.get("mapx"),
                        "mapy": item.get("mapy"),
                        "firstimage": item.get("firstimage"),
                        "distance": round(haversine(lat, lon, float(item.get("mapy", lat)), float(item.get("mapx", lon))), 2)
                    })
        except:
            continue
    return enrich_pois_with_details(spots)

def search_restaurants_along_route(geojson):
    coords = geojson['features'][0]['geometry']['coordinates']
    restaurants, seen_ids = [], set()
    for lon, lat in coords[::10]:
        try:
            url = "http://apis.data.go.kr/B551011/KorService1/locationBasedList1"
            params = {
                "serviceKey": TOURAPI_KEY,
                "mapX": lon,
                "mapY": lat,
                "radius": 10000,
                "listYN": "Y",
                "arrange": "E",
                "numOfRows": 10,
                "pageNo": 1,
                "MobileOS": "ETC",
                "MobileApp": "SeaRoute",
                "_type": "json",
                "contentTypeId": "39"
            }
            res = requests.get(url, params=params, timeout=5)
            items = res.json().get("response", {}).get("body", {}).get("items", {}).get("item", [])
            for item in items:
                cid = item.get("contentid")
                if cid and cid not in seen_ids:
                    seen_ids.add(cid)
                    restaurants.append({
                        "contentid": cid,
                        "title": item.get("title"),
                        "addr1": item.get("addr1"),
                        "mapx": item.get("mapx"),
                        "mapy": item.get("mapy"),
                        "firstimage": item.get("firstimage"),
                        "distance": round(haversine(lat, lon, float(item.get("mapy", lat)), float(item.get("mapx", lon))), 2)
                    })
        except:
            continue
    return enrich_pois_with_details(restaurants)

@app.route("/route", methods=["POST"])
def route():
    try:
        data = request.get_json()
        start = geocode_google(data.get("start"))
        end = geocode_google(data.get("end"))
        if not start or not end:
            return jsonify({"error": "❌ 주소 변환 실패"}), 400

        wp1, wp2 = find_two_beach_waypoints(start, end)
        if not wp1:
            return jsonify({"error": "❌ 경유지 탐색 실패"}), 500

        route_data, status = get_ors_route_multi(start, wp1, wp2, end)
        if "error" in route_data:
            return jsonify({"error": route_data["error"]}), status

        spots = search_tour_spots_along_route(route_data)
        restaurants = search_restaurants_along_route(route_data)
        wp1_addr = reverse_geocode_google(wp1[1], wp1[2]) if wp1 else ""
        wp2_addr = reverse_geocode_google(wp2[1], wp2[2]) if wp2 else ""

        return jsonify({
            "route": route_data,
            "waypoint1": {"name": wp1[0], "lat": wp1[1], "lon": wp1[2], "address": wp1_addr} if wp1 else None,
            "waypoint2": {"name": wp2[0], "lat": wp2[1], "lon": wp2[2], "address": wp2_addr} if wp2 else None,
            "spots": spots or [],
            "restaurants": restaurants or []
        })
    except Exception as e:
        return jsonify({"error": f"❌ 서버 오류: {str(e)}"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
