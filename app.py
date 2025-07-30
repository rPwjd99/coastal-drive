from flask import Flask, render_template, request, jsonify
import requests
import os
from dotenv import load_dotenv

app = Flask(__name__)
load_dotenv()

TOUR_API_KEY = os.getenv("TOUR_API_KEY")  # 디코딩된 키
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/route", methods=["POST"])
def calculate_route():
    data = request.get_json()
    start = data.get("start")
    end = data.get("end")

    if not start or not end:
        return jsonify({"error": "출발지와 도착지를 모두 입력하세요."}), 400

    # 네이버 주소 → 좌표 변환
    def geocode(address):
        res = requests.get(
            "https://naveropenapi.apigw.ntruss.com/map-geocode/v2/geocode",
            headers={
                "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID,
                "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET
            },
            params={"query": address}
        )
        items = res.json().get("addresses")
        if items:
            return float(items[0]["x"]), float(items[0]["y"])
        return None

    start_coord = geocode(start)
    end_coord = geocode(end)

    if not start_coord or not end_coord:
        return jsonify({"error": "주소 변환 실패"}), 500

    # 경유지: 중간 위도 기준 해안 가까운 지점 (임시)
    waypoint = {"name": "가까운 해안", "address": "임의위치", "coord": [(start_coord[0] + end_coord[0])/2, (start_coord[1] + end_coord[1])/2]}

    # OpenRouteService (또는 NAVER API) 대체 부분 — 임의 경로 생성
    route_geojson = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [start_coord, waypoint["coord"], end_coord]
            }
        }]
    }

    # 관광지 및 맛집 검색
    def search_tourspots(lat, lon, content_type):
        res = requests.get(
            "https://apis.data.go.kr/B551011/KorService1/locationBasedList1",
            params={
                "serviceKey": TOUR_API_KEY,
                "mapX": lon,
                "mapY": lat,
                "radius": 5000,
                "MobileOS": "ETC",
                "MobileApp": "coastaldrive",
                "contentTypeId": content_type,
                "_type": "json",
                "numOfRows": 30
            }
        )
        items = res.json().get("response", {}).get("body", {}).get("items", {}).get("item", [])
        return items

    tour_spots = search_tourspots(end_coord[1], end_coord[0], content_type=12)  # 관광지
    food_spots = search_tourspots(end_coord[1], end_coord[0], content_type=39)  # 음식점

    def format_spot(item, category):
        return {
            "title": item.get("title"),
            "mapx": item.get("mapx"),
            "mapy": item.get("mapy"),
            "firstimage": item.get("firstimage"),
            "addr1": item.get("addr1"),
            "parking": item.get("parking"),
            "usetime": item.get("usetime"),
            "homepage": item.get("homepage"),
            "category": category
        }

    spots = [format_spot(item, "관광지") for item in tour_spots] + \
            [format_spot(item, "음식점") for item in food_spots]

    return jsonify({
        "route": route_geojson,
        "waypoint": {
            "name": waypoint["name"],
            "address": waypoint["address"]
        },
        "spots": spots
    })


if __name__ == "__main__":
    app.run(debug=False, port=10000, host="0.0.0.0")
