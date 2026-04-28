import csv
import glob
import io
import json
import os
import secrets
import smtplib
import urllib.error
import urllib.request
from datetime import date, datetime
from email.message import EmailMessage
from functools import wraps

from flask import Flask, flash, jsonify, redirect, render_template, request, send_file, session, url_for
from flask_sqlalchemy import SQLAlchemy
from openpyxl.cell.cell import MergedCell
from openpyxl import Workbook, load_workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.styles import PatternFill
from sqlalchemy.exc import OperationalError, ProgrammingError
from werkzeug.security import check_password_hash, generate_password_hash
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired


def _load_local_env_file():
    env_path = os.path.join(os.path.dirname(__file__), ".env.local")
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except Exception:
        # .env okunamasa bile uygulama env vars ile devam eder
        pass


_load_local_env_file()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-change-me")
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///mesai_web.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["RESET_TOKEN_EXPIRE_MIN"] = int(os.environ.get("RESET_TOKEN_EXPIRE_MIN", "30"))
app.config["APK_URL"] = os.environ.get("APK_URL", "/download-apk")
app.config["UPDATE_MANIFEST_URL"] = os.environ.get(
    "UPDATE_MANIFEST_URL",
    "https://github.com/mehmettuzner42-code/mesai-app/releases/latest/download/update.json",
)
app.config["SMTP_HOST"] = os.environ.get("SMTP_HOST", "")
app.config["SMTP_PORT"] = int(os.environ.get("SMTP_PORT", "587"))
app.config["SMTP_USERNAME"] = os.environ.get("SMTP_USERNAME", "")
app.config["SMTP_PASSWORD"] = os.environ.get("SMTP_PASSWORD", "")
app.config["SMTP_FROM"] = os.environ.get("SMTP_FROM", "")
app.config["SMTP_USE_TLS"] = os.environ.get("SMTP_USE_TLS", "true").lower() == "true"
app.config["SITE_BASE_URL"] = os.environ.get("SITE_BASE_URL", "http://127.0.0.1:5000")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("COOKIE_SECURE", "false").lower() == "true"
app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("MAX_UPLOAD_MB", "5")) * 1024 * 1024

db = SQLAlchemy(app)
token_serializer = URLSafeTimedSerializer(app.config["SECRET_KEY"])
_RATE_LIMIT_STATE = {}
FOUNDER_EMAIL = "mehmettuzner42@gmail.com"


@app.get("/healthz")
def healthz():
    # Keep-alive pingi icin hafif endpoint.
    return jsonify({"ok": True, "service": "mesai-web-portal"}), 200


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class UserProfile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, unique=True, index=True)
    daire_baskanligi = db.Column(db.String(255), default="", nullable=False)
    sube_mudurlugu = db.Column(db.String(255), default="", nullable=False)
    ad_soyad = db.Column(db.String(255), default="", nullable=False)
    sicil_no = db.Column(db.String(100), default="", nullable=False)
    ekip_kodu = db.Column(db.String(100), default="", nullable=False)


class OvertimeEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    work_date = db.Column(db.Date, nullable=False, index=True)
    start_time = db.Column(db.String(5), nullable=False)
    end_time = db.Column(db.String(5), nullable=False)
    pct60 = db.Column(db.Float, default=0.0, nullable=False)
    pct15 = db.Column(db.Float, default=0.0, nullable=False)
    pazar = db.Column(db.Float, default=0.0, nullable=False)
    bayram = db.Column(db.Float, default=0.0, nullable=False)
    description = db.Column(db.String(500), default="", nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class AppSetting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    setting_key = db.Column(db.String(120), unique=True, nullable=False, index=True)
    setting_value = db.Column(db.Text, default="", nullable=False)


class DelegatedAdminPermission(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    owner_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    delegate_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, unique=True, index=True)
    allowed_user_ids_json = db.Column(db.Text, default="[]", nullable=False)
    # Legacy kolon (eski surumlerle uyumluluk icin tutuluyor)
    can_view_passwords = db.Column(db.Boolean, default=False, nullable=False)
    can_reset_password = db.Column(db.Boolean, default=False, nullable=False)
    can_view_users_screen = db.Column(db.Boolean, default=False, nullable=False)
    can_view_charts = db.Column(db.Boolean, default=False, nullable=False)
    can_view_filters = db.Column(db.Boolean, default=False, nullable=False)
    can_add_user = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


def fmt_num(value: float) -> str:
    if value is None:
        return ""
    if abs(value) < 1e-9:
        return ""
    if float(value).is_integer():
        return str(int(value))
    out = f"{value:.2f}".rstrip("0").rstrip(".")
    return out.replace(".", ",")


def parse_float(value: str) -> float:
    t = (value or "").strip()
    if not t:
        return 0.0
    return float(t.replace(",", "."))


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def format_dmy(d: date) -> str:
    return d.strftime("%d.%m.%Y")


def weekday_tr(d: date) -> str:
    names = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]
    return names[d.weekday()]


def hhmm_to_minutes(hhmm: str):
    try:
        h, m = hhmm.split(":")
        h, m = int(h), int(m)
        if h < 0 or h > 23 or m < 0 or m > 59:
            return None
        return h * 60 + m
    except Exception:
        return None


def calc_total_hours(start_hhmm: str, end_hhmm: str):
    s = hhmm_to_minutes(start_hhmm)
    e = hhmm_to_minutes(end_hhmm)
    if s is None or e is None:
        return None
    if e <= s:
        e += 1440
    return (e - s) / 60.0


def overlap(a0: int, a1: int, b0: int, b1: int) -> int:
    return max(0, min(a1, b1) - max(a0, b0))


def calc_night_20_06(start_hhmm: str, end_hhmm: str):
    s = hhmm_to_minutes(start_hhmm)
    e = hhmm_to_minutes(end_hhmm)
    if s is None or e is None:
        return None
    if e <= s:
        e += 1440
    total = 0
    max_day = (e // 1440) + 1
    for k in range(max_day + 2):
        d0 = k * 1440
        total += overlap(s, e, d0, d0 + 6 * 60)
        total += overlap(s, e, d0 + 20 * 60, d0 + 24 * 60)
    return total / 60.0


def calc_lunch_12_13(start_hhmm: str, end_hhmm: str):
    s = hhmm_to_minutes(start_hhmm)
    e = hhmm_to_minutes(end_hhmm)
    if s is None or e is None:
        return None
    if e <= s:
        e += 1440
    total = 0
    max_day = (e // 1440) + 1
    for k in range(max_day + 2):
        d0 = k * 1440
        total += overlap(s, e, d0 + 12 * 60, d0 + 13 * 60)
    return total / 60.0


def tr_upper(text: str) -> str:
    # Turkce buyuk harf donusumu: i->I degil, i->I ve ı->I kurallarini dogru uygular.
    if text is None:
        return ""
    trans = str.maketrans({"i": "İ", "ı": "I"})
    return str(text).translate(trans).upper()


def period_start_for_date(d: date) -> date:
    if d.day >= 24:
        return date(d.year, d.month, 24)
    if d.month == 1:
        return date(d.year - 1, 12, 24)
    return date(d.year, d.month - 1, 24)


def add_month(year: int, month: int):
    if month == 12:
        return year + 1, 1
    return year, month + 1


def period_for_start(year: int, month: int):
    start = date(year, month, 24)
    ey, em = add_month(year, month)
    end = date(ey, em, 23)
    return start, end


def period_year(start_year: int, start_month: int) -> int:
    return start_year + 1 if start_month == 12 else start_year


def fixed_holiday_set(year: int):
    return {
        date(year, 1, 1),
        date(year, 4, 23),
        date(year, 5, 1),
        date(year, 5, 19),
        date(year, 7, 15),
        date(year, 8, 30),
        date(year, 10, 29),
    }


def day_defaults(target_date: date, end_time_override: str = None):
    wd = target_date.weekday()  # 0 pazartesi ... 6 pazar
    holidays = fixed_holiday_set(target_date.year)
    is_holiday = target_date in holidays

    if is_holiday:
        start, end = "08:00", "17:00"
        pazar, bayram = 0.0, 1.0
    elif wd == 6:  # pazar
        start, end = "08:00", "17:00"
        pazar, bayram = 1.0, 0.0
    elif wd == 5:  # cumartesi
        start, end = "08:00", "18:00"
        pazar, bayram = 0.0, 0.0
    else:  # akşam mesaisi
        start, end = "18:00", "21:00"
        pazar, bayram = 0.0, 0.0

    if end_time_override:
        end = end_time_override
    total = calc_total_hours(start, end) or 0.0
    night = calc_night_20_06(start, end) or 0.0
    lunch = calc_lunch_12_13(start, end) or 0.0
    net = max(0.0, total - lunch)
    if is_holiday or wd == 6:
        # Pazar/Bayramda 8 saatten az (ogle arasi dusulmus) calisma %60'a yazilir.
        # 8 saat ve ustunde 1 gun pazar/bayram + 8 saat uzeri %60 olur.
        if net < 7.0:
            pct60 = net
            pazar = 0.0
            bayram = 0.0
        else:
            pct60 = max(0.0, net - 8.0)
            if is_holiday:
                pazar, bayram = 0.0, 1.0
            else:
                pazar, bayram = 1.0, 0.0
    elif wd == 5:
        pct60 = max(0.0, total - 1.0)
    else:
        pct60 = total

    return {
        "start": start,
        "end": end,
        "pct60": pct60,
        "pct15": night,
        "pazar": pazar,
        "bayram": bayram,
        "isHoliday": is_holiday,
        "weekday": wd,
    }


def send_reset_email(to_email: str, reset_url: str) -> bool:
    host = app.config["SMTP_HOST"]
    username = app.config["SMTP_USERNAME"]
    password = app.config["SMTP_PASSWORD"]
    sender = app.config["SMTP_FROM"] or username
    if not host or not sender:
        return False
    msg = EmailMessage()
    msg["Subject"] = "Mesai Portal - Sifre Sifirlama"
    msg["From"] = sender
    msg["To"] = to_email
    msg.set_content(
        "Sifre sifirlama baglantiniz:\n\n"
        f"{reset_url}\n\n"
        f"Baglanti {app.config['RESET_TOKEN_EXPIRE_MIN']} dakika gecerlidir."
    )
    port = app.config["SMTP_PORT"]
    use_tls = app.config["SMTP_USE_TLS"]
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=20) as server:
            if username:
                server.login(username, password)
            server.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=20) as server:
            if use_tls:
                server.starttls()
            if username:
                server.login(username, password)
            server.send_message(msg)
    return True


def is_rate_limited(key: str, limit: int, window_sec: int) -> bool:
    now = datetime.utcnow().timestamp()
    rec = _RATE_LIMIT_STATE.get(key, [])
    rec = [t for t in rec if now - t < window_sec]
    if len(rec) >= limit:
        _RATE_LIMIT_STATE[key] = rec
        return True
    rec.append(now)
    _RATE_LIMIT_STATE[key] = rec
    return False


def login_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return view_func(*args, **kwargs)

    return wrapped


def is_founder_user(user: User) -> bool:
    return bool(user and (user.email or "").strip().lower() == FOUNDER_EMAIL)


def founder_user_id() -> int:
    u = User.query.filter(db.func.lower(db.func.trim(User.email)) == FOUNDER_EMAIL.lower()).first()
    return int(u.id) if u else 0


