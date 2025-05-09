import os
import requests
from flask import Flask, request, jsonify, render_template

app = Flask(__name__)

GOOGLE_API_KEY = "AIzaSyC9MSD-WhkqK_Og5YdVYfux21xiRjy2q1M"
ORS_API_KEY = "5b3ce3597851110001cf62486d543846e80049df9c7a9e10ecef2953"
TOUR_API_KEY = "e1tU33wjMx2nynKjH8yDBm/S4YNne6B8mpCOWtzMH9TSONF71XG/xAwPqyv1fANpgeOvbPY+Le+gM6cYCnWV8w=="

def geocode_address(address):
    url = f"https://maps.googleapis.com/maps/api/geocode/json?address={address}&region=kr&key={GOOGLE_API_KEY}"
    response = requests.get(url).json()
    if response['status'] == 'OK':
        location = response['results'][0]['geometry']['location']
        formatted = response['results'][0]['formatted_address']
        return location['lat'], location['lng'], formatted
    return None, None, None

def get_route(start_coords, end_coords):
    headers = {
        'Authorization': ORS_API_KEY,
        'Content-Type': 'application/json'
    }
    body = {
        "coordinates": [
            [start_coords[1], start_coords[0]],
            [end_coords[1], end_coords[0]]
        ]
    }
    try:
        print("📡 ORS 요청 좌표:", body)
        response = requests.post("https://api.openrouteservice.org/v2/directions/driving-car", headers=headers, json=body)
        print("📡 응답 코드:", response.status_code)
        print("📡 응답 원문:", response.text[:500])  # 응답 길이 제한
        return response.json()
    except Exception as e:
