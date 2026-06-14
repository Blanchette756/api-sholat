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
    logger.warning("JWT_SECRET tidak di-set. Login administrator akan gagal.")

ADMIN_COOKIE = 'reve_admin'


def set_admin_cookie(response, token):
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
        end_mins = nh * 60 + nm + tolerance_minutes
    else:
        end_mins = 23 * 60 + 59
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
        logger.warning("FIREBASE_SERVICE_ACCOUNT tidak di-set, seluruh permintaan akan ditolak")
except Exception as e:
    logger.error("Gagal inisialisasi Firebase: %s", e)

app = Flask(__name__)

cors_origins = os.environ.get('CORS_ORIGINS', '')
if cors_origins:
    CORS(app, origins=cors_origins.split(','))
else:
    logger.warning("CORS_ORIGINS tidak di-set, permintaan cross-origin akan diblokir")

if _has_limiter:
    limiter = Limiter(get_remote_address, app=app, default_limits=["60 per minute"])
else:
    limiter = None
    logger.warning("Flask-Limiter tidak tersedia, pembatasan permintaan dinonaktifkan")

db_manager = Database()

# Ubah bagian ini di server.py
ALLOWED_STATIC = {'index.html', 'login.html', 'style.css', 'admin-login.html', 'Reve.png', 'firebase-messaging-sw.js'}


@app.after_request
def set_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy'] = 'geolocation=(), microphone=(), camera=()'
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://www.gstatic.com https://apis.google.com; "
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
        logger.warning("Verifikasi Token gagal: %s", e)
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

@app.route('/sholat/check', methods=['POST'])
@_rate_limit("40 per minute")
def check_prayer():
    try:
        user = verify_token()
        if not user:
            return jsonify({"status": "error", "message": "Silakan masuk (login) terlebih dahulu"}), 401

        data = request.get_json(silent=True)
        if not data or not isinstance(data, dict):
            return jsonify({"status": "error", "message": "Data yang dikirimkan tidak valid"}), 400

        prayer = data.get('prayer', '')
        checked = bool(data.get('checked', False))
        jamaah = data.get('jamaah', None)
        npm = data.get('npm', '')
        program_studi = data.get('program_studi', '')
        input_nama = data.get('nama', '').strip() # Menangkap nama dari ketikan manual

        if prayer not in PRAYER_TIMES_APPROX:
            return jsonify({"status": "error", "message": "Pemilihan sholat tidak valid"}), 400

        uid = user.get('uid', '')
        email = user.get('email', '')
        
        # Jika ada input nama manual, gunakan itu. Jika kosong, baru ambil dari Google
        nama = input_nama if input_nama else user.get('name', email)
        tanggal = get_wita_date_str()

        tolerance = db_manager.get_settings().get('tolerance_minutes', 60)
        if checked and not is_prayer_in_window(prayer, tolerance):
            return jsonify({"status": "error", "message": "Waktu pengisian ceklis berada di luar batas yang diizinkan"}), 403

        success, msg = db_manager.check_prayer(uid, email, nama, npm, program_studi, tanggal, prayer, checked, jamaah)
        if success:
            return jsonify({"status": "success"})
        return jsonify({"status": "error", "message": msg}), 400
    except Exception as e:
        logger.exception("Error pada /sholat/check: %s", e)
        return jsonify({"status": "error", "message": "Terjadi kendala pada peladen (server)"}), 500


