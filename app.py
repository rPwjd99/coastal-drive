import os
import json
import requests
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from urllib.parse import quote

app = Flask(__name__)
CORS(app)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
ORS_API_KEY = os.getenv("ORS_API_KEY")
TOURAPI_KEY = os.getenv("TOURAPI_KEY")


def correct_address(addr):
    if "세종시청" in addr or ("한누리대로" in addr and "세종" in addr):
        return "세종특별자치시 한누리대로 2130"
    if "보람동" in addr:
        return "세종특별자치시 보람동 218"
    if "속초시청" in addr:
        return "강원도 속초시 중앙로 183"
    if "중앙로" in addr and "속초" in addr:
        return "강원도 속초시 중앙로 183"
    if "중앙동" in addr:
        return "강원도 속초시 중앙동 469-6"
    return addr


def get_coordinates_from_google(address):
    address = correct_address(address)
    url = f"https://maps.googleapis.com/maps/api/geocode/json?address={quote(address)}&key={GOOGLE_API_KEY}"
    try:
        response = requests.get(url)
        data = response.json()
        if data["status"] == "OK":
            location = data["results"][0]["geometry"]["location"]
            return location["lat"], location["lng"]
        else:
            print(f"Google Maps API 실패: {data['status']}")
    except Exception as e:
        print("Google API 요청 예외:", e)
    return None, None


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/geocode", methods=["POST"])
def geocode():
    data = request.get_json()
    address = data.get("address")
    lat, lng = get_coordinates_from_google(address)
    if lat is not None:
        return jsonify({"lat": lat, "lng": lng})
    else:
        return jsonify({"error": "주소를 찾을 수 없습니다."})


@app.route("/tourspots", methods=["POST"])
def tourspots():
    data = request.get_json()
    lat, lng = data.get("lat"), data.get("lng")
    url = (
        f"https://apis.data.go.kr/B551011/KorService1/locationBasedList1?ServiceKey={TOURAPI_KEY}"
        f"&numOfRows=10&pageNo=1&MobileOS=ETC&MobileApp=test&_type=json"
        f"&mapX={lng}&mapY={lat}&radius=5000"
    )
    try:
        response = requests.get(url)
        data = response.json()
        items = data.get("response", {}).get("body", {}).get("items", {}).get("item", [])
        results = []
        for item in items:
            results.append({
                "title": item.get("title"),
                "addr": item.get("addr1"),
                "mapx": item.get("mapx"),
                "mapy": item.get("mapy")
            })
        return jsonify({"spots": results})
    except Exception as e:
        return jsonify({"error": "관광지 정보를 가져오지 못했습니다.", "detail": str(e)})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)), debug=True)
