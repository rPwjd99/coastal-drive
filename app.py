import requests
from flask import Flask, request, jsonify, render_template
from beaches_coordinates import beach_coords
from geopy.distance import geodesic
import os

app = Flask(__name__)

# Google Maps Geocoding API Key (환경변수 또는 직접 삽입)
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY") or "<YOUR_GOOGLE_API_KEY_HERE>"


def geocode_address(address):
    url = f"https://maps.googleapis.com/maps/api/geocode/json"
    params = {"address": address, "key": GOOGLE_API_KEY}
    response = requests.get(url, params=params)
    data = response.json()
    if data['status'] == 'OK':
        location = data['results'][0]['geometry']['location']
        return location['lat'], location['lng']
    else:
        return None


def find_closest_beach_by_lat_or_lon(start_coord, by='lat'):
    min_dist = float('inf')
    best_beach = None
    for name, (lon, lat) in beach_coords.items():
        if by == 'lat':
            delta = abs(start_coord[0] - lat)
        else:
            delta = abs(start_coord[1] - lon)
        dist = geodesic(start_coord, (lat, lon)).km
        if delta < 1.0 and dist < min_dist:
            min_dist = dist
            best_beach = (name, lat, lon)
    return best_beach


def select_waypoint(start_coord, end_coord):
    by_lat = find_closest_beach_by_lat_or_lon(start_coord, by='lat')
    by_lon = find_closest_beach_by_lat_or_lon(start_coord, by='lon')
    
    if not by_lat and not by_lon:
        return None
    
    dist_lat = geodesic(start_coord, (by_lat[1], by_lat[2])).km if by_lat else float('inf')
    dist_lon = geodesic(start_coord, (by_lon[1], by_lon[2])).km if by_lon else float('inf')

    return by_lat if dist_lat <= dist_lon else by_lon


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/route', methods=['POST'])
def get_route():
    data = request.get_json()
    start_addr = data.get('start')
    end_addr = data.get('end')

    start_coord = geocode_address(start_addr)
    end_coord = geocode_address(end_addr)

    if not start_coord or not end_coord:
        return jsonify({'error': '주소 변환 실패'}), 400

    waypoint = select_waypoint(start_coord, end_coord)
    if not waypoint:
        return jsonify({'error': '적절한 해안 웨이포인트를 찾을 수 없음'}), 400

    route_data = {
        'start': {'lat': start_coord[0], 'lng': start_coord[1]},
        'end': {'lat': end_coord[0], 'lng': end_coord[1]},
        'waypoint': {'name': waypoint[0], 'lat': waypoint[1], 'lng': waypoint[2]}
    }
    return jsonify(route_data)


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')
