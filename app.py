from flask import Flask, request, jsonify, render_template
from beaches_coordinates import beach_coords

app = Flask(__name__)

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/beaches", methods=["GET"])
def list_beaches():
    return jsonify(list(beach_coords.keys()))

@app.route("/beach", methods=["GET"])
def get_beach_coordinates():
    name = request.args.get("name")
    if not name:
        return jsonify({"error": "❌ 'name' 파라미터가 필요합니다."}), 400

    coords = beach_coords.get(name)
    if coords:
        return jsonify({
            "name": name,
            "longitude": coords[0],
            "latitude": coords[1]
        })
    else:
        return jsonify({"error": f"'{name}' 해수욕장을 찾을 수 없습니다."}), 404

if __name__ == "__main__":
    app.run(debug=True, port=5000)