@app.route('/sholat/haid', methods=['POST'])
@_rate_limit("10 per minute")
def set_haid():
    try:
        user = verify_token()
        if not user:
            return jsonify({"status": "error", "message": "Silakan masuk (login) terlebih dahulu"}), 401

        data = request.get_json(silent=True)
        if not data or not isinstance(data, dict):
            return jsonify({"status": "error", "message": "Data yang dikirimkan tidak valid"}), 400

        is_haid = bool(data.get('haid', False))
        npm = data.get('npm', '')
        program_studi = data.get('program_studi', '')
        input_nama = data.get('nama', '').strip() # Menangkap nama dari ketikan manual
        
        uid = user.get('uid', '')
        email = user.get('email', '')
        
        # Gunakan nama inputan manual atau fallback ke Google
        nama = input_nama if input_nama else user.get('name', email)
        tanggal = get_wita_date_str()

        success, msg = db_manager.set_haid(uid, email, nama, npm, program_studi, tanggal, is_haid)
        if success:
            return jsonify({"status": "success"})
        return jsonify({"status": "error", "message": msg}), 400
    except Exception as e:
        logger.exception("Error pada /sholat/haid: %s", e)
        return jsonify({"status": "error", "message": "Terjadi kendala pada peladen (server)"}), 500

@app.route('/sholat/today', methods=['GET'])
@_rate_limit("30 per minute")
def get_today():
    try:
        user = verify_token()
        if not user:
            return jsonify({"status": "error", "message": "Silakan masuk (login) terlebih dahulu"}), 401

        uid = user.get('uid', '')
        date_param = request.args.get('date', '').strip()
        if date_param:
            import re
            if not re.match(r'^\d{4}-\d{2}-\d{2}$', date_param):
                return jsonify({"status": "error", "message": "Format penulisan tanggal tidak sesuai"}), 400
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
            return jsonify({"status": "error", "message": "Silakan masuk (login) terlebih dahulu"}), 401

        data = request.get_json(silent=True)
        if not data: data = {}
        
        input_nama = data.get('nama', '').strip()
        npm = data.get('npm', '')
        program_studi = data.get('program_studi', '')

        uid = user.get('uid', '')
        email = user.get('email', '')
        tanggal = get_wita_date_str()
        
        nama_final = input_nama if input_nama else user.get('name', email)

        status = db_manager.get_today_status(uid, tanggal)
        haid = status.get('haid', False)
        sholat = status.get('sholat', {})
        jamaah = status.get('jamaah', {})
        
        if not haid:
            total = sum(1 for v in sholat.values() if v)
            if total == 0:
                return jsonify({"status": "error", "message": "Belum ada ibadah sholat yang diceklis"}), 400
            for p, is_done in sholat.items():
                if is_done and not jamaah.get(p):
                    return jsonify({"status": "error", "message": f"Keterangan Berjamaah/Sendiri untuk sholat {p.capitalize()} diwajibkan"}), 400
        else:
            total = 0

        db_manager.finalize_day(uid, tanggal, nama_final, npm, program_studi)
        logger.info("Laporan terselesaikan uid=%s tanggal=%s total=%d", uid, tanggal, total)
        return jsonify({"status": "success", "message": "Laporan status Haid dikirimkan" if haid else f"Laporan ibadah dikirimkan ({total}/5)"})
    except Exception as e:
        logger.exception("Error pada /sholat/finalize: %s", e)
        return jsonify({"status": "error", "message": "Terjadi kendala pada peladen (server)"}), 500

@app.route('/api/reset', methods=['POST'])
@_rate_limit("2 per minute")
def reset_database():
    if not verify_admin():
        return jsonify({"status": "error", "message": "Akses Anda tidak diizinkan"}), 401
    try:
        db_manager.reset_database()
        logger.info("Database dibersihkan seluruhnya oleh Administrator.")
        return jsonify({"status": "success", "message": "Data pada sistem berhasil dibersihkan."})
    except Exception as e:
        logger.exception("Error reset db: %s", e)
        return jsonify({"status": "error", "message": "Gagal membersihkan sistem basis data."}), 500

