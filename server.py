from flask import Flask, request, jsonify, send_from_directory, abort
from database import Database
from datetime import date
from flask_cors import CORS
from dotenv import load_dotenv
import os

load_dotenv()

app = Flask(__name__)
CORS(app, origins=os.environ.get('CORS_ORIGINS', '*').split(','))
db_manager = Database()

ALLOWED_STATIC = {'index.html', 'style.css', 'script.js'}

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/<path:path>')
def static_files(path):
    if path in ALLOWED_STATIC:
        return send_from_directory('.', path)
    abort(404)

@app.route('/sholat', methods=['POST'])
def terima():
    try:
        data = request.get_json(silent=True)
        if not data or not isinstance(data, dict):
            return jsonify({"status": "error", "message": "Data JSON tidak valid"}), 400

        name = data.get('nama')
        if not isinstance(name, str):
            return jsonify({"status": "error", "message": "Nama harus berupa teks"}), 400

        subuh = bool(data.get('subuh', False))
        dzuhur = bool(data.get('dzuhur', False))
        ashar = bool(data.get('ashar', False))
        maghrib = bool(data.get('maghrib', False))
        isya = bool(data.get('isya', False))

        success, message = db_manager.sholat(name, date.today(), subuh, dzuhur, ashar, maghrib, isya)
        if success:
            return jsonify({"status": "success", "message": message}), 201
        else:
            return jsonify({"status": "error", "message": message}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": "Terjadi kesalahan server"}), 500

if __name__ == '__main__':
    print("=======================================")
    print("    MENGHIDUPKAN MESIN SERVER API      ")
    print("=======================================")
    print("Server berjalan di port 5000...")
    app.run(host='0.0.0.0', port=5000, debug=True)
