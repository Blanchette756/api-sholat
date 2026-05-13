import os
import json
import logging
import io
from datetime import date, datetime, timedelta, timezone

from flask import Flask, request, jsonify, send_from_directory, abort, Response, redirect, make_response
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
JWT_SECRET = os.environ.get('JWT_SECRET', '')

if not JWT_SECRET:
    logger.warning("JWT_SECRET tidak di-set. Admin login akan gagal.")

ADMIN_COOKIE = 'reve_admin'


def set_admin_cookie(response, token):
    """Set HTTP-only admin session cookie."""
    response.set_cookie(
        ADMIN_COOKIE,
        token,
        httponly=True,
        secure=True,
        samesite='Strict',
        max_age=12 * 3600,
        path='/',
    )
    return response


def verify_admin_cookie():
    """Verify admin JWT from cookie (server-side page guarding)."""
    if not _has_jwt or not JWT_SECRET:
        return False
    token = request.cookies.get(ADMIN_COOKIE, '')
    if not token:
        return False
    try:
        payload = pyjwt.decode(token, JWT_SECRET, algorithms=['HS256'])
        return payload.get('role') == 'admin'
    except Exception:
        return False


WITA = timezone(timedelta(hours=8))

PRAYER_ORDER = ['subuh', 'dzuhur', 'ashar', 'maghrib', 'isya']

PRAYER_TIMES_APPROX = {
    'subuh': (4, 20),
    'dzuhur': (12, 0),
    'ashar': (15, 15),
    'maghrib': (18, 10),
    'isya': (19, 20),
}


def get_wita_now():
    return datetime.now(WITA)


def get_wita_date_str():
    return get_wita_now().strftime('%Y-%m-%d')


def is_prayer_in_window(prayer_id, tolerance_minutes=60):
    if prayer_id not in PRAYER_TIMES_APPROX:
        return False
    h, m = PRAYER_TIMES_APPROX[prayer_id]
    now = get_wita_now()
    start_mins = h * 60 + m
    now_mins = now.hour * 60 + now.minute
    idx = PRAYER_ORDER.index(prayer_id)
    if idx < len(PRAYER_ORDER) - 1:
        nh, nm = PRAYER_TIMES_APPROX[PRAYER_ORDER[idx + 1]]
        # extend end window by tolerance so users can still check just after next prayer starts
        end_mins = nh * 60 + nm + tolerance_minutes
    else:
        end_mins = 23 * 60 + 59
    # Hanya boleh checklist SETELAH waktu sholat masuk (tidak boleh early check)
    return start_mins <= now_mins < end_mins


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

ALLOWED_STATIC = {'index.html', 'login.html', 'style.css', 'admin-login.html'}


@app.after_request
def set_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy'] = 'geolocation=(), microphone=(), camera=()'
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://www.gstatic.com https://apis.google.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "connect-src 'self' https://*.googleapis.com https://*.firebaseapp.com https://api.aladhan.com https://identitytoolkit.googleapis.com https://securetoken.googleapis.com; "
        "frame-src https://*.firebaseapp.com https://accounts.google.com;"
    )
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
    if not _has_jwt or not JWT_SECRET:
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


@app.route('/ping')
def ping():
    return jsonify({"status": "ok"}), 200



@app.route('/admin.html')
def admin_page():
    # Serve admin.html only if a valid admin cookie is present
    if not verify_admin_cookie():
        return redirect('/admin-login.html')
    return send_from_directory('.', 'admin.html')

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


# ── Prayer Check (individual toggle with server-side validation) ──

@app.route('/sholat/check', methods=['POST'])
@_rate_limit("30 per minute")
def check_prayer():
    try:
        user = verify_token()
        if not user:
            return jsonify({"status": "error", "message": "Silakan login terlebih dahulu"}), 401

        data = request.get_json(silent=True)
        if not data or not isinstance(data, dict):
            return jsonify({"status": "error", "message": "Data tidak valid"}), 400

        prayer = data.get('prayer', '')
        checked = bool(data.get('checked', False))

        if prayer not in PRAYER_TIMES_APPROX:
            return jsonify({"status": "error", "message": "Sholat tidak valid"}), 400

        uid = user.get('uid', '')
        email = user.get('email', '')
        nama = user.get('name', email)
        tanggal = get_wita_date_str()

        tolerance = db_manager.get_settings().get('tolerance_minutes', 60)
        if checked and not is_prayer_in_window(prayer, tolerance):
            return jsonify({"status": "error", "message": "Di luar waktu sholat"}), 403

        success, msg = db_manager.check_prayer(uid, email, nama, tanggal, prayer, checked)
        if success:
            return jsonify({"status": "success"})
        return jsonify({"status": "error", "message": msg}), 400
    except Exception as e:
        logger.exception("Error pada /sholat/check: %s", e)
        return jsonify({"status": "error", "message": "Terjadi kesalahan server"}), 500