@app.route('/admin/login', methods=['POST'])
@_rate_limit("5 per minute")
def admin_login():
    if not ADMIN_USER or not ADMIN_PASS:
        return jsonify({"status": "error", "message": "Kredensial Admin belum dikonfigurasi"}), 503
    if not JWT_SECRET:
        return jsonify({"status": "error", "message": "JWT_SECRET belum dipersiapkan"}), 503
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"status": "error", "message": "Data yang dikirimkan tidak valid"}), 400
    username = data.get('username', '')
    password = data.get('password', '')
    if username == ADMIN_USER and password == ADMIN_PASS:
        if not _has_jwt:
            return jsonify({"status": "error", "message": "Layanan JWT tidak tersedia pada sistem"}), 503
        token = pyjwt.encode(
            {"role": "admin", "exp": datetime.now(timezone.utc) + timedelta(hours=12)},
            JWT_SECRET,
            algorithm="HS256",
        )
        logger.info("Login Admin berhasil diotorisasi")
        resp = make_response(jsonify({"status": "success", "token": token}))
        set_admin_cookie(resp, token)
        return resp
    logger.warning("Upaya otorisasi gagal: username=%s", username)
    return jsonify({"status": "error", "message": "Kredensial yang diberikan tidak cocok"}), 401

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
    logger.info("Otorisasi Admin diakhiri")
    return resp

@app.route('/api/statistik', methods=['GET'])
def get_statistik():
    if not verify_admin():
        return jsonify({"status": "error", "message": "Akses Anda tidak diizinkan"}), 401
    try:
        start_date = request.args.get('start', '')
        end_date = request.args.get('end', '')
        data = db_manager.get_statistik_lomba(start_date=start_date, end_date=end_date)
        return jsonify({"status": "success", "data": data})
    except Exception as e:
        logger.exception("Error /api/statistik: %s", e)
        return jsonify({"status": "error", "message": "Gagal merespons data"}), 500

@app.route('/api/settings', methods=['GET'])
def get_settings():
    try:
        settings = db_manager.get_settings()
        return jsonify(settings)
    except Exception as e:
        logger.exception("Error memuat pengaturan: %s", e)
        return jsonify({"tolerance_minutes": 60})

@app.route('/api/settings', methods=['POST'])
def update_settings():
    if not verify_admin():
        return jsonify({"status": "error", "message": "Akses Anda tidak diizinkan"}), 401
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"status": "error", "message": "Data yang dikirimkan tidak valid"}), 400
    minutes = data.get('tolerance_minutes')
    if minutes not in (60, 120):
        return jsonify({"status": "error", "message": "Batas toleransi harus dikonfigurasi antara 60 atau 120 menit"}), 400
    if db_manager.update_tolerance(minutes):
        logger.info("Batas toleransi dimutakhirkan ke %d menit", minutes)
        return jsonify({"status": "success", "message": f"Batas toleransi berhasil dikonfigurasi ke {minutes} menit"})
    return jsonify({"status": "error", "message": "Kegagalan pada saat pemutakhiran data"}), 500

@app.route('/api/students', methods=['GET'])
def get_students():
    if not verify_admin():
        return jsonify({"status": "error", "message": "Akses Anda tidak diizinkan"}), 401
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
        return jsonify({"status": "error", "message": "Gagal merespons data"}), 500

@app.route('/api/scoreboard', methods=['GET'])
@_rate_limit("30 per minute")
def get_scoreboard():
    try:
        user = verify_token()
        if not user:
            return jsonify({"status": "error", "message": "Silakan masuk (login) terlebih dahulu"}), 401
        tanggal = get_wita_date_str()
        data = db_manager.get_today_scoreboard(tanggal)
        return jsonify({"status": "success", "data": data, "tanggal": tanggal})
    except Exception as e:
        logger.exception("Error /api/scoreboard: %s", e)
        return jsonify({"status": "error", "message": "Gagal merespons data"}), 500