def get_delegate_permission(user_id: int):
    fid = founder_user_id()
    if not fid:
        return None
    try:
        return DelegatedAdminPermission.query.filter_by(owner_user_id=fid, delegate_user_id=user_id).first()
    except (OperationalError, ProgrammingError):
        db.session.rollback()
        # Canlıda kolonlar henüz oluşmadıysa otomatik tamamla ve tekrar dene.
        ensure_delegated_permission_columns()
        return DelegatedAdminPermission.query.filter_by(owner_user_id=fid, delegate_user_id=user_id).first()


def allowed_user_ids_for(user: User):
    if not user:
        return set()
    if is_founder_user(user):
        return None  # None => all users
    perm = get_delegate_permission(user.id)
    if not perm:
        return set()
    try:
        raw = json.loads(perm.allowed_user_ids_json or "[]")
        return {int(x) for x in raw if str(x).isdigit()}
    except Exception:
        return set()


def can_access_admin_area(user: User) -> bool:
    return bool(is_founder_user(user) or get_delegate_permission(user.id if user else 0))


def delegate_can(user: User, capability: str) -> bool:
    if not user:
        return False
    if is_founder_user(user):
        return True
    perm = get_delegate_permission(user.id)
    if not perm:
        return False
    if capability == "users":
        return bool(perm.can_view_users_screen)
    if capability == "charts":
        return bool(perm.can_view_charts)
    if capability == "filters":
        return bool(perm.can_view_filters)
    if capability == "add_user":
        return bool(perm.can_add_user)
    if capability == "reset_password":
        return bool(perm.can_reset_password)
    if capability == "impersonate":
        return bool(perm.can_view_users_screen)
    return False


def session_login_user():
    uid = session.get("user_id")
    if not uid:
        return None
    return User.query.get(uid)


def current_user():
    login_user = session_login_user()
    if login_user is None:
        return None
    # Kurucu kullanici "kullaniciya burunme" modunda ise ekrandaki tum veriler secilen kisiye gore akar.
    if can_access_admin_area(login_user):
        imp_uid = session.get("admin_impersonate_user_id")
        if imp_uid:
            imp_user = User.query.get(imp_uid)
            if imp_user:
                allowed = allowed_user_ids_for(login_user)
                if allowed is None or imp_user.id in allowed:
                    return imp_user
                session.pop("admin_impersonate_user_id", None)
    return login_user


def ensure_user_or_redirect():
    user = current_user()
    if user is None:
        session.clear()
        return None
    return user


def admin_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        login_user = session_login_user()
        if not login_user:
            return redirect(url_for("login"))
        if not is_founder_user(login_user):
            flash("Bu alan sadece kurucu kullanıcıya açıktır.", "error")
            return redirect(url_for("dashboard"))
        return view_func(*args, **kwargs)

    return wrapped


def admin_or_delegate_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        login_user = session_login_user()
        if not login_user:
            return redirect(url_for("login"))
        if not can_access_admin_area(login_user):
            flash("Bu alan sadece yetkili kullanıcılara açıktır.", "error")
            return redirect(url_for("dashboard"))
        return view_func(*args, **kwargs)

    return wrapped


def get_or_create_profile(user_id: int):
    p = UserProfile.query.filter_by(user_id=user_id).first()
    if p:
        return p
    p = UserProfile(user_id=user_id)
    db.session.add(p)
    db.session.commit()
    return p


def get_setting_value(key: str, default_value: str = "") -> str:
    row = AppSetting.query.filter_by(setting_key=key).first()
    return row.setting_value if row and row.setting_value is not None else default_value


def set_setting_value(key: str, value: str):
    row = AppSetting.query.filter_by(setting_key=key).first()
    if row:
        row.setting_value = value
    else:
        row = AppSetting(setting_key=key, setting_value=value)
        db.session.add(row)


def entry_to_dict(entry: OvertimeEntry):
    return {
        "id": entry.id,
        "workDate": entry.work_date.isoformat(),
        "startTime": entry.start_time,
        "endTime": entry.end_time,
        "pct60": entry.pct60,
        "pct15": entry.pct15,
        "pazar": entry.pazar,
        "bayram": entry.bayram,
        "description": entry.description,
        "updatedAt": entry.updated_at.isoformat(),
    }


def grouped_period_rows(entries):
    day_map = {}
    for e in entries:
        key = e.work_date.isoformat()
        if key not in day_map:
            day_map[key] = {
                "work_date": e.work_date,
                "start_time": e.start_time,
                "end_time": e.end_time,
                "pct60": e.pct60,
                "pct15": e.pct15,
                "pazar": e.pazar,
                "bayram": e.bayram,
                "description": e.description.strip(),
                "entry_id": e.id,
            }
        else:
            r = day_map[key]
            r["start_time"] = min(r["start_time"], e.start_time)
            r["end_time"] = max(r["end_time"], e.end_time)
            r["pct60"] += e.pct60
            r["pct15"] += e.pct15
            r["pazar"] += e.pazar
            r["bayram"] += e.bayram
            if e.description.strip():
                r["description"] = " | ".join([x for x in [r["description"], e.description.strip()] if x])
    return [day_map[k] for k in sorted(day_map.keys())]


def build_recent_ui_items(entries):
    def sort_key(e: OvertimeEntry):
        ps = period_start_for_date(e.work_date)
        period_value = ps.year * 100 + ps.month
        return (period_value, e.work_date, e.start_time, e.id)

    sorted_entries = sorted(entries, key=sort_key, reverse=True)
    out = []
    prev_period = None
    for e in sorted_entries:
        ps = period_start_for_date(e.work_date)
        key = (ps.year, ps.month)
        if key != prev_period:
            p_start, p_end = period_for_start(ps.year, ps.month)
            out.append(
                {
                    "kind": "header",
                    "label": f"{format_dmy(p_start)} - {format_dmy(p_end)}",
                }
            )
            prev_period = key
        out.append({"kind": "entry", "entry": e})
    return out


@app.context_processor
def inject_helpers():
    login_user = session_login_user()
    is_founder = is_founder_user(login_user)
    is_delegate_admin = bool(login_user and get_delegate_permission(login_user.id))
    is_impersonating = bool(session.get("admin_impersonate_user_id"))
    return {
        "fmt_num": fmt_num,
        "apk_url": app.config.get("APK_URL", "/download-apk"),
        "is_founder": is_founder,
        "is_delegate_admin": is_delegate_admin,
        "can_view_users_screen": delegate_can(login_user, "users"),
        "can_view_charts": delegate_can(login_user, "charts"),
        "can_view_filters": delegate_can(login_user, "filters"),
        "is_impersonating": is_impersonating,
    }


@app.after_request
def apply_security_headers(resp):
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    resp.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'"
    # Dinamik sayfalar cache'lenmesin: farkli kullaniciya geciste eski profil/veri gorunmesini engeller.
    if not request.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    return resp


@app.get("/")
def root():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    flash("Web kayıt olma ekranı kapatıldı.", "error")
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown")
        if is_rate_limited(f"login:{ip}", limit=15, window_sec=60):
            flash("Çok fazla deneme. Lütfen 1 dakika sonra tekrar deneyin.", "error")
            return render_template("login.html")
        identity = request.form.get("email", request.form.get("username_or_email", "")).strip()
        password = request.form.get("password", "")
        user = User.query.filter((User.username == identity) | (User.email == identity)).first()
        if not user or not check_password_hash(user.password_hash, password):
            flash("E-posta veya şifre hatalı.", "error")
            return render_template("login.html")
        session.clear()
        session["user_id"] = user.id
        session["api_token"] = token_serializer.dumps({"uid": user.id, "nonce": secrets.token_hex(8)})
        return redirect(url_for("dashboard"))
    return render_template("login.html")


@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


def build_period_options_for_entries(entries):
    start_options = sorted({(period_start_for_date(e.work_date).year, period_start_for_date(e.work_date).month) for e in entries}, reverse=True)
    if not start_options:
        ps = period_start_for_date(date.today())
        start_options = [(ps.year, ps.month)]
    years = sorted({period_year(y, m) for (y, m) in start_options}, reverse=True)
    selected_year = years[0]
    period_options = [(y, m) for (y, m) in start_options if period_year(y, m) == selected_year] or [start_options[0]]
    return years, selected_year, period_options, start_options[0]


@app.get("/admin/users")
@login_required
@admin_or_delegate_required
def admin_users():
    login_user = session_login_user()
    can_users_screen = delegate_can(login_user, "users")
    can_charts_screen = delegate_can(login_user, "charts")
    can_filters = delegate_can(login_user, "filters")
    can_add_user = delegate_can(login_user, "add_user")
    allowed_ids = allowed_user_ids_for(login_user)
    delegate_perm = get_delegate_permission(login_user.id) if login_user else None
    can_impersonate = delegate_can(login_user, "impersonate")
    # Tum kullanicilari profil ile birlikte listele
    users_query = User.query.order_by(User.created_at.desc())
    users = users_query.all() if allowed_ids is None else users_query.filter(User.id.in_(list(allowed_ids) or [0])).all()
    profiles = {p.user_id: p for p in UserProfile.query.all()}
    entry_counts = {
        uid: cnt
        for uid, cnt in db.session.query(OvertimeEntry.user_id, db.func.count(OvertimeEntry.id)).group_by(OvertimeEntry.user_id).all()
    }
    rows = []
    for u in users:
        p = profiles.get(u.id) or UserProfile(user_id=u.id)
        rows.append(
            {
                "user": u,
                "profile": p,
                "entry_count": int(entry_counts.get(u.id, 0)),
                "can_manage_permissions": bool(is_founder_user(login_user)),
                "can_reset_password": bool(is_founder_user(login_user) or (delegate_perm.can_reset_password if delegate_perm else False)),
                "can_open_user": bool(can_impersonate and (allowed_ids is None or u.id in allowed_ids)),
            }
        )

    founder_entries = OvertimeEntry.query.filter_by(user_id=login_user.id).order_by(OvertimeEntry.work_date.desc(), OvertimeEntry.id.desc()).all()
    years, selected_year, period_options, active_start = build_period_options_for_entries(founder_entries)
    sig_prefix = f"bulk_excel_sign_{login_user.id}"
    default_title = "" if not is_founder_user(login_user) else "Ambarlar Şefi"
    default_manager_title = "" if not is_founder_user(login_user) else "Ambarlar Şube Müdürü"
    default_director_title = "" if not is_founder_user(login_user) else "Daire Başkanı"
    sign_fields = {
        "chef_title": get_setting_value(f"{sig_prefix}_chef_title", default_title),
        "chef_name": get_setting_value(f"{sig_prefix}_chef_name", ""),
        "manager_title": get_setting_value(f"{sig_prefix}_manager_title", default_manager_title),
        "manager_name": get_setting_value(f"{sig_prefix}_manager_name", ""),
        "director_title": get_setting_value(f"{sig_prefix}_director_title", default_director_title),
        "director_name": get_setting_value(f"{sig_prefix}_director_name", ""),
    }
    return render_template(
        "admin_users.html",
        rows=rows,
        can_users_screen=can_users_screen,
        can_charts_screen=can_charts_screen,
        can_filters=can_filters,
        can_add_user=can_add_user,
        years=years,
        selected_year=selected_year,
        period_options=period_options,
        period_value=f"{active_start[0]:04d}-{active_start[1]:02d}",
        sign_fields=sign_fields,
    )


