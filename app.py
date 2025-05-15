from flask import Flask, request, jsonify, render_template
import requests
import os

app = Flask(__name__)

NAVER_API_KEY_ID = os.getenv("NAVER_API_KEY_ID", "l8jxeiubya")
NAVER_API_KEY_SECRET = os.getenv("NAVER_API_KEY_SECRET", "W8qIqr...")  # 실제 전체 키 입력

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/route', methods=['POST'])
def route():
    try:
        data = request.get_json()
        start = data.get("start")
        end = data.get("end")

        if not start or not end:
            return jsonify({"error": "좌표 정보가 누락되었습니다."}), 400

        start_coord = f"{start['lng']},{start['lat']}"
        end_coord = f"{end['lng']},{end['lat']}"

        url = "https://naveropenapi.apigw.ntruss.com/map-direction/v1/driving"
        headers = {
            "X-NCP-APIGW-API-KEY-ID": NAVER_API_KEY_ID,
            "X-NCP-APIGW-API-KEY": NAVER_API_KEY_SECRET
        }
        params = {
            "start": start_coord,
            "goal": end_coord,
            "option": "traoptimal"
        }

        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()  # 요청 실패 시 예외 발생

        res_json = response.json()
        if "route" not in res_json or "traoptimal" not in res_json["route"]:
            return jsonify({"error": "응답 형식이 올바르지 않습니다.", "response": res_json}), 500

        path = res_json["route"]["traoptimal"][0]["path"]

        geojson = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": path
                },
                "properties": {"name": "경로선"}
            }]
        }

        return jsonify(geojson)

    except requests.exceptions.RequestException as e:
        print(f"❌ NAVER API 요청 실패: {e}")
        return jsonify({"error": "NAVER API 요청 실패", "message": str(e)}), 500

    except Exception as e:
        print(f"❌ 서버 오류 발생: {e}")
        return jsonify({"error": "서버 내부 오류", "message": str(e)}), 500

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=10000, debug=True)  # 개발 시 debug=True
