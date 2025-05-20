import os
import requests
from flask import Flask, request, jsonify, render_template

app = Flask(__name__)

# NAVER API 직접 입력
NAVER_ID = "4etplzn46c"
NAVER_SECRET = "mHHltk1um0D09kTbRbbdJLN0MDpA0SXLboPlHx1F"

# NAVER 주소 → 좌표 변환 함수
def geocode_naver(address):
    url = "https://naveropenapi.apigw.ntruss.com/map-geocode/v2/geocode"
    headers = {
        "X-NCP-APIGW-API-KEY-ID": NAVER_ID,
        "X-NCP-APIGW-API-KEY": NAVER_SECRET
    }
    params = {"query": address}
    print(f"📤 NAVER 지오코딩 요청: {address}")
    try:
        res = requests.get(url, headers=headers, params=params, timeout=5)
        print("📥 응답 코드:", res.status_code)
        data = res.json()
        if data.get("addresses"):
            addr = data["addresses"][0]
            lat, lon = float(addr["y"]), float(addr["x"])
            print(f"✅ 주소 변환 성공: {address} → ({lat}, {lon})")
            return lat, lon
        else:
            print("❌ 주소 변환 실패 - 응답 내용:", data)
    except Exception as e:
        print("❌ NAVER 지오코딩 예외:", e)
    return None

# NAVER Directions 15 API 요청
def get_naver_route(start, end):
    url = "https://naveropenapi.apigw.ntruss.com/map-direction-15/v1/driving"
    headers = {
        "X-NCP-APIGW-API-KEY-ID": NAVER_ID,
        "X-NCP-APIGW-API-KEY": NAVER_SECRET
    }
    params = {
        "start": f"{start[1]},{start[0]}",
        "goal": f"{end[1]},{end[0]}",
        "option": "trafast",
        "cartype": 1,
        "fueltype": "gasoline",
        "mileage": 14,
        "lang": "ko"
    }
    print("📦 NAVER 경로 요청 파라미터:", params)

    res = requests.get(url, headers=headers, params=params)
    print("📡 NAVER Directions 응답 코드:", res.status_code)
    try:
        return res.json(), res.status_code
    except Exception as e:
        return {"error": str(e)}, 500

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/route", methods=["POST"])
def route():
    try:
        data = request.get_json()
        start_addr = data.get("start", "").strip()
        end_addr = data.get("end", "").strip()
        print("📥 입력된 주소:", start_addr, "→", end_addr)

        start = geocode_naver(start_addr)
        end = geocode_naver(end_addr)

        if not start or not end:
            print("❌ 주소 변환 실패:", start_addr, "→", start, "|", end_addr, "→", end)
            return jsonify({"error": "❌ 주소 변환 실패"}), 400

        route_data, status = get_naver_route(start, end)
        return jsonify(route_data), status

    except Exception as e:
        print("❌ 서버 오류:", str(e))
        return jsonify({"error": f"❌ 서버 내부 오류: {str(e)}"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