@app.get("/admin/users/charts")
@login_required
@admin_or_delegate_required
def admin_users_charts():
    login_user = session_login_user()
    if not delegate_can(login_user, "charts"):
        flash("Grafik ekranını görme yetkiniz yok.", "error")
        return redirect(url_for("admin_users"))
    allowed_ids = allowed_user_ids_for(login_user)
    entries_query = OvertimeEntry.query.order_by(OvertimeEntry.work_date.desc(), OvertimeEntry.id.desc())
    all_entries = entries_query.all() if allowed_ids is None else entries_query.filter(OvertimeEntry.user_id.in_(list(allowed_ids) or [0])).all()
    years, default_year, period_options, default_start = build_period_options_for_entries(all_entries)
    selected_year = request.args.get("year", type=int) or default_year
    selected_period = request.args.get("period", "").strip()
    selected_user_ids = {int(v) for v in request.args.getlist("selected_user_ids") if str(v).isdigit()}
    selected_daire = request.args.get("daire", "").strip()
    selected_sube = request.args.get("sube", "").strip()
    active_start = default_start
    if selected_period and "-" in selected_period:
        try:
            sy, sm = (int(x) for x in selected_period.split("-"))
            if (sy, sm) in period_options:
                active_start = (sy, sm)
        except Exception:
            pass
    p_start, p_end = period_for_start(active_start[0], active_start[1])

    users_query = User.query.order_by(User.email.asc())
    users = users_query.all() if allowed_ids is None else users_query.filter(User.id.in_(list(allowed_ids) or [0])).all()
    if selected_user_ids:
        users = [u for u in users if u.id in selected_user_ids]
    profiles = {p.user_id: p for p in UserProfile.query.all()}

    period_agg_rows = (
        db.session.query(
            OvertimeEntry.user_id,
            db.func.sum(OvertimeEntry.pct60),
            db.func.sum(OvertimeEntry.pct15),
            db.func.sum(OvertimeEntry.pazar),
            db.func.sum(OvertimeEntry.bayram),
        )
        .filter(
            OvertimeEntry.work_date >= p_start,
            OvertimeEntry.work_date <= p_end,
        )
        .group_by(OvertimeEntry.user_id)
        .all()
    )
    # Yil grafigi, rapor sayfasindaki "donem yili" kuraliyla ayni olmali:
    # Aralikta baslayan donem bir sonraki yila yazilir.
    all_year_entries = OvertimeEntry.query.all()
    year_agg = {}
    for e in all_year_entries:
        ps = period_start_for_date(e.work_date)
        py = period_year(ps.year, ps.month)
        if py != selected_year:
            continue
        d = year_agg.setdefault(
            int(e.user_id),
            {"pct60": 0.0, "pct15": 0.0, "pazar": 0.0, "bayram": 0.0},
        )
        d["pct60"] += float(e.pct60 or 0)
        d["pct15"] += float(e.pct15 or 0)
        d["pazar"] += float(e.pazar or 0)
        d["bayram"] += float(e.bayram or 0)

    period_agg = {
        int(uid): {
            "pct60": float(s60 or 0),
            "pct15": float(s15 or 0),
            "pazar": float(sp or 0),
            "bayram": float(sb or 0),
        }
        for uid, s60, s15, sp, sb in period_agg_rows
    }
    rows = []
    for u in users:
        p = profiles.get(u.id) or UserProfile(user_id=u.id)
        pa = period_agg.get(u.id, {"pct60": 0.0, "pct15": 0.0, "pazar": 0.0, "bayram": 0.0})
        ya = year_agg.get(u.id, {"pct60": 0.0, "pct15": 0.0, "pazar": 0.0, "bayram": 0.0})
        rows.append(
            {
                "email": u.email,
                "name": p.ad_soyad or "-",
                "period": pa,
                "year": ya,
                # Grafik metriği: sadece %60 mesai
                "period_hours": pa["pct60"],
                "year_hours": ya["pct60"],
            }
        )

    rows_period = sorted(rows, key=lambda x: x["period_hours"], reverse=True)
    rows_year = sorted(rows, key=lambda x: x["year_hours"], reverse=True)
    rows_year_pazar = sorted(rows, key=lambda x: float(x["year"].get("pazar", 0) or 0), reverse=True)
    rows_year_bayram = sorted(rows, key=lambda x: float(x["year"].get("bayram", 0) or 0), reverse=True)
    max_period = max([r["period_hours"] for r in rows_period] + [1.0])
    max_year = max([r["year_hours"] for r in rows_year] + [1.0])
    max_year_pazar = max([float(r["year"].get("pazar", 0) or 0) for r in rows_year_pazar] + [1.0])
    max_year_bayram = max([float(r["year"].get("bayram", 0) or 0) for r in rows_year_bayram] + [1.0])
    year_total_pazar = sum(float(r["year"].get("pazar", 0) or 0) for r in rows_year)
    year_total_bayram = sum(float(r["year"].get("bayram", 0) or 0) for r in rows_year)

    return render_template(
        "admin_users_charts.html",
        all_users=users_query.all() if allowed_ids is None else users_query.filter(User.id.in_(list(allowed_ids) or [0])).all(),
        profiles=profiles,
        selected_user_ids=selected_user_ids,
        selected_daire=selected_daire,
        selected_sube=selected_sube,
        can_view_filters=delegate_can(login_user, "filters"),
        rows_period=rows_period,
        rows_year=rows_year,
        rows_year_pazar=rows_year_pazar,
        rows_year_bayram=rows_year_bayram,
        max_period=max_period,
        max_year=max_year,
        max_year_pazar=max_year_pazar,
        max_year_bayram=max_year_bayram,
        years=years,
        selected_year=selected_year,
        period_options=period_options,
        period_value=f"{active_start[0]:04d}-{active_start[1]:02d}",
        period_start=p_start,
        period_end=p_end,
        format_dmy=format_dmy,
        year_total_pazar=year_total_pazar,
        year_total_bayram=year_total_bayram,
    )


@app.post("/admin/users/charts/export.xlsx")
@login_required
@admin_or_delegate_required
def admin_users_charts_export_xlsx():
    login_user = session_login_user()
    if not delegate_can(login_user, "charts"):
        flash("Grafik ekranını görme yetkiniz yok.", "error")
        return redirect(url_for("admin_users"))
    allowed_ids = allowed_user_ids_for(login_user)
    selected_user_ids = {int(v) for v in request.form.getlist("selected_user_ids") if str(v).isdigit()}

    entries_query = OvertimeEntry.query.order_by(OvertimeEntry.work_date.desc(), OvertimeEntry.id.desc())
    all_entries = entries_query.all() if allowed_ids is None else entries_query.filter(OvertimeEntry.user_id.in_(list(allowed_ids) or [0])).all()
    years, default_year, period_options, default_start = build_period_options_for_entries(all_entries)
    selected_year = request.form.get("year", type=int) or default_year
    selected_period = request.form.get("period", "").strip()
    active_start = default_start
    if selected_period and "-" in selected_period:
        try:
            sy, sm = (int(x) for x in selected_period.split("-"))
            if (sy, sm) in period_options:
                active_start = (sy, sm)
        except Exception:
            pass
    p_start, p_end = period_for_start(active_start[0], active_start[1])

    users_query = User.query.order_by(User.email.asc())
    users = users_query.all() if allowed_ids is None else users_query.filter(User.id.in_(list(allowed_ids) or [0])).all()
    if selected_user_ids:
        users = [u for u in users if u.id in selected_user_ids]
    profiles = {p.user_id: p for p in UserProfile.query.all()}

    period_agg_rows = (
        db.session.query(
            OvertimeEntry.user_id,
            db.func.sum(OvertimeEntry.pct60),
            db.func.sum(OvertimeEntry.pct15),
            db.func.sum(OvertimeEntry.pazar),
            db.func.sum(OvertimeEntry.bayram),
        )
        .filter(
            OvertimeEntry.work_date >= p_start,
            OvertimeEntry.work_date <= p_end,
        )
        .group_by(OvertimeEntry.user_id)
        .all()
    )
    all_year_entries = OvertimeEntry.query.all()
    year_agg = {}
    for e in all_year_entries:
        ps = period_start_for_date(e.work_date)
        py = period_year(ps.year, ps.month)
        if py != selected_year:
            continue
        d = year_agg.setdefault(int(e.user_id), {"pct60": 0.0, "pct15": 0.0, "pazar": 0.0, "bayram": 0.0})
        d["pct60"] += float(e.pct60 or 0)
        d["pct15"] += float(e.pct15 or 0)
        d["pazar"] += float(e.pazar or 0)
        d["bayram"] += float(e.bayram or 0)
    period_agg = {
        int(uid): {"pct60": float(s60 or 0), "pct15": float(s15 or 0), "pazar": float(sp or 0), "bayram": float(sb or 0)}
        for uid, s60, s15, sp, sb in period_agg_rows
    }
    rows = []
    for u in users:
        p = profiles.get(u.id) or UserProfile(user_id=u.id)
        pa = period_agg.get(u.id, {"pct60": 0.0, "pct15": 0.0, "pazar": 0.0, "bayram": 0.0})
        ya = year_agg.get(u.id, {"pct60": 0.0, "pct15": 0.0, "pazar": 0.0, "bayram": 0.0})
        rows.append({"name": p.ad_soyad or "-", "period_hours": pa["pct60"], "year_hours": ya["pct60"], "year": ya})
    rows_period = sorted(rows, key=lambda x: x["period_hours"], reverse=True)
    rows_year = sorted(rows, key=lambda x: x["year_hours"], reverse=True)
    rows_year_pazar = sorted(rows, key=lambda x: float(x["year"].get("pazar", 0) or 0), reverse=True)
    rows_year_bayram = sorted(rows, key=lambda x: float(x["year"].get("bayram", 0) or 0), reverse=True)

    wb = Workbook()
    ws1 = wb.active
    ws1.title = "Donem Grafigi"
    ws2 = wb.create_sheet("Yil Grafigi")
    ws3 = wb.create_sheet("Pazar Grafigi")
    ws4 = wb.create_sheet("Bayram Grafigi")

    def fill_sheet(ws, title, data_rows, value_getter):
        ws["A1"] = title
        ws["A2"] = "Ad Soyad"
        ws["B2"] = "Deger"
        row_num = 3
        for r in data_rows:
            ws.cell(row=row_num, column=1).value = r["name"]
            ws.cell(row=row_num, column=2).value = float(value_getter(r))
            row_num += 1
        if row_num > 3:
            chart = BarChart()
            chart.type = "col"
            chart.style = 10
            chart.y_axis.title = "Deger"
            chart.x_axis.title = "Personel"
            data_ref = Reference(ws, min_col=2, min_row=2, max_row=row_num - 1)
            cats_ref = Reference(ws, min_col=1, min_row=3, max_row=row_num - 1)
            chart.add_data(data_ref, titles_from_data=True)
            chart.set_categories(cats_ref)
            chart.height = 9
            chart.width = 20
            ws.add_chart(chart, "D2")

    fill_sheet(ws1, f"Donem Grafigi ({format_dmy(p_start)} - {format_dmy(p_end)})", rows_period, lambda r: r["period_hours"])
    fill_sheet(ws2, f"Yil Grafigi ({selected_year})", rows_year, lambda r: r["year_hours"])
    fill_sheet(ws3, f"Pazar Grafigi ({selected_year})", rows_year_pazar, lambda r: float(r["year"].get("pazar", 0) or 0))
    fill_sheet(ws4, f"Bayram Grafigi ({selected_year})", rows_year_bayram, lambda r: float(r["year"].get("bayram", 0) or 0))

    mem = io.BytesIO()
    wb.save(mem)
    mem.seek(0)
    return send_file(
        mem,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=f"Grafik_Raporu_{selected_year}_{active_start[0]:04d}-{active_start[1]:02d}.xlsx",
    )


