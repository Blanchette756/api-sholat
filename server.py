import os
import json
from datetime import date

from flask import Flask, request, jsonify, send_from_directory, abort
from flask_cors import CORS
from dotenv import load_dotenv

import firebase_admin
from firebase_admin import credentials, auth as firebase_auth

from database import Database

load_dotenv()

cred_json = os.environ.get('FIREBASE_SERVICE_ACCOUNT')
if cred_json:
    cred = credentials.Certificate(json.loads(cred_json))
    firebase_admin.initialize_app(cred)

app = Flask(__name__)
CORS(app, origins=os.environ.get('CORS_ORIGINS', '*').split(','))
db_manager = Database()

ALLOWED_STATIC = {'index.html', 'login.html', 'style.css', 'script.js'}

def verify_token():
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        return None
    token = auth_header[7:]
    try:
        decoded = firebase_auth.verify_id_token(token)
        return decoded
    except Exception:
        return None

@app.route('/')
def index():
    return send_from_directory('.', 'login.html')

@app.route('/<path:path>')
def static_files(path):
    if path in ALLOWED_STATIC:
        return send_from_directory('.', path)
    abort(404)

@app.route('/sholat', methods=['POST'])
def terima():
    try:
        user = verify_token()
        if not user:
            return jsonify({"status": "error", "message": "Silakan login terlebih dahulu"}), 401

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

        uid = user.get('uid', '')
        email = user.get('email', '')

        success, message = db_manager.sholat(name, date.today(), subuh, dzuhur, ashar, maghrib, isya, uid, email)
        if success:
            return jsonify({"status": "success", "message": message}), 201
        else:
            return jsonify({"status": "error", "message": message}), 400
    except Exception:
        return jsonify({"status": "error", "message": "Terjadi kesalahan server"}), 500

if __name__ == '__main__':
    print("=======================================")
    print("    MENGHIDUPKAN MESIN SERVER API      ")
    print("=======================================")
    print("Server berjalan di port 5000...")
    app.run(host='0.0.0.0', port=5000, debug=True)
