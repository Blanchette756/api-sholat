import os
import json
import logging
import io
from datetime import date, datetime, timedelta

from flask import Flask, request, jsonify, send_from_directory, abort, Response
from flask_cors import CORS
from dotenv import load_dotenv

try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address
    _has_limiter = True
except ImportError:
    _has_limiter = False

try:
    import jwt as pyjwt
    _has_jwt = True
except ImportError:
    _has_jwt = False

try:
    from openpyxl import Workbook
    _has_openpyxl = True
except ImportError:
    _has_openpyxl = False

from database import Database

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

ADMIN_USER = os.environ.get('ADMIN_USER', '')
ADMIN_PASS = os.environ.get('ADMIN_PASS', '')
JWT_SECRET = os.environ.get('JWT_SECRET', 'fallback-secret-change-me')

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

ALLOWED_STATIC = {'index.html', 'login.html', 'style.css', 'admin-login.html', 'admin.html'}

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

def verify_admin():
    if not _has_jwt:
        return False
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        return False
    token = auth_header[7:]
    try:
        payload = pyjwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        return payload.get("role") == "admin"
    except Exception:
        return False

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

# ── Admin Endpoints ──

@app.route('/admin/login', methods=['POST'])
@_rate_limit("5 per minute")
def admin_login():
    if not ADMIN_USER or not ADMIN_PASS:
        return jsonify({"status": "error", "message": "Admin belum dikonfigurasi"}), 503
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"status": "error", "message": "Data tidak valid"}), 400
    username = data.get('username', '')
    password = data.get('password', '')
    if username == ADMIN_USER and password == ADMIN_PASS:
        if not _has_jwt:
            return jsonify({"status": "error", "message": "JWT tidak tersedia di server"}), 503
        token = pyjwt.encode(
            {"role": "admin", "exp": datetime.utcnow() + timedelta(hours=12)},
            JWT_SECRET,
            algorithm="HS256",
        )
        logger.info("Admin login berhasil")
        return jsonify({"status": "success", "token": token})
    logger.warning("Admin login gagal: username=%s", username)
    return jsonify({"status": "error", "message": "Username atau password salah"}), 401

@app.route('/admin/verify', methods=['GET'])
def admin_verify():
    if verify_admin():
        return jsonify({"status": "ok"})
    return jsonify({"status": "error"}), 401

@app.route('/api/settings', methods=['GET'])
def get_settings():
    try:
        settings = db_manager.get_settings()
        return jsonify(settings)
    except Exception as e:
        logger.exception("Error get settings: %s", e)
        return jsonify({"tolerance_minutes": 30})

@app.route('/api/settings', methods=['POST'])
def update_settings():
    if not verify_admin():
        return jsonify({"status": "error", "message": "Unauthorized"}), 401
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"status": "error", "message": "Data tidak valid"}), 400
    minutes = data.get('tolerance_minutes')
    if minutes not in (30, 60):
        return jsonify({"status": "error", "message": "Toleransi harus 30 atau 60"}), 400
    if db_manager.update_tolerance(minutes):
        logger.info("Toleransi diubah ke %d menit", minutes)
        return jsonify({"status": "success", "message": f"Toleransi diubah ke {minutes} menit"})
    return jsonify({"status": "error", "message": "Gagal update"}), 500

@app.route('/api/students', methods=['GET'])
def get_students():
    if not verify_admin():
        return jsonify({"status": "error", "message": "Unauthorized"}), 401
    try:
        records = db_manager.get_all_records()
        return jsonify(records)
    except Exception as e:
        logger.exception("Error get students: %s", e)
        return jsonify({"status": "error", "message": "Gagal mengambil data"}), 500

@app.route('/api/export', methods=['GET'])
def export_excel():
    if not verify_admin():
        return jsonify({"status": "error", "message": "Unauthorized"}), 401
    if not _has_openpyxl:
        return jsonify({"status": "error", "message": "openpyxl tidak tersedia"}), 503
    try:
        records = db_manager.get_all_records()
        wb = Workbook()
        ws = wb.active
        ws.title = "Laporan Sholat"
        ws.append(["Email", "Tanggal", "Subuh", "Dzuhur", "Ashar", "Maghrib", "Isya"])
        for r in records:
            s = r.get("sholat", {})
            ws.append([
                r.get("email", ""),
                r.get("tanggal", ""),
                "Ya" if s.get("subuh") else "Tidak",
                "Ya" if s.get("dzuhur") else "Tidak",
                "Ya" if s.get("ashar") else "Tidak",
                "Ya" if s.get("maghrib") else "Tidak",
                "Ya" if s.get("isya") else "Tidak",
            ])
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return Response(
            buf.getvalue(),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment; filename=laporan_sholat.xlsx"},
        )
    except Exception as e:
        logger.exception("Error export: %s", e)
        return jsonify({"status": "error", "message": "Gagal export"}), 500

if __name__ == '__main__':
    print("=======================================")
    print("    MENGHIDUPKAN MESIN SERVER API      ")
    print("=======================================")
    print("Server berjalan di port 5000...")
    app.run(host='0.0.0.0', port=5000, debug=False)