@app.route('/sholat/today', methods=['GET'])
@_rate_limit("30 per minute")
def get_today():
    try:
        user = verify_token()
        if not user:
            return jsonify({"status": "error", "message": "Login dulu"}), 401

        uid = user.get('uid', '')
        # Terima date param opsional (untuk recap EOD tengah malam → ambil data kemarin)
        date_param = request.args.get('date', '').strip()
        if date_param:
            import re
            if not re.match(r'^\d{4}-\d{2}-\d{2}$', date_param):
                return jsonify({"status": "error", "message": "Format tanggal tidak valid"}), 400
            tanggal = date_param
        else:
            tanggal = get_wita_date_str()
        status = db_manager.get_today_status(uid, tanggal)
        return jsonify(status)
    except Exception as e:
        logger.exception("Error pada /sholat/today: %s", e)
        return jsonify({"status": "error"}), 500


@app.route('/sholat/finalize', methods=['POST'])
@_rate_limit("5 per minute")
def finalize():
    try:
        user = verify_token()
        if not user:
            return jsonify({"status": "error", "message": "Login dulu"}), 401

        uid = user.get('uid', '')
        tanggal = get_wita_date_str()

        status = db_manager.get_today_status(uid, tanggal)
        sholat = status.get('sholat', {})
        total = sum(1 for v in sholat.values() if v)
        if total == 0:
            return jsonify({"status": "error", "message": "Belum ada sholat yang diceklis"}), 400

        db_manager.finalize_day(uid, tanggal)
        logger.info("Laporan finalized uid=%s tanggal=%s total=%d", uid, tanggal, total)
        return jsonify({"status": "success", "message": f"Laporan dikirim ({total}/5)"})
    except Exception as e:
        logger.exception("Error pada /sholat/finalize: %s", e)
        return jsonify({"status": "error", "message": "Terjadi kesalahan server"}), 500


# ── Legacy batch submit (backward compat) ──

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
        nama = user.get('name', email)
        tanggal = get_wita_date_str()

        success, message = db_manager.sholat(tanggal, subuh, dzuhur, ashar, maghrib, isya, uid, email, nama)
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
    if not JWT_SECRET:
        return jsonify({"status": "error", "message": "JWT_SECRET belum di-set"}), 503
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"status": "error", "message": "Data tidak valid"}), 400
    username = data.get('username', '')
    password = data.get('password', '')
    if username == ADMIN_USER and password == ADMIN_PASS:
        if not _has_jwt:
            return jsonify({"status": "error", "message": "JWT tidak tersedia di server"}), 503
        token = pyjwt.encode(
            {"role": "admin", "exp": datetime.now(timezone.utc) + timedelta(hours=12)},
            JWT_SECRET,
            algorithm="HS256",
        )
        logger.info("Admin login berhasil")
        resp = make_response(jsonify({"status": "success", "token": token}))
        set_admin_cookie(resp, token)
        return resp
    logger.warning("Admin login gagal: username=%s", username)
    return jsonify({"status": "error", "message": "Username atau password salah"}), 401


@app.route('/admin/verify', methods=['GET'])
@_rate_limit("20 per minute")
def admin_verify():
    if verify_admin():
        return jsonify({"status": "ok"})
    return jsonify({"status": "error"}), 401


@app.route('/admin/logout', methods=['POST'])
def admin_logout():
    resp = make_response(jsonify({"status": "ok"}))
    resp.delete_cookie(ADMIN_COOKIE, path='/')
    logger.info("Admin logout")
    return resp

@app.route('/api/statistik', methods=['GET'])
def get_statistik():
    if not verify_admin():
        return jsonify({"status": "error", "message": "Unauthorized"}), 401
    try:
        # Rentang waktu default disesuaikan dengan tanggal sosialisasi
        start_date = request.args.get('start', '2026-05-12')
        end_date = request.args.get('end', '2026-05-18')
        data = db_manager.get_statistik_lomba(start_date=start_date, end_date=end_date)
        return jsonify({"status": "success", "data": data})
    except Exception as e:
        logger.exception("Error /api/statistik: %s", e)
        return jsonify({"status": "error", "message": "Gagal mengambil data"}), 500

@app.route('/api/settings', methods=['GET'])
def get_settings():
    try:
        settings = db_manager.get_settings()
        return jsonify(settings)
    except Exception as e:
        logger.exception("Error get settings: %s", e)
        return jsonify({"tolerance_minutes": 60})


@app.route('/api/settings', methods=['POST'])
def update_settings():
    if not verify_admin():
        return jsonify({"status": "error", "message": "Unauthorized"}), 401
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"status": "error", "message": "Data tidak valid"}), 400
    minutes = data.get('tolerance_minutes')
    if minutes not in (60, 120):
        return jsonify({"status": "error", "message": "Toleransi harus 60 atau 120 menit"}), 400
    if db_manager.update_tolerance(minutes):
        logger.info("Toleransi diubah ke %d menit", minutes)
        return jsonify({"status": "success", "message": f"Toleransi diubah ke {minutes} menit"})
    return jsonify({"status": "error", "message": "Gagal update"}), 500


