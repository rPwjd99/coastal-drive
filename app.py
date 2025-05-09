from flask import Flask, request, jsonify, render_template
import requests
import json

app = Flask(__name__)

GOOGLE_API_KEY = "AIzaSyC9MSD-WhkqK_Og5YdVYfux21xiRjy2q1M"
ORS_API_KEY = "5b3ce3597851110001cf62486d543846e80049df9c7a9e10ecef2953"

def geocode_address_google(address):
    url = f"https://maps.googleapis.com/maps/api/geocode/json?address={address}&key={GOOGLE_API_KEY}"
    response = requests.get(url).json()
    if response['status'] == 'OK':
        result = response['results'][0]
        latlng = result['geometry']['location']
        formatted_address = result['formatted_address']
        return latlng['lat'], latlng['lng'], formatted_address
    else:
        return None, None, None

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/route', methods=['POST'])
def get_route():
    data = request.get_json()
    start = data['start']
    end = data['end']

    start_lat, start_lng, start_corrected = geocode_address_google(start)
    end_lat, end_lng, end_corrected = geocode_address_google(end)

    if None in [start_lat, start_lng, end_lat, end_lng]:
        return jsonify({'error': 'Invalid address'}), 400

    route_url = "https://api.openrouteservice.org/v2/directions/driving-car"
    headers = {
        'Authorization': ORS_API_KEY,
        'Content-Type': 'application/json'
    }
    body = {
        "coordinates": [[start_lng, start_lat], [end_lng, end_lat]]
    }

    response = requests.post(route_url, headers=headers, json=body)
    if response.status_code != 200:
        return jsonify({'error': 'Route calculation failed'}), 500

    route = response.json()
    return jsonify({
        'geojson': route,
        'start_corrected': start_corrected,
        'end_corrected': end_corrected
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000, debug=True)