@app.get("/admin/users/<int:target_user_id>/show-password")
@login_required
@admin_or_delegate_required
def admin_show_password(target_user_id: int):
    login_user = session_login_user()
    allowed_ids = allowed_user_ids_for(login_user)
    if allowed_ids is not None and target_user_id not in allowed_ids:
        flash("Bu kullanıcı için yetkiniz yok.", "error")
        return redirect(url_for("admin_users"))
    delegate_perm = get_delegate_permission(login_user.id) if login_user else None
    can_view = bool(is_founder_user(login_user) or (delegate_perm.can_reset_password if delegate_perm else False))
    target = User.query.get(target_user_id)
    if not target:
        flash("Kullanıcı bulunamadı.", "error")
        return redirect(url_for("admin_users"))
    if not can_view:
        flash("Şifre görme yetkiniz yok.", "error")
        return redirect(url_for("admin_users"))
    flash(
        f"{target.email} için mevcut şifre güvenlik nedeniyle görüntülenemez (hashli saklanır). Bunun yerine 'Şifre Sıfırla' kullanın.",
        "error",
    )
    return redirect(url_for("admin_users"))


@app.post("/admin/users/<int:target_user_id>/reset-password")
@login_required
@admin_or_delegate_required
def admin_reset_password(target_user_id: int):
    login_user = session_login_user()
    allowed_ids = allowed_user_ids_for(login_user)
    if allowed_ids is not None and target_user_id not in allowed_ids:
        flash("Bu kullanıcı için yetkiniz yok.", "error")
        return redirect(url_for("admin_users"))
    delegate_perm = get_delegate_permission(login_user.id) if login_user else None
    can_reset = bool(is_founder_user(login_user) or (delegate_perm.can_reset_password if delegate_perm else False))
    if not can_reset:
        flash("Şifre sıfırlama yetkiniz yok.", "error")
        return redirect(url_for("admin_users"))

    target = User.query.get(target_user_id)
    if not target:
        flash("Kullanıcı bulunamadı.", "error")
        return redirect(url_for("admin_users"))

    # Kolay okunur geçici şifre üretimi (kullanıcı ilk girişte değiştirmeli).
    temp_password = secrets.token_urlsafe(9)[:12]
    target.password_hash = generate_password_hash(temp_password)
    db.session.commit()
    flash(
        f"{target.email} için geçici şifre oluşturuldu: {temp_password} (kullanıcı giriş yapınca şifresini değiştirmeli).",
        "success",
    )
    return redirect(url_for("admin_users"))


@app.route("/admin/permissions/<int:target_user_id>", methods=["GET", "POST"])
@login_required
@admin_required
def admin_edit_permission(target_user_id: int):
    founder = session_login_user()
    if not founder:
        return redirect(url_for("login"))
    target = User.query.get(target_user_id)
    if not target:
        flash("Kullanıcı bulunamadı.", "error")
        return redirect(url_for("admin_users"))
    if is_founder_user(target):
        flash("Kurucu kullanıcı için bu işlem yapılamaz.", "error")
        return redirect(url_for("admin_users"))

    perm = DelegatedAdminPermission.query.filter_by(owner_user_id=founder.id, delegate_user_id=target.id).first()
    if request.method == "POST":
        allowed_ids = [int(v) for v in request.form.getlist("allowed_user_ids") if str(v).isdigit()]
        can_reset_password = request.form.get("can_reset_password") == "1"
        can_view_users_screen = request.form.get("can_view_users_screen") == "1"
        can_view_charts = request.form.get("can_view_charts") == "1"
        can_view_filters = request.form.get("can_view_filters") == "1"
        can_add_user = request.form.get("can_add_user") == "1"
        if perm is None:
            perm = DelegatedAdminPermission(owner_user_id=founder.id, delegate_user_id=target.id)
            db.session.add(perm)
        perm.allowed_user_ids_json = json.dumps(sorted(set(allowed_ids)))
        perm.can_view_passwords = can_reset_password
        perm.can_reset_password = can_reset_password
        perm.can_view_users_screen = can_view_users_screen
        perm.can_view_charts = can_view_charts
        perm.can_view_filters = can_view_filters
        perm.can_add_user = can_add_user
        db.session.commit()
        flash("Yetkiler kaydedildi.", "success")
        return redirect(url_for("admin_authorized_users"))

    current_allowed = set()
    if perm:
        try:
            current_allowed = {int(x) for x in json.loads(perm.allowed_user_ids_json or "[]") if str(x).isdigit()}
        except Exception:
            current_allowed = set()
    users = User.query.order_by(User.email.asc()).all()
    return render_template(
        "admin_permission_edit.html",
        target=target,
        users=users,
        profiles={p.user_id: p for p in UserProfile.query.all()},
        current_allowed=current_allowed,
        can_reset_password=bool(perm.can_reset_password) if perm else False,
        can_view_users_screen=bool(perm.can_view_users_screen) if perm else False,
        can_view_charts=bool(perm.can_view_charts) if perm else False,
        can_view_filters=bool(perm.can_view_filters) if perm else False,
        can_add_user=bool(perm.can_add_user) if perm else False,
    )


