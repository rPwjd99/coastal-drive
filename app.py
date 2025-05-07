# app.py
from flask import Flask, request, jsonify, render_template
import requests
import os

app = Flask(__name__)

GOOGLE_API_KEY = "AIzaSyC9MSD-WhkqK_Og5YdVYfux21xiRjy2q1M"

def geocode_address(address):
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {"address": address, "key": GOOGLE_API_KEY}
    response = requests.get(url, params=params)
    data = response.json()
    if data.get("status") == "OK":
        loc = data["results"][0]["geometry"]["location"]
        return loc["lat"], loc["lng"]
    return None, None

def get_route(start_coords, end_coords):
    url = "https://maps.googleapis.com/maps/api/directions/json"
    params = {
        "origin": f"{start_coords[0]},{start_coords[1]}",
        "destination": f"{end_coords[0]},{end_coords[1]}",
        "key": GOOGLE_API_KEY,
        "mode": "driving"
    }
    response = requests.get(url, params=params)
    return response.json()

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/route", methods=["POST"])
def route():
    data = request.json
    start = data.get("start")
    end = data.get("end")

    start_lat, start_lng = geocode_address(start)
    end_lat, end_lng = geocode_address(end)

    if not all([start_lat, start_lng, end_lat, end_lng]):
        return jsonify({"error": "주소를 확인할 수 없습니다."}), 400

    route_data = get_route((start_lat, start_lng), (end_lat, end_lng))
    return jsonify(route_data)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
