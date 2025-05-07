# app.py
import os
import requests
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
import urllib.parse

app = Flask(__name__)
CORS(app)

GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")  # Render 환경에서 환경변수로 설정


def geocode_google(address):
    encoded_address = urllib.parse.quote(address)
    url = f"https://maps.googleapis.com/maps/api/geocode/json?address={encoded_address}&key={GOOGLE_API_KEY}"
    response = requests.get(url)
    if response.status_code != 200:
        return None, f"HTTP error {response.status_code}"
    data = response.json()
    if data.get("status") == "OK" and data["results"]:
        location = data["results"][0]["geometry"]["location"]
        return (location["lat"], location["lng"]), None
    return None, data.get("status", "Unknown error")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/geocode", methods=["POST"])
def geocode():
    data = request.json
    address = data.get("address")
    result, error = geocode_google(address)
    if result:
        return jsonify({"status": "success", "location": result})
    else:
        return jsonify({"status": "fail", "error": error})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