@app.route("/admin/users/new", methods=["GET", "POST"])
@login_required
@admin_or_delegate_required
def admin_add_user():
    login_user = session_login_user()
    if not login_user:
        return redirect(url_for("login"))
    if not delegate_can(login_user, "add_user"):
        flash("Kişi ekleme yetkiniz yok.", "error")
        return redirect(url_for("admin_users"))

    daire_options = ["Abone İşleri Dairesi Başkanlığı"]
    sube_options = [
        "Sayaç İşleri Şube Müdürlüğü",
        "Abone İşleri Şube Müdürlüğü",
        "Müşteri Hizmetleri Şube Müdürlüğü",
        "Tahakkuk İşleri Şube Müdürlüğü",
    ]

    if request.method == "POST":
        daire = request.form.get("daire_baskanligi", "").strip()
        sube = request.form.get("sube_mudurlugu", "").strip()
        ad_soyad = request.form.get("ad_soyad", "").strip()
        sicil_no = request.form.get("sicil_no", "").strip()
        ekip_kodu = request.form.get("ekip_kodu", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        password_confirm = request.form.get("password_confirm", "")

        if not email or "@" not in email:
            flash("Geçerli bir e-posta girin.", "error")
            return render_template("admin_user_add.html", daire_options=daire_options, sube_options=sube_options)
        if len(password) < 6:
            flash("Şifre en az 6 karakter olmalı.", "error")
            return render_template("admin_user_add.html", daire_options=daire_options, sube_options=sube_options)
        if password != password_confirm:
            flash("Şifre tekrar alanı uyuşmuyor.", "error")
            return render_template("admin_user_add.html", daire_options=daire_options, sube_options=sube_options)
        if User.query.filter((User.username == email) | (User.email == email)).first():
            flash("Bu e-posta zaten kayıtlı.", "error")
            return render_template("admin_user_add.html", daire_options=daire_options, sube_options=sube_options)

        user = User(username=email, email=email, password_hash=generate_password_hash(password))
        db.session.add(user)
        db.session.flush()
        profile = get_or_create_profile(user.id)
        profile.daire_baskanligi = daire
        profile.sube_mudurlugu = sube
        profile.ad_soyad = ad_soyad
        profile.sicil_no = sicil_no
        profile.ekip_kodu = ekip_kodu
        db.session.commit()
        flash("Yeni kişi eklendi.", "success")
        return redirect(url_for("admin_users"))

    return render_template("admin_user_add.html", daire_options=daire_options, sube_options=sube_options)


@app.get("/admin/authorized-users")
@login_required
@admin_required
def admin_authorized_users():
    founder = session_login_user()
    perms = DelegatedAdminPermission.query.filter_by(owner_user_id=founder.id).all()
    delegates = []
    for p in perms:
        u = User.query.get(p.delegate_user_id)
        if not u:
            continue
        try:
            allowed_count = len({int(x) for x in json.loads(p.allowed_user_ids_json or "[]") if str(x).isdigit()})
        except Exception:
            allowed_count = 0
        delegates.append({"user": u, "perm": p, "allowed_count": allowed_count})
    delegates.sort(key=lambda x: (x["user"].email or "").lower())
    return render_template("admin_authorized_users.html", delegates=delegates)


@app.post("/admin/authorized-users/<int:delegate_user_id>/remove")
@login_required
@admin_required
def admin_remove_authorized_user(delegate_user_id: int):
    founder = session_login_user()
    perm = DelegatedAdminPermission.query.filter_by(owner_user_id=founder.id, delegate_user_id=delegate_user_id).first()
    if perm:
        db.session.delete(perm)
        db.session.commit()
        flash("Yetki kaldırıldı.", "success")
    else:
        flash("Yetki kaydı bulunamadı.", "error")
    return redirect(url_for("admin_authorized_users"))


@app.post("/admin/users/<int:target_user_id>/delete")
@login_required
@admin_required
def admin_delete_user(target_user_id: int):
    founder = session_login_user()
    target = User.query.get(target_user_id)
    if not target:
        flash("Kullanıcı bulunamadı.", "error")
        return redirect(url_for("admin_users"))
    if founder and target.id == founder.id:
        flash("Kurucu kullanıcı kendisini silemez.", "error")
        return redirect(url_for("admin_users"))

    try:
        # Kullanıcıya ait tüm verileri temizle
        OvertimeEntry.query.filter_by(user_id=target.id).delete()
        UserProfile.query.filter_by(user_id=target.id).delete()
        DelegatedAdminPermission.query.filter(
            (DelegatedAdminPermission.delegate_user_id == target.id)
            | (DelegatedAdminPermission.owner_user_id == target.id)
        ).delete(synchronize_session=False)
        db.session.delete(target)
        db.session.commit()
        flash("Kullanıcı ve tüm verileri silindi.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(f"Kullanıcı silinemedi: {exc}", "error")
    return redirect(url_for("admin_users"))


@app.get("/admin/impersonate/<int:target_user_id>")
@login_required
@admin_or_delegate_required
def admin_impersonate(target_user_id: int):
    target = User.query.get(target_user_id)
    if not target:
        flash("Kullanıcı bulunamadı.", "error")
        return redirect(url_for("admin_users"))
    login_user = session_login_user()
    if not login_user:
        return redirect(url_for("login"))
    if not delegate_can(login_user, "impersonate"):
        flash("Kullanıcı ekranı görme yetkiniz yok.", "error")
        return redirect(url_for("admin_users"))
    allowed_ids = allowed_user_ids_for(login_user)
    if allowed_ids is not None and target.id not in allowed_ids:
        flash("Bu kullanıcıyı açma yetkiniz yok.", "error")
        return redirect(url_for("admin_users"))
    session["admin_original_user_id"] = login_user.id
    session["admin_impersonate_user_id"] = target.id
    session["user_id"] = login_user.id
    flash(f"{target.email} kullanıcısı olarak görüntüleme açıldı.", "success")
    return redirect(url_for("dashboard"))


@app.post("/admin/stop-impersonation")
@login_required
@admin_or_delegate_required
def admin_stop_impersonation():
    session.pop("admin_impersonate_user_id", None)
    session.pop("admin_original_user_id", None)
    flash("Kurucu kullanıcı görünümüne geri dönüldü.", "success")
    return redirect(url_for("admin_users"))


@app.post("/admin/users/export.xlsx")
@login_required
@admin_or_delegate_required
def admin_export_selected_users_xlsx():
    selected_ids = [int(v) for v in request.form.getlist("selected_user_ids") if str(v).isdigit()]
    year = request.form.get("year", type=int)
    period = request.form.get("period", "").strip()
    if not selected_ids:
        flash("Lütfen en az bir kişi seçin.", "error")
        return redirect(url_for("admin_users"))
    login_user = session_login_user()
    allowed_ids = allowed_user_ids_for(login_user)
    if allowed_ids is not None:
        selected_ids = [uid for uid in selected_ids if uid in allowed_ids]
        if not selected_ids:
            flash("Seçtiğiniz kullanıcılar için yetkiniz yok.", "error")
            return redirect(url_for("admin_users"))
    if not year or "-" not in period:
        flash("Yıl/dönem bilgisi eksik.", "error")
        return redirect(url_for("admin_users"))
    try:
        sy, sm = (int(x) for x in period.split("-"))
    except Exception:
        flash("Dönem formatı hatalı.", "error")
        return redirect(url_for("admin_users"))

    users = User.query.filter(User.id.in_(selected_ids)).all()
    profiles = {p.user_id: p for p in UserProfile.query.filter(UserProfile.user_id.in_(selected_ids)).all()}
    p_start, p_end = period_for_start(sy, sm)

    sig_prefix = f"bulk_excel_sign_{login_user.id if login_user else 0}"
    chef_title = request.form.get("chef_title", "").strip()
    chef_name = request.form.get("chef_name", "").strip()
    manager_title = request.form.get("manager_title", "").strip()
    manager_name = request.form.get("manager_name", "").strip()
    director_title = request.form.get("director_title", "").strip()
    director_name = request.form.get("director_name", "").strip()
    set_setting_value(f"{sig_prefix}_chef_title", chef_title)
    set_setting_value(f"{sig_prefix}_chef_name", chef_name)
    set_setting_value(f"{sig_prefix}_manager_title", manager_title)
    set_setting_value(f"{sig_prefix}_manager_name", manager_name)
    set_setting_value(f"{sig_prefix}_director_title", director_title)
    set_setting_value(f"{sig_prefix}_director_name", director_name)
    db.session.commit()

    template_candidates = [
        os.path.join(os.path.dirname(__file__), "Toplu_Mesai_Sablon.xlsx"),
        os.path.join(os.path.dirname(__file__), "fazla_mesai_cizelge.xlsx"),
        os.path.join(os.path.dirname(__file__), "sablon.xlsx"),
        os.path.join(os.path.dirname(__file__), "..", "app", "src", "main", "assets", "sablon.xlsx"),
    ]
    template_path = next((p for p in template_candidates if os.path.exists(p)), "")
    if not template_path:
        flash("Toplu rapor şablonu bulunamadı (Toplu_Mesai_Sablon.xlsx).", "error")
        return redirect(url_for("admin_users"))
    wb = load_workbook(template_path)
    ws = wb[wb.sheetnames[0]]

    month_names_upper = ["OCAK", "ŞUBAT", "MART", "NİSAN", "MAYIS", "HAZİRAN", "TEMMUZ", "AĞUSTOS", "EYLÜL", "EKİM", "KASIM", "ARALIK"]
    first_month_upper = month_names_upper[sm - 1]
    second_month_upper = month_names_upper[p_end.month - 1]
    period_year_value = p_end.year

    # G..AK (7..37) kolonları: 24..31 + 1..23
    day_numbers_in_sheet = list(range(24, 32)) + list(range(1, 24))
    day_col_map = {}
    cur_y, cur_m = sy, sm
    prev_day_num = None
    for idx, day_num in enumerate(day_numbers_in_sheet):
        col = 7 + idx
        if prev_day_num is not None and day_num < prev_day_num:
            cur_y, cur_m = add_month(cur_y, cur_m)
        try:
            day_col_map[date(cur_y, cur_m, day_num).isoformat()] = col
        except Exception:
            pass
        prev_day_num = day_num

    export_rows = []
    for u in users:
        p = profiles.get(u.id) or UserProfile(user_id=u.id)
        _, _, rows = report_period_rows_for_export(u.id, sy, sm)
        total60 = sum(float(r.get("pct60", 0) or 0) for r in rows)
        total15 = sum(float(r.get("pct15", 0) or 0) for r in rows)
        total_pazar = sum(float(r.get("pazar", 0) or 0) for r in rows)
        total_bayram = sum(float(r.get("bayram", 0) or 0) for r in rows)
        if abs(total60) < 1e-9 and abs(total15) < 1e-9 and abs(total_pazar) < 1e-9 and abs(total_bayram) < 1e-9:
            continue
        export_rows.append(
            {
                "user": u,
                "profile": p,
                "rows": rows,
                "total60": total60,
                "total15": total15,
                "total_pazar": total_pazar,
                "total_bayram": total_bayram,
                "name_sort": (p.ad_soyad or u.email or "").strip().lower(),
            }
        )

    export_rows.sort(key=lambda x: x["name_sort"])
    if not export_rows:
        flash("Seçtiğiniz kişilerde bu dönem için mesai kaydı bulunamadı.", "error")
        return redirect(url_for("admin_users"))

    base_row = 8
    max_row_for_people = 206
    row_step = 2
    person_capacity = ((max_row_for_people - base_row) // row_step) + 1
    if len(export_rows) > person_capacity:
        flash(f"Şablon en fazla {person_capacity} personel destekliyor.", "error")
        return redirect(url_for("admin_users"))

    grand_60 = 0.0
    grand_15 = 0.0
    grand_pazar = 0.0
    grand_bayram = 0.0
    holiday_day_isos = set()

    for idx, item in enumerate(export_rows):
        row60 = base_row + (idx * row_step)
        row15 = row60 + 1
        p = item["profile"]
        u = item["user"]
        rows_by_day = {r["work_date"].isoformat(): r for r in item["rows"]}

        ws.cell(row=row60, column=3).value = p.sicil_no or ""  # C
        ws.cell(row=row60, column=4).value = p.ad_soyad or u.email  # D

        for day_iso, col in day_col_map.items():
            r = rows_by_day.get(day_iso)
            if not r:
                continue
            v60 = float(r.get("pct60", 0) or 0)
            v15 = float(r.get("pct15", 0) or 0)
            vp = float(r.get("pazar", 0) or 0)
            vb = float(r.get("bayram", 0) or 0)
            # Gunluk tabloda:
            # - Sadece pazar/bayram: 1
            # - Pazar/bayram + ek saat: 1+ekSaat
            # - Sadece %60: saat
            day_marker = vp + vb
            if day_marker > 0 and v60 > 0:
                ws.cell(row=row60, column=col).value = f"{fmt_num(day_marker)}+{fmt_num(v60)}"
            elif day_marker > 0:
                ws.cell(row=row60, column=col).value = day_marker
            else:
                ws.cell(row=row60, column=col).value = v60 if abs(v60) > 1e-9 else None
            ws.cell(row=row15, column=col).value = v15 if abs(v15) > 1e-9 else None
            if abs(vb) > 1e-9:
                holiday_day_isos.add(day_iso)

        total60 = float(item["total60"])
        total15 = float(item["total15"])
        total_pazar = float(item["total_pazar"])
        total_bayram = float(item["total_bayram"])
        ws.cell(row=row60, column=38).value = total60 if abs(total60) > 1e-9 else None  # AL
        ws.cell(row=row15, column=38).value = total15 if abs(total15) > 1e-9 else None  # AL
        ws.cell(row=row60, column=39).value = total_pazar if abs(total_pazar) > 1e-9 else None  # AM
        ws.cell(row=row60, column=40).value = total_bayram if abs(total_bayram) > 1e-9 else None  # AN

        grand_60 += total60
        grand_15 += total15
        grand_pazar += total_pazar
        grand_bayram += total_bayram

    def set_cell_value_safe(cell_ref: str, value):
        cell = ws[cell_ref]
        if not isinstance(cell, MergedCell):
            cell.value = value
            return
        for merged_range in ws.merged_cells.ranges:
            if cell_ref in merged_range:
                ws.cell(row=merged_range.min_row, column=merged_range.min_col).value = value
                return
        # Beklenmeyen durumda yine de deneyelim
        ws[cell_ref].value = value

    first_profile = export_rows[0]["profile"]
    set_cell_value_safe("B2", tr_upper(first_profile.sube_mudurlugu or ""))
    set_cell_value_safe("G5", first_month_upper)
    set_cell_value_safe("O5", second_month_upper)

    people_count = len(export_rows)
    set_cell_value_safe("D209", (
        f"Yukarıda adı soyadı yazılı {people_count} işçi, {period_year_value} yılı {first_month_upper} ve {second_month_upper} ayında toplam "
        f"{fmt_num(grand_60)} saat %60'lık, {fmt_num(grand_15)} saat %15'lik, {fmt_num(grand_pazar)} gün PAZAR, "
        f"{fmt_num(grand_bayram)} gün BAYRAM, olarak fazla çalışma yapmıştır."
    ))
    set_cell_value_safe("D213", chef_title or "")
    set_cell_value_safe("D214", chef_name or "")
    set_cell_value_safe("Q213", manager_title or "")
    set_cell_value_safe("Q214", manager_name or "")
    set_cell_value_safe("AC213", director_title or "")
    set_cell_value_safe("AC214", director_name or "")

    last_used_row60 = base_row + ((people_count - 1) * row_step)
    for r in range(last_used_row60 + 2, 208):
        ws.row_dimensions[r].hidden = True

    weekend_fill = PatternFill(fill_type="solid", start_color="FFD9D9D9", end_color="FFD9D9D9")
    for day_iso, col in day_col_map.items():
        try:
            d = parse_date(day_iso)
        except Exception:
            continue
        is_weekend = d.weekday() in (5, 6)
        is_official_holiday = day_iso in holiday_day_isos
        if is_weekend or is_official_holiday:
            for r in range(6, 208):
                ws.cell(row=r, column=col).fill = weekend_fill

    mem = io.BytesIO()
    wb.save(mem)
    mem.seek(0)
    return send_file(
        mem,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=f"Toplu_Mesai_{year}_{sm:02d}_{format_dmy(p_start)}_{format_dmy(p_end)}.xlsx",
    )


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings_page():
    user = ensure_user_or_redirect()
    if user is None:
        flash("Oturum süresi doldu, lütfen tekrar giriş yapın.", "error")
        return redirect(url_for("login"))

    if request.method == "POST":
        action = request.form.get("action", "").strip()
        try:
            if action == "change_password":
                old_password = request.form.get("old_password", "")
                new_password = request.form.get("new_password", "")
                new_password_confirm = request.form.get("new_password_confirm", "")
                if not check_password_hash(user.password_hash, old_password):
                    flash("Eski şifre hatalı.", "error")
                    return redirect(url_for("settings_page"))
                if len(new_password) < 6:
                    flash("Yeni şifre en az 6 karakter olmalı.", "error")
                    return redirect(url_for("settings_page"))
                if new_password != new_password_confirm:
                    flash("Yeni şifre tekrar alanı uyuşmuyor.", "error")
                    return redirect(url_for("settings_page"))
                user.password_hash = generate_password_hash(new_password)
                db.session.commit()
                flash("Şifre başarıyla değiştirildi.", "success")
            elif action == "apk_refresh":
                OvertimeEntry.query.filter_by(user_id=user.id).delete()
                p = get_or_create_profile(user.id)
                p.daire_baskanligi = ""
                p.sube_mudurlugu = ""
                p.ad_soyad = ""
                p.sicil_no = ""
                p.ekip_kodu = ""
                db.session.commit()
                flash("Web verileri temizlendi. APK'de giriş yapıp senkron yaptığınızda veriler yeniden yüklenecek.", "success")
            elif action == "clear_all":
                OvertimeEntry.query.filter_by(user_id=user.id).delete()
                p = get_or_create_profile(user.id)
                p.daire_baskanligi = ""
                p.sube_mudurlugu = ""
                p.ad_soyad = ""
                p.sicil_no = ""
                p.ekip_kodu = ""
                db.session.commit()
                flash("Web tarafındaki tüm veriler silindi.", "success")
            else:
                flash("Geçersiz işlem.", "error")
        except Exception as exc:
            db.session.rollback()
            flash(f"Ayar işlemi başarısız: {exc}", "error")
        return redirect(url_for("settings_page"))

    all_entries = OvertimeEntry.query.filter_by(user_id=user.id).order_by(OvertimeEntry.work_date.desc(), OvertimeEntry.id.desc()).all()
    start_options = sorted({(period_start_for_date(e.work_date).year, period_start_for_date(e.work_date).month) for e in all_entries}, reverse=True)
    if not start_options:
        ps = period_start_for_date(date.today())
        start_options = [(ps.year, ps.month)]
    selected_year = period_year(start_options[0][0], start_options[0][1])
    active_start = start_options[0]
    period_value = f"{active_start[0]:04d}-{active_start[1]:02d}"

    return render_template(
        "settings.html",
        selected_year=selected_year,
        period_value=period_value,
    )


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    user = ensure_user_or_redirect()
    if user is None:
        flash("Oturum süresi doldu, lütfen tekrar giriş yapın.", "error")
        return redirect(url_for("login"))
    p = get_or_create_profile(user.id)
    if request.method == "POST":
        p.daire_baskanligi = request.form.get("daire_baskanligi", "").strip()
        p.sube_mudurlugu = request.form.get("sube_mudurlugu", "").strip()
        p.ad_soyad = request.form.get("ad_soyad", "").strip()
        p.sicil_no = request.form.get("sicil_no", "").strip()
        p.ekip_kodu = request.form.get("ekip_kodu", "").strip()
        db.session.commit()
        flash("Profil bilgileri güncellendi.", "success")
        return redirect(url_for("profile"))
    return render_template("profile.html", user=user, profile=p)


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    reset_url = None
    sent_via_smtp = False
    if request.method == "POST":
        ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown")
        if is_rate_limited(f"forgot:{ip}", limit=8, window_sec=60):
            flash("Çok fazla deneme. Lütfen 1 dakika sonra tekrar deneyin.", "error")
            return render_template("forgot_password.html", reset_url=None, sent_via_smtp=False)
        email = request.form.get("email", "").strip()
        user = User.query.filter_by(email=email).first()
        if user:
            token = token_serializer.dumps({"uid": user.id}, salt="reset-password")
            reset_url = f"{app.config['SITE_BASE_URL']}{url_for('reset_password', token=token)}"
            try:
                sent_via_smtp = send_reset_email(user.email, reset_url)
            except Exception:
                sent_via_smtp = False
        flash("E-posta kayıtlıysa şifre sıfırlama bağlantısı oluşturuldu.", "success")
    return render_template("forgot_password.html", reset_url=reset_url if not sent_via_smtp else None, sent_via_smtp=sent_via_smtp)


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    max_age = app.config["RESET_TOKEN_EXPIRE_MIN"] * 60
    try:
        data = token_serializer.loads(token, salt="reset-password", max_age=max_age)
    except SignatureExpired:
        flash("Bağlantının süresi doldu.", "error")
        return redirect(url_for("forgot_password"))
    except BadSignature:
        flash("Geçersiz bağlantı.", "error")
        return redirect(url_for("forgot_password"))
    user = User.query.get(data["uid"])
    if not user:
        flash("Kullanıcı bulunamadı.", "error")
        return redirect(url_for("forgot_password"))
    if request.method == "POST":
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        if len(password) < 6:
            flash("Şifre en az 6 karakter olmalı.", "error")
            return render_template("reset_password.html")
        if password != confirm:
            flash("Şifreler eşleşmiyor.", "error")
            return render_template("reset_password.html")
        user.password_hash = generate_password_hash(password)
        db.session.commit()
        flash("Şifre güncellendi. Giriş yapabilirsiniz.", "success")
        return redirect(url_for("login"))
    return render_template("reset_password.html")


@app.get("/api/day-defaults")
@login_required
def api_day_defaults_web():
    ymd = request.args.get("date", "")
    end_override = request.args.get("endTime", "")
    try:
        d = parse_date(ymd)
    except Exception:
        return jsonify({"error": "invalid_date"}), 400
    defaults = day_defaults(d, end_override if end_override else None)
    return jsonify(defaults)


@app.route("/dashboard", methods=["GET", "POST"])
@login_required
def dashboard():
    user = ensure_user_or_redirect()
    if user is None:
        flash("Oturum süresi doldu, lütfen tekrar giriş yapın.", "error")
        return redirect(url_for("login"))
    profile = get_or_create_profile(user.id)
    if request.method == "POST":
        try:
            entry = OvertimeEntry(
                user_id=user.id,
                work_date=parse_date(request.form.get("work_date", "")),
                start_time=request.form.get("start_time", "").strip(),
                end_time=request.form.get("end_time", "").strip(),
                pct60=parse_float(request.form.get("pct60", "0")),
                pct15=parse_float(request.form.get("pct15", "0")),
                pazar=parse_float(request.form.get("pazar", "0")),
                bayram=parse_float(request.form.get("bayram", "0")),
                description=request.form.get("description", "").strip(),
            )
            dup = OvertimeEntry.query.filter_by(
                user_id=user.id,
                work_date=entry.work_date,
                start_time=entry.start_time,
                end_time=entry.end_time,
            ).first()
            if dup:
                flash("Aynı gün ve saat için mükerrer mesai girilemez.", "error")
                return redirect(url_for("dashboard"))
            db.session.add(entry)
            db.session.commit()
            flash("Mesai kaydı eklendi.", "success")
        except Exception as exc:
            db.session.rollback()
            flash(f"Kayıt eklenemedi: {exc}", "error")
        return redirect(url_for("dashboard"))
    entries = OvertimeEntry.query.filter_by(user_id=user.id).all()
    recent_items = build_recent_ui_items(entries)
    return render_template(
        "dashboard.html",
        user=user,
        profile=profile,
        recent_items=recent_items,
        apk_url=app.config["APK_URL"],
        api_token=session.get("api_token"),
    )


@app.route("/entries/<int:entry_id>/edit", methods=["GET", "POST"])
@login_required
def edit_entry(entry_id: int):
    user = ensure_user_or_redirect()
    if user is None:
        flash("Oturum süresi doldu, lütfen tekrar giriş yapın.", "error")
        return redirect(url_for("login"))
    entry = OvertimeEntry.query.filter_by(id=entry_id, user_id=user.id).first_or_404()
    if request.method == "POST":
        try:
            entry.work_date = parse_date(request.form.get("work_date", ""))
            entry.start_time = request.form.get("start_time", "").strip()
            entry.end_time = request.form.get("end_time", "").strip()
            entry.pct60 = parse_float(request.form.get("pct60", "0"))
            entry.pct15 = parse_float(request.form.get("pct15", "0"))
            entry.pazar = parse_float(request.form.get("pazar", "0"))
            entry.bayram = parse_float(request.form.get("bayram", "0"))
            entry.description = request.form.get("description", "").strip()

            # Aynı kullanıcıda aynı tarih+saat mükerrer kayıtlar varsa tek kayda indir.
            # Eski senkronlardan kalan kopyalar bu şekilde temizlenir.
            duplicates = OvertimeEntry.query.filter(
                OvertimeEntry.user_id == user.id,
                OvertimeEntry.work_date == entry.work_date,
                OvertimeEntry.start_time == entry.start_time,
                OvertimeEntry.end_time == entry.end_time,
                OvertimeEntry.id != entry.id,
            ).all()
            for d in duplicates:
                db.session.delete(d)

            db.session.commit()
            if duplicates:
                flash(f"Kayıt güncellendi. {len(duplicates)} mükerrer kayıt temizlendi.", "success")
            else:
                flash("Kayıt güncellendi.", "success")
            back = request.form.get("back", "dashboard")
            return redirect(url_for("reports") if back == "reports" else url_for("dashboard"))
        except Exception as exc:
            db.session.rollback()
            flash(f"Güncelleme başarısız: {exc}", "error")
    return render_template("entry_edit.html", entry=entry, back=request.args.get("back", "dashboard"))


@app.post("/entries/<int:entry_id>/delete")
@login_required
def delete_entry(entry_id: int):
    user = ensure_user_or_redirect()
    if user is None:
        flash("Oturum süresi doldu, lütfen tekrar giriş yapın.", "error")
        return redirect(url_for("login"))
    entry = OvertimeEntry.query.filter_by(id=entry_id, user_id=user.id).first_or_404()
    db.session.delete(entry)
    db.session.commit()
    flash("Kayıt silindi.", "success")
    back = request.form.get("back", "dashboard")
    return redirect(url_for("reports") if back == "reports" else url_for("dashboard"))


@app.get("/reports")
@login_required
def reports():
    user = ensure_user_or_redirect()
    if user is None:
        flash("Oturum süresi doldu, lütfen tekrar giriş yapın.", "error")
        return redirect(url_for("login"))
    profile = get_or_create_profile(user.id)
    all_entries = OvertimeEntry.query.filter_by(user_id=user.id).order_by(OvertimeEntry.work_date.desc(), OvertimeEntry.id.desc()).all()
    start_options = sorted({(period_start_for_date(e.work_date).year, period_start_for_date(e.work_date).month) for e in all_entries}, reverse=True)
    if not start_options:
        ps = period_start_for_date(date.today())
        start_options = [(ps.year, ps.month)]
    years = sorted({period_year(y, m) for (y, m) in start_options}, reverse=True)
    selected_year = request.args.get("year", type=int) or years[0]
    period_options = [(y, m) for (y, m) in start_options if period_year(y, m) == selected_year] or [start_options[0]]
    selected_period = request.args.get("period", "")
    active_start = period_options[0]
    if selected_period and "-" in selected_period:
        sy, sm = (int(x) for x in selected_period.split("-"))
        if (sy, sm) in period_options:
            active_start = (sy, sm)
    p_start, p_end = period_for_start(active_start[0], active_start[1])
    period_entries_raw = (
        OvertimeEntry.query.filter(
            OvertimeEntry.user_id == user.id,
            OvertimeEntry.work_date >= p_start,
            OvertimeEntry.work_date <= p_end,
        )
        .order_by(OvertimeEntry.work_date.asc(), OvertimeEntry.start_time.asc(), OvertimeEntry.id.asc())
        .all()
    )
    period_rows = grouped_period_rows(period_entries_raw)
    period_total = {
        "pct60": sum(e["pct60"] for e in period_rows),
        "pct15": sum(e["pct15"] for e in period_rows),
        "pazar": sum(e["pazar"] for e in period_rows),
        "bayram": sum(e["bayram"] for e in period_rows),
    }
    yearly_entries = [e for e in all_entries if period_year(period_start_for_date(e.work_date).year, period_start_for_date(e.work_date).month) == selected_year]
    year_rows = grouped_period_rows(yearly_entries)
    year_total = {
        "pct60": sum(e["pct60"] for e in year_rows),
        "pct15": sum(e["pct15"] for e in year_rows),
        "pazar": sum(e["pazar"] for e in year_rows),
        "bayram": sum(e["bayram"] for e in year_rows),
    }
    return render_template(
        "reports.html",
        user=user,
        profile=profile,
        years=years,
        selected_year=selected_year,
        period_options=period_options,
        active_start=active_start,
        rows=period_rows,
        period_start=p_start,
        period_end=p_end,
        period_total=period_total,
        year_total=year_total,
        weekday_tr=weekday_tr,
        format_dmy=format_dmy,
    )


@app.post("/reports/import")
@login_required
def import_reports_backup():
    user = ensure_user_or_redirect()
    if user is None:
        flash("Oturum süresi doldu, lütfen tekrar giriş yapın.", "error")
        return redirect(url_for("login"))
    profile = get_or_create_profile(user.id)
    f = request.files.get("backup_file")
    back = request.form.get("back", "reports")
    redirect_target = "settings_page" if back == "settings" else "reports"
    if f is None or f.filename == "":
        flash("İçe aktarma için dosya seçin.", "error")
        return redirect(url_for(redirect_target))
    try:
        raw = f.read()
        if not raw:
            flash("Dosya boş.", "error")
            return redirect(url_for(redirect_target))
        payload = json.loads(raw.decode("utf-8-sig", errors="strict"))
        if not isinstance(payload, dict):
            raise ValueError("Geçersiz JSON yapısı")
        prof = payload.get("profile", {})
        entries = payload.get("entries", [])
        if isinstance(prof, dict):
            profile.daire_baskanligi = str(prof.get("daireBaskanligi", profile.daire_baskanligi))
            profile.sube_mudurlugu = str(prof.get("subeMudurlugu", profile.sube_mudurlugu))
            profile.ad_soyad = str(prof.get("adSoyad", profile.ad_soyad))
            profile.sicil_no = str(prof.get("sicilNo", profile.sicil_no))
            profile.ekip_kodu = str(prof.get("ekipKodu", profile.ekip_kodu))
        inserted = 0
        for e in entries if isinstance(entries, list) else []:
            if not isinstance(e, dict):
                continue
            work_date = str(e.get("workDate", "")).strip()
            start_time = str(e.get("startTime", "")).strip()
            end_time = str(e.get("endTime", "")).strip()
            if not work_date or not start_time or not end_time:
                continue
            try:
                wd = parse_date(work_date)
            except Exception:
                continue
            dup = OvertimeEntry.query.filter_by(
                user_id=user.id,
                work_date=wd,
                start_time=start_time,
                end_time=end_time,
            ).first()
            if dup:
                continue
            row = OvertimeEntry(
                user_id=user.id,
                work_date=wd,
                start_time=start_time,
                end_time=end_time,
                pct60=float(e.get("pct60", 0) or 0),
                pct15=float(e.get("pct15", 0) or 0),
                pazar=float(e.get("pazar", 0) or 0),
                bayram=float(e.get("bayram", 0) or 0),
                description=str(e.get("description", "")),
            )
            db.session.add(row)
            inserted += 1
        db.session.commit()
        flash(f"İçe aktarma tamamlandı. Eklenen kayıt: {inserted}", "success")
    except Exception as exc:
        db.session.rollback()
        flash(f"İçe aktarma başarısız: {exc}", "error")
    return redirect(url_for(redirect_target))


def report_period_rows_for_export(user_id: int, sy: int, sm: int):
    p_start, p_end = period_for_start(sy, sm)
    entries = (
        OvertimeEntry.query.filter(
            OvertimeEntry.user_id == user_id,
            OvertimeEntry.work_date >= p_start,
            OvertimeEntry.work_date <= p_end,
        )
        .order_by(OvertimeEntry.work_date.asc(), OvertimeEntry.start_time.asc(), OvertimeEntry.id.asc())
        .all()
    )
    return p_start, p_end, grouped_period_rows(entries)


@app.get("/reports/export.csv")
@login_required
def export_reports_csv():
    user = ensure_user_or_redirect()
    if user is None:
        flash("Oturum süresi doldu, lütfen tekrar giriş yapın.", "error")
        return redirect(url_for("login"))
    year = request.args.get("year", type=int)
    period = request.args.get("period", "")
    if not year or "-" not in period:
        flash("Yıl/dönem bilgisi eksik.", "error")
        return redirect(url_for("reports"))
    sy, sm = (int(x) for x in period.split("-"))
    _, _, rows = report_period_rows_for_export(user.id, sy, sm)
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["Tarih", "Gun", "Baslama", "Bitis", "%60", "%15", "Pazar", "Bayram", "Aciklama"])
    for r in rows:
        writer.writerow(
            [
                format_dmy(r["work_date"]),
                weekday_tr(r["work_date"]),
                r["start_time"],
                r["end_time"],
                fmt_num(r["pct60"]),
                fmt_num(r["pct15"]),
                fmt_num(r["pazar"]),
                fmt_num(r["bayram"]),
                r["description"],
            ]
        )
    mem = io.BytesIO(output.getvalue().encode("utf-8-sig"))
    mem.seek(0)
    return send_file(mem, mimetype="text/csv", as_attachment=True, download_name=f"Mesai_{year}_{sm:02d}.csv")


@app.get("/reports/export.xlsx")
@login_required
def export_reports_xlsx():
    user = ensure_user_or_redirect()
    if user is None:
        flash("Oturum süresi doldu, lütfen tekrar giriş yapın.", "error")
        return redirect(url_for("login"))
    profile = get_or_create_profile(user.id)
    year = request.args.get("year", type=int)
    period = request.args.get("period", "")
    if not year or "-" not in period:
        flash("Yıl/dönem bilgisi eksik.", "error")
        return redirect(url_for("reports"))
    sy, sm = (int(x) for x in period.split("-"))
    p_start, p_end, rows = report_period_rows_for_export(user.id, sy, sm)
    totals = {
        "pct60": sum(r["pct60"] for r in rows),
        "pct15": sum(r["pct15"] for r in rows),
        "pazar": sum(r["pazar"] for r in rows),
        "bayram": sum(r["bayram"] for r in rows),
    }

    template_candidates = [
        os.path.join(os.path.dirname(__file__), "sablon.xlsx"),
        os.path.join(os.path.dirname(__file__), "..", "app", "src", "main", "assets", "sablon.xlsx"),
    ]
    template_path = next((p for p in template_candidates if os.path.exists(p)), "")
    if not template_path:
        flash("Excel şablonu bulunamadı (sablon.xlsx). Lütfen şablon dosyasını web-portal klasörüne ekleyin.", "error")
        return redirect(url_for("reports", year=year, period=period))

    wb = load_workbook(template_path)
    ws = wb[wb.sheetnames[0]]
    ws["D3"] = profile.daire_baskanligi
    ws["D4"] = profile.sube_mudurlugu
    ws["D5"] = profile.ad_soyad
    ws["D6"] = profile.sicil_no
    end_month_name = ["OCAK", "ŞUBAT", "MART", "NİSAN", "MAYIS", "HAZİRAN", "TEMMUZ", "AĞUSTOS", "EYLÜL", "EKİM", "KASIM", "ARALIK"][p_end.month - 1]
    ws["H10"] = end_month_name
    ws["J10"] = p_end.year
    day_map = {r["work_date"].isoformat(): r for r in rows}
    next_y, next_m = add_month(sy, sm)
    for row_num in range(14, 45):
        day_num = 24 + (row_num - 14) if row_num <= 21 else (row_num - 21)
        cur_date = None
        try:
            if row_num <= 21:
                cur_date = date(sy, sm, day_num)
            else:
                cur_date = date(next_y, next_m, day_num)
        except Exception:
            cur_date = None
        data = day_map.get(cur_date.isoformat()) if cur_date else None
        ws[f"B{row_num}"] = data["start_time"] if data else None
        ws[f"C{row_num}"] = data["end_time"] if data else None
        ws[f"D{row_num}"] = data["pct60"] if data and abs(data["pct60"]) > 1e-9 else None
        ws[f"E{row_num}"] = data["pct15"] if data and abs(data["pct15"]) > 1e-9 else None
        ws[f"F{row_num}"] = data["pazar"] if data and abs(data["pazar"]) > 1e-9 else None
        ws[f"G{row_num}"] = data["bayram"] if data and abs(data["bayram"]) > 1e-9 else None
        ws[f"H{row_num}"] = data["description"] if data and data["description"] else None
        has_any = data and (
            data["start_time"] or data["end_time"] or abs(data["pct60"]) > 1e-9 or abs(data["pct15"]) > 1e-9 or abs(data["pazar"]) > 1e-9 or abs(data["bayram"]) > 1e-9 or data["description"]
        )
        ws[f"I{row_num}"] = profile.ekip_kodu if has_any else None
    ws["D45"] = totals["pct60"] if abs(totals["pct60"]) > 1e-9 else None
    ws["E45"] = totals["pct15"] if abs(totals["pct15"]) > 1e-9 else None
    ws["F45"] = totals["pazar"] if abs(totals["pazar"]) > 1e-9 else None
    ws["G45"] = totals["bayram"] if abs(totals["bayram"]) > 1e-9 else None
    mem = io.BytesIO()
    wb.save(mem)
    mem.seek(0)
    return send_file(mem, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", as_attachment=True, download_name=f"Mesai_{year}_{sm:02d}.xlsx")


@app.get("/download-apk")
@login_required
def download_apk():
    user = ensure_user_or_redirect()
    if user is None:
        flash("Oturum süresi doldu, lütfen tekrar giriş yapın.", "error")
        return redirect(url_for("login"))

    # APK uygulamasiyla ayni guncelleme kaynagi: update manifest -> apkUrl.
    manifest_url = (app.config.get("UPDATE_MANIFEST_URL") or "").strip()
    if manifest_url:
        try:
            req = urllib.request.Request(
                manifest_url,
                headers={"User-Agent": "MesaiWebPortal/1.0"},
            )
            with urllib.request.urlopen(req, timeout=12) as resp:
                if resp.status == 200:
                    payload = json.loads(resp.read().decode("utf-8"))
                    apk_from_manifest = str(payload.get("apkUrl", "")).strip()
                    if apk_from_manifest:
                        return redirect(apk_from_manifest)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, KeyError):
            # Manifeste erisilemezse asagidaki fallback adimlari calissin.
            pass
        except Exception:
            pass

    # Dis URL tanimliysa (ornegin GitHub release), tek noktadan yonlendir.
    configured_apk_url = (app.config.get("APK_URL") or "").strip()
    if configured_apk_url and configured_apk_url != "/download-apk":
        return redirect(configured_apk_url)

    # Yerelde/depoda bulunan APK dosyalari arasindan en yeni olani indir.
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    candidate_patterns = [
        os.path.join(repo_root, "app", "build", "outputs", "apk", "release", "*.apk"),
        os.path.join(repo_root, "app", "build", "outputs", "apk", "debug", "*.apk"),
        os.path.join(repo_root, "web-portal", "static", "apk", "*.apk"),
        os.path.join(repo_root, "apk", "*.apk"),
    ]
    candidates = []
    for pattern in candidate_patterns:
        candidates.extend(glob.glob(pattern))
    candidates = [p for p in candidates if os.path.exists(p)]
    if candidates:
        latest_apk = max(candidates, key=lambda p: os.path.getmtime(p))
        return send_file(latest_apk, as_attachment=True, download_name=os.path.basename(latest_apk))

    flash("APK dosyası henüz üretilmemiş.", "error")
    return redirect(url_for("dashboard"))


@app.get("/apk-auto-login")
def apk_auto_login():
    token = request.args.get("token", "").strip()
    if not token:
        return redirect(url_for("login"))
    try:
        data = token_serializer.loads(token, max_age=60 * 60 * 24 * 30)
        uid = data.get("uid")
    except Exception:
        return redirect(url_for("login"))
    user = User.query.get(uid) if uid else None
    if not user:
        session.clear()
        return redirect(url_for("login"))
    session.clear()
    session["user_id"] = user.id
    # web oturumu için yeni token üret (nonce taze kalsın)
    session["api_token"] = token_serializer.dumps({"uid": user.id, "nonce": secrets.token_hex(8)})
    return redirect(url_for("dashboard"))


def bearer_user():
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    token = header.replace("Bearer ", "", 1).strip()
    try:
        data = token_serializer.loads(token, max_age=60 * 60 * 24 * 30)
    except Exception:
        return None
    return User.query.get(data.get("uid"))


def api_auth_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        user = bearer_user()
        if not user:
            return jsonify({"error": "unauthorized"}), 401
        request.api_user = user
        return view_func(*args, **kwargs)

    return wrapped


@app.post("/api/login")
def api_login():
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown")
    if is_rate_limited(f"api_login:{ip}", limit=20, window_sec=60):
        return jsonify({"error": "rate_limited"}), 429
    data = request.get_json(silent=True) or {}
    identity = str(data.get("email", data.get("usernameOrEmail", ""))).strip()
    password = str(data.get("password", ""))
    user = User.query.filter((User.username == identity) | (User.email == identity)).first()
    if not user or not check_password_hash(user.password_hash, password):
        return jsonify({"error": "invalid_credentials"}), 401
    token = token_serializer.dumps({"uid": user.id, "nonce": secrets.token_hex(8)})
    return jsonify({"token": token, "user": {"id": user.id, "email": user.email}})


@app.post("/api/register")
def api_register():
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown")
    if is_rate_limited(f"api_register:{ip}", limit=10, window_sec=60):
        return jsonify({"error": "rate_limited"}), 429
    data = request.get_json(silent=True) or {}
    email = str(data.get("email", "")).strip()
    password = str(data.get("password", ""))
    if "@" not in email:
        return jsonify({"error": "invalid_email"}), 400
    if len(password) < 6:
        return jsonify({"error": "invalid_password"}), 400
    if User.query.filter((User.username == email) | (User.email == email)).first():
        return jsonify({"error": "already_exists"}), 409
    user = User(username=email, email=email, password_hash=generate_password_hash(password))
    db.session.add(user)
    db.session.commit()
    get_or_create_profile(user.id)
    token = token_serializer.dumps({"uid": user.id, "nonce": secrets.token_hex(8)})
    return jsonify({"token": token, "user": {"id": user.id, "email": user.email}}), 201


@app.post("/api/change-password")
@api_auth_required
def api_change_password():
    user = request.api_user
    data = request.get_json(silent=True) or {}
    old_password = str(data.get("oldPassword", ""))
    new_password = str(data.get("newPassword", ""))
    if not check_password_hash(user.password_hash, old_password):
        return jsonify({"error": "invalid_old_password"}), 400
    if len(new_password) < 6:
        return jsonify({"error": "invalid_new_password"}), 400
    user.password_hash = generate_password_hash(new_password)
    db.session.commit()
    return jsonify({"ok": True})


@app.get("/api/profile")
@api_auth_required
def api_profile_get():
    user = request.api_user
    p = get_or_create_profile(user.id)
    return jsonify({
        "daireBaskanligi": p.daire_baskanligi or "",
        "subeMudurlugu": p.sube_mudurlugu or "",
        "adSoyad": p.ad_soyad or "",
        "sicilNo": p.sicil_no or "",
        "ekipKodu": p.ekip_kodu or "",
    })


@app.put("/api/profile")
@api_auth_required
def api_profile_put():
    user = request.api_user
    p = get_or_create_profile(user.id)
    data = request.get_json(silent=True) or {}
    p.daire_baskanligi = str(data.get("daireBaskanligi", p.daire_baskanligi or "")).strip()
    p.sube_mudurlugu = str(data.get("subeMudurlugu", p.sube_mudurlugu or "")).strip()
    p.ad_soyad = str(data.get("adSoyad", p.ad_soyad or "")).strip()
    p.sicil_no = str(data.get("sicilNo", p.sicil_no or "")).strip()
    p.ekip_kodu = str(data.get("ekipKodu", p.ekip_kodu or "")).strip()
    db.session.commit()
    return jsonify({"ok": True})


@app.get("/api/entries")
@api_auth_required
def api_entries():
    user = request.api_user
    updated_after = request.args.get("updatedAfter")
    q = OvertimeEntry.query.filter_by(user_id=user.id)
    if updated_after:
        try:
            dt = datetime.fromisoformat(updated_after)
            q = q.filter(OvertimeEntry.updated_at > dt)
        except Exception:
            return jsonify({"error": "invalid_updatedAfter"}), 400
    entries = q.order_by(OvertimeEntry.updated_at.asc()).all()
    return jsonify([entry_to_dict(e) for e in entries])


@app.post("/api/entries")
@api_auth_required
def api_create_entry():
    user = request.api_user
    data = request.get_json(silent=True) or {}
    try:
        work_date = parse_date(str(data.get("workDate", "")))
        start_time = str(data.get("startTime", ""))
        end_time = str(data.get("endTime", ""))

        # APK/web tekrar aynı kaydı gönderirse mükerrer oluşturma
        existing = OvertimeEntry.query.filter_by(
            user_id=user.id,
            work_date=work_date,
            start_time=start_time,
            end_time=end_time,
        ).first()
        if existing:
            return jsonify(entry_to_dict(existing)), 200

        entry = OvertimeEntry(
            user_id=user.id,
            work_date=work_date,
            start_time=start_time,
            end_time=end_time,
            pct60=float(data.get("pct60", 0)),
            pct15=float(data.get("pct15", 0)),
            pazar=float(data.get("pazar", 0)),
            bayram=float(data.get("bayram", 0)),
            description=str(data.get("description", "")),
        )
        db.session.add(entry)
        db.session.commit()
        return jsonify(entry_to_dict(entry)), 201
    except Exception as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 400


@app.put("/api/entries/<int:entry_id>")
@api_auth_required
def api_update_entry(entry_id: int):
    user = request.api_user
    data = request.get_json(silent=True) or {}
    entry = OvertimeEntry.query.filter_by(id=entry_id, user_id=user.id).first()
    if not entry:
        return jsonify({"error": "not_found"}), 404
    try:
        entry.work_date = parse_date(str(data.get("workDate", entry.work_date.isoformat())))
        entry.start_time = str(data.get("startTime", entry.start_time))
        entry.end_time = str(data.get("endTime", entry.end_time))
        entry.pct60 = float(data.get("pct60", entry.pct60))
        entry.pct15 = float(data.get("pct15", entry.pct15))
        entry.pazar = float(data.get("pazar", entry.pazar))
        entry.bayram = float(data.get("bayram", entry.bayram))
        entry.description = str(data.get("description", entry.description))
        db.session.commit()
        return jsonify(entry_to_dict(entry))
    except Exception as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 400


@app.delete("/api/entries/<int:entry_id>")
@api_auth_required
def api_delete_entry(entry_id: int):
    user = request.api_user
    entry = OvertimeEntry.query.filter_by(id=entry_id, user_id=user.id).first()
    if not entry:
        return jsonify({"error": "not_found"}), 404
    db.session.delete(entry)
    db.session.commit()
    return jsonify({"ok": True})


@app.cli.command("init-db")
def init_db():
    db.create_all()
    print("Database initialized.")


def sync_usernames_with_emails() -> int:
    users = User.query.all()
    changed = 0
    for u in users:
        email = (u.email or "").strip()
        if not email:
            continue
        if (u.username or "").strip() != email:
            u.username = email
            changed += 1
    if changed:
        db.session.commit()
    return changed


def ensure_delegated_permission_columns():
    inspector = db.inspect(db.engine)
    try:
        cols = {c["name"] for c in inspector.get_columns("delegated_admin_permission")}
    except Exception:
        return
    if "can_reset_password" not in cols:
        db.session.execute(db.text("ALTER TABLE delegated_admin_permission ADD COLUMN can_reset_password BOOLEAN NOT NULL DEFAULT FALSE"))
    if "can_view_users_screen" not in cols:
        db.session.execute(db.text("ALTER TABLE delegated_admin_permission ADD COLUMN can_view_users_screen BOOLEAN NOT NULL DEFAULT FALSE"))
    if "can_view_charts" not in cols:
        db.session.execute(db.text("ALTER TABLE delegated_admin_permission ADD COLUMN can_view_charts BOOLEAN NOT NULL DEFAULT FALSE"))
    if "can_view_filters" not in cols:
        db.session.execute(db.text("ALTER TABLE delegated_admin_permission ADD COLUMN can_view_filters BOOLEAN NOT NULL DEFAULT FALSE"))
    if "can_add_user" not in cols:
        db.session.execute(db.text("ALTER TABLE delegated_admin_permission ADD COLUMN can_add_user BOOLEAN NOT NULL DEFAULT FALSE"))
    # eski kolon varsa yeni yapıya taşımak için bir kez eşitle
    if "can_view_passwords" in cols:
        db.session.execute(
            db.text(
                "UPDATE delegated_admin_permission "
                "SET can_reset_password = CASE WHEN can_view_passwords IS NULL THEN can_reset_password ELSE can_view_passwords END "
                "WHERE can_reset_password = FALSE"
            )
        )
        db.session.execute(
            db.text(
                "UPDATE delegated_admin_permission "
                "SET can_view_passwords = FALSE "
                "WHERE can_view_passwords IS NULL"
            )
        )
    db.session.commit()


@app.cli.command("sync-usernames")
def sync_usernames_cmd():
    changed = sync_usernames_with_emails()
    print(f"Synced users: {changed}")


with app.app_context():
    db.create_all()
    try:
        ensure_delegated_permission_columns()
        sync_usernames_with_emails()
    except Exception:
        db.session.rollback()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