@app.route('/sholat/reason', methods=['POST'])
@_rate_limit("5 per minute")
def save_reason():
    try:
        user = verify_token()
        if not user:
            return jsonify({"status": "error", "message": "Silakan masuk (login) terlebih dahulu"}), 401
        data = request.get_json(silent=True)
        if not data:
            return jsonify({"status": "error", "message": "Data yang dikirimkan tidak valid"}), 400
        reason = data.get('reason', '').strip()
        if not reason:
            return jsonify({"status": "error", "message": "Penjelasan tidak diperkenankan kosong"}), 400
        uid = user.get('uid', '')
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
        logger.info("Penjelasan direkam secara logis uid=%s tanggal=%s", uid, tanggal)
        return jsonify({"status": "success", "message": "Pernyataan penjelasan berhasil diarsipkan"})
    except Exception as e:
        logger.exception("Error /sholat/reason: %s", e)
        return jsonify({"status": "error", "message": "Terjadi kendala pada peladen (server)"}), 500

@app.route('/api/weekly', methods=['GET'])
def get_weekly():
    if not verify_admin():
        return jsonify({"status": "error", "message": "Akses Anda tidak diizinkan"}), 401
    try:
        date_from = request.args.get('from', '')
        date_to = request.args.get('to', '')
        data = db_manager.get_weekly_summary(date_from=date_from, date_to=date_to)
        return jsonify({"data": data})
    except Exception as e:
        logger.exception("Error /api/weekly: %s", e)
        return jsonify({"status": "error", "message": "Gagal merespons data"}), 500

@app.route('/api/monthly', methods=['GET'])
def get_monthly():
    if not verify_admin():
        return jsonify({"status": "error", "message": "Akses Anda tidak diizinkan"}), 401
    try:
        date_from = request.args.get('from', '')
        date_to = request.args.get('to', '')
        data = db_manager.get_monthly_summary(date_from=date_from, date_to=date_to)
        return jsonify({"data": data})
    except Exception as e:
        logger.exception("Error /api/monthly: %s", e)
        return jsonify({"status": "error", "message": "Gagal merespons data"}), 500

@app.route('/api/export', methods=['GET'])
def export_excel():
    if not verify_admin():
        return jsonify({"status": "error", "message": "Akses Anda tidak diizinkan"}), 401
    if not _has_openpyxl:
        return jsonify({"status": "error", "message": "Pustaka openpyxl tidak didukung pada sistem"}), 503
    try:
        date_from = request.args.get('from', '')
        date_to = request.args.get('to', '')
        records, _ = db_manager.get_all_records(page=1, limit=10000, date_from=date_from, date_to=date_to)
        wb = Workbook()
        ws = wb.active
        ws.title = "Laporan Sholat"
        ws.append(["Nama", "NPM", "Program Studi", "Email", "Tanggal", "Sedang Haid", "Subuh", "Dzuhur", "Ashar", "Maghrib", "Isya", "Status Laporan", "Keterangan Tambahan"])
        
        for r in records:
            haid = r.get("haid", False)
            s = r.get("sholat", {})
            j = r.get("jamaah", {})
            
            def format_prayer(p):
                if haid: return "Sedang Berhalangan"
                if s.get(p):
                    jam = j.get(p)
                    if jam == 'berjamaah': return "Ditunaikan (Berjamaah)"
                    if jam == 'sendiri': return "Ditunaikan (Individu)"
                    return "Ditunaikan"
                return "Belum Ditunaikan"
                
            ws.append([
                r.get("nama", ""),
                r.get("npm", ""),
                r.get("program_studi", ""),
                r.get("email", ""),
                r.get("tanggal", ""),
                "Ya" if haid else "Tidak",
                format_prayer("subuh"),
                format_prayer("dzuhur"),
                format_prayer("ashar"),
                format_prayer("maghrib"),
                format_prayer("isya"),
                "Terselesaikan" if r.get("finalized") else "Tertunda",
                r.get("alasan", ""),
            ])
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return Response(
            buf.getvalue(),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment; filename=Arsip_Laporan_Sholat_Mahasiswa.xlsx"},
        )
    except Exception as e:
        logger.exception("Error export: %s", e)
        return jsonify({"status": "error", "message": "Kegagalan pada saat menginisiasi proses ekspor data"}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)