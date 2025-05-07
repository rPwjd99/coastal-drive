from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import requests
import geopandas as gpd
import shapely.geometry
import os
from urllib.parse import quote

app = Flask(__name__)
CORS(app)

# 네이버 API 키 (주의: 보안 필요 시 .env 사용 권장)
NAVER_CLIENT_ID = "vsdzf1f4n5"
NAVER_CLIENT_SECRET = "0gzctO51PUTVv0gUZU025JYNHPTmVzLS9sGbfYBM"

# 해안선 GeoJSON 로드 (파일 존재 여부 확인)
coastline_path = "해안선_국가기본도.geojson"
if not os.path.exists(coastline_path):
    raise FileNotFoundError(f"해안선 GeoJSON 파일이 존재하지 않습니다: {coastline_path}")
coastline = gpd.read_file(coastline_path)

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/api/geocode")
def geocode():
    address = request.args.get("address")
    if not address:
        return jsonify({"error": "주소가 제공되지 않았습니다."}), 400

    encoded = quote(address)
    url = f"https://naveropenapi.apigw.ntruss.com/map-geocode/v2/geocode?query={encoded}"
    headers = {
        "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID,
        "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET
    }

    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        return jsonify({"error": "네이버 API 호출 실패", "status": response.status_code}), 500

    data = response.json()
    if "addresses" not in data or len(data["addresses"]) == 0:
        return jsonify({"error": "주소를 찾을 수 없습니다."}), 404

    addr = data["addresses"][0]
    return jsonify({"lat": float(addr["y"]), "lon": float(addr["x"])})

# TODO: 추가로 경로 탐색(OpenRouteService), 해안선 우회 경로 계산 등 API 추가 가능

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, host="0.0.0.0", port=port)
