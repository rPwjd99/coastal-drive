from flask import Flask, request, jsonify, send_from_directory, render_template
import os
import requests
from flask_cors import CORS
from shapely.geometry import Point
import geopandas as gpd
from math import radians, cos, sin, asin, sqrt
from dotenv import load_dotenv

# Load environment variables from Render or .env
load_dotenv()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
ORS_API_KEY = os.getenv("ORS_API_KEY")
VWORLD_API_KEY = os.getenv("VWORLD_API_KEY")
TOURAPI_KEY = os.getenv("TOURAPI_KEY")

app = Flask(__name__, static_folder='static', template_folder='templates')
CORS(app)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/geocode', methods=['POST'])
def geocode():
    address = request.json.get('address')
    if not address:
        return jsonify({'error': '주소가 제공되지 않았습니다.'}), 400

    response = requests.get(
        'https://maps.googleapis.com/maps/api/geocode/json',
        params={'address': address, 'key': GOOGLE_API_KEY}
    )
    data = response.json()

    if data['status'] == 'OK':
        location = data['results'][0]['geometry']['location']
        return jsonify({
            'lat': location['lat'],
            'lng': location['lng']
        })
    else:
        return jsonify({'error': f'Google 지오코딩 실패: {data.get("status")}'})

# 향후 /api/route 및 /api/tourspot 경로 추가 예정

if __name__ == '__main__':
    app.run(debug=True, port=int(os.getenv('PORT', 10000)))
