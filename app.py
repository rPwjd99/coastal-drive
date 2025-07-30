from flask import Flask, render_template, request, jsonify
import requests
import math

app = Flask(__name__)

# 한국관광공사 TourAPI 키
TOURAPI_KEY = "e1tU33wjMx2nynKjH8yDBm/S4YNne6B8mpCOWtzMH9TSONF71XG/xAwPqyv1fANpgeOvbPY+Le+gM6cYCnWV8w=="

# 거리 계산 함수
def haversine(lat1, lon1, lat2, lon2):
    R = 6371  # 지구 반지름 (km)
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(d_lambda/2)**2
    return R * (2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)))

# 관광지/맛집 검색
def search_tourapi(lat, lon, radius=5000):
    content_type_ids = {'tourist': '12', 'restaurant': '39'}
    results = {'tourist': [], 'restaurant': []}

    for category, content_type_id in content_type_ids.items():
        url = (
            f"http://apis.data.go.kr/B551011/KorService1/locationBasedList1"
            f"?MobileOS=ETC&MobileApp=coastaldrive&arrange=E&contentTypeId={content_type_id}"
            f"&mapX={lon}&mapY={lat}&radius={radius}&listYN=Y&_type=json&numOfRows=100"
            f"&serviceKey={TOURAPI_KEY}"
        )
        try:
            response = requests.get(url)
            data = response.json()
            items = data['response']['body']['items']['item']
            for item in items:
                results[category].append({
                    'title': item.get('title'),
                    'addr': item.get('addr1', ''),
                    'mapx': float(item.get('mapx', 0)),
                    'mapy': float(item.get('mapy', 0)),
                    'image': item.get('firstimage', ''),
                    'parking': item.get('parking', ''),
                    'usetime': item.get('usetime', ''),
                    'contentid': item.get('contentid')
                })
        except Exception as e:
            print(f"[ERROR] {category} API 오류:", e)
    return results

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/route', methods=['POST'])
def route():
    try:
        data = request.get_json()
        start = data['start']
        end = data['end']

        # 중간 좌표 계산
        mid_lat = (start['lat'] + end['lat']) / 2
        mid_lon = (start['lon'] + end['lon']) / 2

        # 주변 관광지 및 음식점 검색
        spots = search_tourapi(mid_lat, mid_lon)

        return jsonify({
            'route': {
                'start': start,
                'end': end,
                'midpoint': {'lat': mid_lat, 'lon': mid_lon}
            },
            'spots': spots
        })
    except Exception as e:
        print("[ERROR] route 처리 실패:", e)
        return jsonify({'error': '경로 계산 중 오류 발생'}), 500

if __name__ == '__main__':
    app.run(debug=False)
