import csv
import io
import json
import os
import secrets
import smtplib
from datetime import date, datetime
from email.message import EmailMessage
from functools import wraps

from flask import Flask, flash, jsonify, redirect, render_template, request, send_file, session, url_for
from flask_sqlalchemy import SQLAlchemy
from openpyxl import Workbook, load_workbook
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
    if is_holiday or wd == 6:
        pct60 = 0.0
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


def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    return User.query.get(uid)


def ensure_user_or_redirect():
    user = current_user()
    if user is None:
        session.clear()
        return None
    return user


def get_or_create_profile(user_id: int):
    p = UserProfile.query.filter_by(user_id=user_id).first()
    if p:
        return p
    p = UserProfile(user_id=user_id)
    db.session.add(p)
    db.session.commit()
    return p


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
    return {"fmt_num": fmt_num, "apk_url": app.config.get("APK_URL", "/download-apk")}


@app.after_request
def apply_security_headers(resp):
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    resp.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'"
    return resp


@app.get("/")
def root():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown")
        if is_rate_limited(f"register:{ip}", limit=10, window_sec=60):
            flash("Çok fazla deneme. Lütfen 1 dakika sonra tekrar deneyin.", "error")
            return render_template("register.html")
        username = request.form.get("username", "").strip().lower()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        if len(username) < 3:
            flash("Kullanıcı adı en az 3 karakter olmalı.", "error")
            return render_template("register.html")
        if "@" not in email:
            flash("Geçerli bir e-posta girin.", "error")
            return render_template("register.html")
        if len(password) < 6:
            flash("Şifre en az 6 karakter olmalı.", "error")
            return render_template("register.html")
        if password != confirm:
            flash("Şifreler eşleşmiyor.", "error")
            return render_template("register.html")
        if User.query.filter((User.username == username) | (User.email == email)).first():
            flash("Bu kullanıcı adı veya e-posta zaten kayıtlı.", "error")
            return render_template("register.html")
        user = User(username=username, email=email, password_hash=generate_password_hash(password))
        db.session.add(user)
        db.session.commit()
        get_or_create_profile(user.id)
        flash("Kayıt başarılı. Giriş yapabilirsiniz.", "success")
        return redirect(url_for("login"))
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown")
        if is_rate_limited(f"login:{ip}", limit=15, window_sec=60):
            flash("Çok fazla deneme. Lütfen 1 dakika sonra tekrar deneyin.", "error")
            return render_template("login.html")
        identity = request.form.get("username_or_email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter((User.username == identity) | (User.email == identity)).first()
        if not user or not check_password_hash(user.password_hash, password):
            flash("Kullanıcı adı/e-posta veya şifre hatalı.", "error")
            return render_template("login.html")
        session["user_id"] = user.id
        session["api_token"] = token_serializer.dumps({"uid": user.id, "nonce": secrets.token_hex(8)})
        return redirect(url_for("dashboard"))
    return render_template("login.html")


@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


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
        email = request.form.get("email", "").strip().lower()
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
            db.session.commit()
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
    if f is None or f.filename == "":
        flash("İçe aktarma için dosya seçin.", "error")
        return redirect(url_for("reports"))
    try:
        raw = f.read()
        if not raw:
            flash("Dosya boş.", "error")
            return redirect(url_for("reports"))
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
    return redirect(url_for("reports"))


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

    template_path = os.path.join(os.path.dirname(__file__), "..", "app", "src", "main", "assets", "sablon.xlsx")
    if os.path.exists(template_path):
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
    else:
        wb = Workbook()
        ws = wb.active
        ws.title = "Mesai"
        ws.append(["Tarih", "Gun", "Baslama", "Bitis", "%60", "%15", "Pazar", "Bayram", "Aciklama"])
        for r in rows:
            ws.append([
                format_dmy(r["work_date"]),
                weekday_tr(r["work_date"]),
                r["start_time"],
                r["end_time"],
                r["pct60"],
                r["pct15"],
                r["pazar"],
                r["bayram"],
                r["description"],
            ])
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
    apk_path = os.path.join(os.path.dirname(__file__), "..", "app", "build", "outputs", "apk", "debug", "app-debug.apk")
    if os.path.exists(apk_path):
        return send_file(apk_path, as_attachment=True, download_name="MesaiApp.apk")
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
    identity = str(data.get("usernameOrEmail", "")).strip().lower()
    password = str(data.get("password", ""))
    user = User.query.filter((User.username == identity) | (User.email == identity)).first()
    if not user or not check_password_hash(user.password_hash, password):
        return jsonify({"error": "invalid_credentials"}), 401
    token = token_serializer.dumps({"uid": user.id, "nonce": secrets.token_hex(8)})
    return jsonify({"token": token, "user": {"id": user.id, "username": user.username}})


@app.post("/api/register")
def api_register():
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown")
    if is_rate_limited(f"api_register:{ip}", limit=10, window_sec=60):
        return jsonify({"error": "rate_limited"}), 429
    data = request.get_json(silent=True) or {}
    username = str(data.get("username", "")).strip().lower()
    email = str(data.get("email", "")).strip().lower()
    password = str(data.get("password", ""))
    if len(username) < 3:
        return jsonify({"error": "invalid_username"}), 400
    if "@" not in email:
        return jsonify({"error": "invalid_email"}), 400
    if len(password) < 6:
        return jsonify({"error": "invalid_password"}), 400
    if User.query.filter((User.username == username) | (User.email == email)).first():
        return jsonify({"error": "already_exists"}), 409
    user = User(username=username, email=email, password_hash=generate_password_hash(password))
    db.session.add(user)
    db.session.commit()
    get_or_create_profile(user.id)
    token = token_serializer.dumps({"uid": user.id, "nonce": secrets.token_hex(8)})
    return jsonify({"token": token, "user": {"id": user.id, "username": user.username}}), 201


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
        entry = OvertimeEntry(
            user_id=user.id,
            work_date=parse_date(str(data.get("workDate", ""))),
            start_time=str(data.get("startTime", "")),
            end_time=str(data.get("endTime", "")),
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


with app.app_context():
    db.create_all()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
