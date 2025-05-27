# app.py
from flask import Flask, request, jsonify
from flask_cors import CORS
import os
from beaches_coordinates import beach_coords  # ✅ 같은 폴더 내 .py 파일

app = Flask(__name__)
CORS(app)

@app.route('/')
def index():
    return "🏖️ CoastalDrive 앱이 실행 중입니다!"

@app.route('/route', methods=['POST'])
def route():
    data = request.get_json()
    beach_name = data.get("beach")

    if not beach_name:
        return jsonify({"error": "해수욕장 이름이 누락되었습니다."}), 400

    coords = beach_coords.get(beach_name)
    if coords:
        return jsonify({"name": beach_name, "coords": coords})
    else:
        return jsonify({"error": "해당 해수욕장 정보를 찾을 수 없습니다."}), 404

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))  # ✅ Render는 PORT 환경변수를 자동 할당
    app.run(host='0.0.0.0', port=port, debug=True)  # ✅ 0.0.0.0 필수
