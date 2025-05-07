# app.py
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import requests
import json
from urllib.parse import quote

app = Flask(__name__)
CORS(app)

NAVER_CLIENT_ID = "vsdzf1f4n5"
NAVER_CLIENT_SECRET = "0gzctO51PUTVv0gUZU025JYNHPTmVzLS9sGbfYBM"

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/api/geocode", methods=["GET"])
def geocode():
    raw_address = request.args.get("address")
    address = quote(raw_address.strip())

    url = f"https://naveropenapi.apigw.ntruss.com/map-geocode/v2/geocode?query={address}"
    headers = {
        "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID,
        "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET,
    }
    try:
        response = requests.get(url, headers=headers)
        data = response.json()

        if "addresses" not in data or len(data["addresses"]) == 0:
            # 자동 보정 로직 예시 (공백 제거, 도로명 추정 등)
            corrected = raw_address.replace("시청", "청사").replace("  ", " ").strip()
            if corrected != raw_address:
                return jsonify({"status": "retry", "suggested": corrected})
            return jsonify({"status": "fail", "message": "Geocode API returned no results."})

        addr = data["addresses"][0]
        return jsonify({
            "status": "success",
            "lat": float(addr["y"]),
            "lon": float(addr["x"]),
            "roadAddress": addr.get("roadAddress", ""),
            "jibunAddress": addr.get("jibunAddress", "")
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