@app.route('/api/students', methods=['GET'])
def get_students():
    if not verify_admin():
        return jsonify({"status": "error", "message": "Unauthorized"}), 401
    try:
        page = request.args.get('page', 1, type=int)
        limit = min(request.args.get('limit', 50, type=int), 200)
        date_from = request.args.get('from', '')
        date_to = request.args.get('to', '')
        records, total = db_manager.get_all_records(
            page=page, limit=limit, date_from=date_from, date_to=date_to
        )
        return jsonify({"data": records, "total": total, "page": page, "limit": limit})
    except Exception as e:
        logger.exception("Error get students: %s", e)
        return jsonify({"status": "error", "message": "Gagal mengambil data"}), 500


# ── Public Scoreboard (hari ini, semua user login) ──

@app.route('/api/scoreboard', methods=['GET'])
@_rate_limit("30 per minute")
def get_scoreboard():
    try:
        user = verify_token()
        if not user:
            return jsonify({"status": "error", "message": "Login dulu"}), 401
        tanggal = get_wita_date_str()
        data = db_manager.get_today_scoreboard(tanggal)
        return jsonify({"status": "success", "data": data, "tanggal": tanggal})
    except Exception as e:
        logger.exception("Error /api/scoreboard: %s", e)
        return jsonify({"status": "error", "message": "Gagal mengambil data"}), 500


# ── Save end-of-day reason ──

@app.route('/sholat/reason', methods=['POST'])
@_rate_limit("5 per minute")
def save_reason():
    try:
        user = verify_token()
        if not user:
            return jsonify({"status": "error", "message": "Login dulu"}), 401
        data = request.get_json(silent=True)
        if not data:
            return jsonify({"status": "error", "message": "Data tidak valid"}), 400
        reason = data.get('reason', '').strip()
        if not reason:
            return jsonify({"status": "error", "message": "Alasan tidak boleh kosong"}), 400
        uid = user.get('uid', '')
        # Gunakan date param jika ada (untuk EOD tengah malam → simpan ke tanggal kemarin)
        date_param = data.get('date', '').strip()
        if date_param:
            import re
            if re.match(r'^\d{4}-\d{2}-\d{2}$', date_param):
                tanggal = date_param
            else:
                tanggal = get_wita_date_str()
        else:
            tanggal = get_wita_date_str()
        db_manager.save_reason(uid, tanggal, reason)
        logger.info("Alasan disimpan uid=%s tanggal=%s", uid, tanggal)
        return jsonify({"status": "success", "message": "Alasan berhasil disimpan"})
    except Exception as e:
        logger.exception("Error /sholat/reason: %s", e)
        return jsonify({"status": "error", "message": "Terjadi kesalahan server"}), 500


# ── Weekly report ──

@app.route('/api/weekly', methods=['GET'])
def get_weekly():
    if not verify_admin():
        return jsonify({"status": "error", "message": "Unauthorized"}), 401
    try:
        date_from = request.args.get('from', '')
        date_to = request.args.get('to', '')
        data = db_manager.get_weekly_summary(date_from=date_from, date_to=date_to)
        return jsonify({"data": data})
    except Exception as e:
        logger.exception("Error /api/weekly: %s", e)
        return jsonify({"status": "error", "message": "Gagal mengambil data"}), 500


# ── Monthly report ──

@app.route('/api/monthly', methods=['GET'])
def get_monthly():
    if not verify_admin():
        return jsonify({"status": "error", "message": "Unauthorized"}), 401
    try:
        date_from = request.args.get('from', '')
        date_to = request.args.get('to', '')
        data = db_manager.get_monthly_summary(date_from=date_from, date_to=date_to)
        return jsonify({"data": data})
    except Exception as e:
        logger.exception("Error /api/monthly: %s", e)
        return jsonify({"status": "error", "message": "Gagal mengambil data"}), 500


@app.route('/api/export', methods=['GET'])
def export_excel():
    if not verify_admin():
        return jsonify({"status": "error", "message": "Unauthorized"}), 401
    if not _has_openpyxl:
        return jsonify({"status": "error", "message": "openpyxl tidak tersedia"}), 503
    try:
        date_from = request.args.get('from', '')
        date_to = request.args.get('to', '')
        records, _ = db_manager.get_all_records(page=1, limit=10000, date_from=date_from, date_to=date_to)
        wb = Workbook()
        ws = wb.active
        ws.title = "Laporan Sholat"
        ws.append(["Nama", "Email", "Tanggal", "Subuh", "Dzuhur", "Ashar", "Maghrib", "Isya", "Status", "Alasan"])
        for r in records:
            s = r.get("sholat", {})
            ws.append([
                r.get("nama", ""),
                r.get("email", ""),
                r.get("tanggal", ""),
                "Ya" if s.get("subuh") else "Tidak",
                "Ya" if s.get("dzuhur") else "Tidak",
                "Ya" if s.get("ashar") else "Tidak",
                "Ya" if s.get("maghrib") else "Tidak",
                "Ya" if s.get("isya") else "Tidak",
                "Selesai" if r.get("finalized") else "Belum",
                r.get("alasan", ""),
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
