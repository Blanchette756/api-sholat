import os
import json
import logging
from datetime import date

from flask import Flask, request, jsonify, send_from_directory, abort
from flask_cors import CORS
from dotenv import load_dotenv

try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address
    _has_limiter = True
except ImportError:
    _has_limiter = False

from database import Database

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

firebase_enabled = False
try:
    import firebase_admin
    from firebase_admin import credentials, auth as firebase_auth
    cred_json = os.environ.get('FIREBASE_SERVICE_ACCOUNT')
    if cred_json:
        cred = credentials.Certificate(json.loads(cred_json))
        firebase_admin.initialize_app(cred)
        firebase_enabled = True
        logger.info("Firebase authentication aktif")
    else:
        logger.warning("FIREBASE_SERVICE_ACCOUNT tidak di-set, semua request akan ditolak")
except Exception as e:
    logger.error("Gagal inisialisasi Firebase: %s", e)

app = Flask(__name__)

cors_origins = os.environ.get('CORS_ORIGINS', '')
if cors_origins:
    CORS(app, origins=cors_origins.split(','))
else:
    logger.warning("CORS_ORIGINS tidak di-set, cross-origin requests akan diblokir")

if _has_limiter:
    limiter = Limiter(get_remote_address, app=app, default_limits=["60 per minute"])
else:
    limiter = None
    logger.warning("Flask-Limiter tidak tersedia, rate limiting nonaktif")

db_manager = Database()

ALLOWED_STATIC = {'index.html', 'login.html', 'style.css'}

@app.after_request
def set_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy'] = 'geolocation=(), microphone=(), camera=()'
    if request.is_secure:
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    return response

def verify_token():
    if not firebase_enabled:
        return None
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        return None
    token = auth_header[7:]
    try:
        decoded = firebase_auth.verify_id_token(token)
        return decoded
    except Exception as e:
        logger.warning("Token verification gagal: %s", e)
        return None

@app.route('/')
def index():
    return send_from_directory('.', 'login.html')

@app.route('/<path:path>')
def static_files(path):
    if path in ALLOWED_STATIC:
        return send_from_directory('.', path)
    abort(404)

def _rate_limit(limit_string):
    if limiter:
        return limiter.limit(limit_string)
    return lambda f: f

@app.route('/sholat', methods=['POST'])
@_rate_limit("10 per minute")
def terima():
    try:
        user = verify_token()
        if not user:
            return jsonify({"status": "error", "message": "Silakan login terlebih dahulu"}), 401

        data = request.get_json(silent=True)
        if not data or not isinstance(data, dict):
            return jsonify({"status": "error", "message": "Data JSON tidak valid"}), 400

        subuh = bool(data.get('subuh', False))
        dzuhur = bool(data.get('dzuhur', False))
        ashar = bool(data.get('ashar', False))
        maghrib = bool(data.get('maghrib', False))
        isya = bool(data.get('isya', False))

        uid = user.get('uid', '')
        email = user.get('email', '')

        success, message = db_manager.sholat(date.today(), subuh, dzuhur, ashar, maghrib, isya, uid, email)
        if success:
            logger.info("Laporan sholat dari uid=%s berhasil", uid)
            return jsonify({"status": "success", "message": message}), 201
        else:
            return jsonify({"status": "error", "message": message}), 400
    except Exception as e:
        logger.exception("Error pada endpoint /sholat: %s", e)
        return jsonify({"status": "error", "message": "Terjadi kesalahan server"}), 500

if __name__ == '__main__':
    print("=======================================")
    print("    MENGHIDUPKAN MESIN SERVER API      ")
    print("=======================================")
    print("Server berjalan di port 5000...")
    app.run(host='0.0.0.0', port=5000, debug=False)
