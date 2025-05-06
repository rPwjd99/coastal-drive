from flask import Flask, render_template, request, jsonify
import requests
import os

app = Flask(__name__)

VWORLD_API_KEY = os.environ.get("VWORLD_API_KEY")
ORS_API_KEY = os.environ.get("ORS_API_KEY")

@app.route("/")
def index():
    return render_template("index.html")


def geocode(address):
    url = f"https://api.vworld.kr/req/address?service=address&request=getcoord&format=json&type=road&address={address}&key={VWORLD_API_KEY}"
    res = requests.get(url)
    data = res.json()
    try:
        x = float(data['response']['result']['point']['x'])
        y = float(data['response']['result']['point']['y'])
        return [x, y]  # [lon, lat]
    except:
        return None


def get_route(coords):
    url = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
    headers = {
        "Authorization": ORS_API_KEY,
        "Content-Type": "application/json"
    }
    body = {
        "coordinates": coords
    }
    res = requests.post(url, headers=headers, json=body)
    return res.json()


@app.route("/api/route")
def route():
    start_addr = request.args.get("start")
    end_addr = request.args.get("end")
    start_coord = geocode(start_addr)
    end_coord = geocode(end_addr)

    if not start_coord or not end_coord:
        return jsonify({"error": "주소 변환 실패"}), 400

    # 해안선 우회 좌표는 여기서 자동 계산하도록 나중에 추가 (지금은 직선 연결)
    coords = [start_coord, end_coord]
    route = get_route(coords)
    return jsonify(route)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
