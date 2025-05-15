from flask import Flask, request, jsonify, render_template
import requests
import os

app = Flask(__name__)

# 환경 변수 또는 직접 설정
NAVER_API_KEY_ID = os.getenv("NAVER_API_KEY_ID", "l8jxeiubya")
NAVER_API_KEY_SECRET = os.getenv("NAVER_API_KEY_SECRET", "W8qIqr...")  # 실제 전체 Secret 입력

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/route', methods=['POST'])
def get_route():
    try:
        data = request.get_json()
        start = data.get("start")  # {"lat": xx, "lng": yy}
        end = data.get("end")

        if not start or not end:
            return jsonify({"error": "출발지와 도착지 좌표가 필요합니다."}), 400

        url = "https://naveropenapi.apigw.ntruss.com/map-direction/v1/driving"
        headers = {
            "X-NCP-APIGW-API-KEY-ID": NAVER_API_KEY_ID,
            "X-NCP-APIGW-API-KEY": NAVER_API_KEY_SECRET
        }
        params = {
            "start": f"{start['lng']},{start['lat']}",
            "goal": f"{end['lng']},{end['lat']}",
            "option": "traoptimal"
        }

        res = requests.get(url, headers=headers, params=params)
        if res.status_code != 200:
            return jsonify({
                "error": "NAVER Directions API 오류",
                "status": res.status_code,
                "response": res.text
            }), 500

        route_data = res.json()
        path = route_data["route"]["traoptimal"][0]["path"]

        # GeoJSON 형식으로 변환
        geojson = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": path  # [[lng, lat], [lng, lat], ...]
                },
                "properties": {
                    "name": "경로선"
                }
            }]
        }

        return jsonify(geojson)

    except Exception as e:
        return jsonify({"error": "서버 오류 발생", "message": str(e)}), 500


if __name__ == '__main__':
    app.run(host="0.0.0.0", port=10000)
