"""ログイン・権限・個人情報を守るための仕組み。

- 管理者／職員の2つの権限（職員は給与・人事・請求・設定を見られない）
- 一定時間操作がないと自動でログアウト（共用PCの置きっぱなし対策）
- パスワードを続けて間違えるとしばらくロック
- 管理者が作った・再設定したパスワードは、本人が最初のログインで必ず変更
- 退職者などのアカウントは「停止」にできる
- ログイン・閲覧・登録・変更・削除・Excel出力を操作の記録に残す
- 職員のExcel出力は管理者が許可したときだけ
- 管理者が登録した事業所の端末では、名前をえらんで4桁のPINで交代ログインできる
  （PINで入った管理者は、管理者の画面を開くときにパスワードを聞かれる）
"""

import functools
import hashlib
import re
import secrets
import time
from datetime import datetime, timedelta

from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_db, get_setting, now

bp = Blueprint("auth", __name__)

PUBLIC = {"auth.login", "auth.setup", "auth.pin_login", "static", "system.ping", "system.shutdown", "work.kiosk"}
# パスワード変更が必要な人でも開ける画面
WHILE_MUST_CHANGE = {"auth.my_password", "auth.logout", "static"}
MAX_FAILS = 5
LOCK_MINUTES = 15
ROLES = {"admin": "管理者", "staff": "職員"}
DEVICE_COOKIE = "ghms_device"
DEVICE_DAYS = 400


def _hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


def current_device():
    """この端末が、PINでログインできる端末として登録されていればその行"""
    if "device" not in g:
        token = request.cookies.get(DEVICE_COOKIE)
        g.device = get_db().execute("SELECT * FROM devices WHERE token_hash=? AND active=1",
                                    (_hash_token(token),)).fetchone() if token else None
    return g.device


def pin_problem(pin):
    if not re.fullmatch(r"[0-9]{4}", pin or ""):
        return "PINは4桁の数字にしてください。"
    if len(set(pin)) == 1 or pin in "0123456789" or pin in "9876543210" or pin[:2] == pin[2:]:
        return "「1111」「1234」「1212」のような推測されやすいPINは使えません。"
    return None


def log_event(action, entity=None, record_id=None, detail=None, username=None):
    """操作の記録（ログイン失敗など、ログイン前でも書けるように username を直接受け取れる）"""
    if username is None:
        username = g.user["username"] if g.get("user") else ""
    get_db().execute(
        "INSERT INTO audit_log (at, username, action, entity, record_id, detail, ip) VALUES (?,?,?,?,?,?,?)",
        (now(), username, action, entity, record_id, detail, request.remote_addr if request else None),
    )


def password_problem(pw, username=""):
    """パスワードの決まり。問題があればその説明、なければ None"""
    if len(pw) < 8:
        return "パスワードは8文字以上にしてください。"
    if not re.search(r"[A-Za-z]", pw) or not re.search(r"[0-9]", pw):
        return "パスワードには英字と数字の両方を入れてください。"
    if username and pw.lower() == username.lower():
        return "ユーザー名と同じパスワードは使えません。"
    return None


def timeout_seconds():
    key, default = ("pin_timeout_min", 15) if session.get("via") == "pin" else ("session_timeout_min", 30)
    try:
        return max(int(get_setting(key, str(default)) or default), 1) * 60
    except ValueError:
        return default * 60


def can_export():
    return is_admin() or get_setting("staff_can_export", "0") == "1"


def install(app):
    @app.before_request
    def load_user():
        g.user = None
        uid = session.get("uid")
        if uid:
            user = get_db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
            seen = session.get("seen", 0)
            if user is None or not user["active"]:
                session.clear()
            elif time.time() - seen > timeout_seconds():
                session.clear()
                if request.endpoint not in PUBLIC:
                    flash("しばらく操作がなかったため、安全のためログアウトしました。もう一度ログインしてください。", "error")
            else:
                g.user = user
                session["seen"] = time.time()
        if request.endpoint in PUBLIC:
            return None
        if get_db().execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            return redirect(url_for("auth.setup"))
        if g.user is None:
            return redirect(url_for("auth.login", next=request.path))
        if g.user["must_change"] and request.endpoint not in WHILE_MUST_CHANGE:
            return redirect(url_for("auth.my_password"))
        # CSRF対策: 変更系リクエストはセッションのトークンと照合
        if request.method == "POST" and request.form.get("_csrf") != session.get("csrf"):
            abort(400, "画面の有効期限が切れました。もう一度開き直してください。")
        # Excel出力・バックアップは許可された人だけ
        if (request.path.endswith(".xlsx") or request.endpoint == "views.backup") and not can_export():
            abort(403)
        return None

    @app.after_request
    def protect(resp):
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        if g.get("user"):
            # ログアウト後に「戻る」で個人情報が表示されないように
            resp.headers["Cache-Control"] = "no-store"
            if "attachment" in resp.headers.get("Content-Disposition", "") and resp.status_code == 200:
                log_event("export", detail=request.full_path.rstrip("?"))
                get_db().commit()
        return resp

    @app.context_processor
    def csrf():
        if "csrf" not in session:
            session["csrf"] = secrets.token_hex(16)
        return {"csrf_token": session["csrf"], "is_admin": is_admin(), "can_export": can_export() if g.get("user") else False,
                "ROLES": ROLES, "this_device": current_device(), "via_pin": session.get("via") == "pin"}


def is_admin():
    return bool(g.get("user")) and g.user["role"] == "admin"


def require_admin():
    """管理者でなければ403。PINで入った管理者はパスワードの確認へ"""
    if not is_admin():
        abort(403)
    if session.get("via") == "pin":
        abort(redirect(url_for("auth.reauth", next=request.full_path.rstrip("?"))))


def admin_required(view):
    @functools.wraps(view)
    def wrapped(*a, **kw):
        require_admin()
        return view(*a, **kw)

    return wrapped


def _login(user, via="password"):
    session.clear()
    session["uid"] = user["id"]
    session["csrf"] = secrets.token_hex(16)
    session["seen"] = time.time()
    session["via"] = via


def _safe_next(default="views.dashboard"):
    nxt = request.args.get("next", "")
    return nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for(default)


@bp.route("/setup", methods=["GET", "POST"])
def setup():
    """はじめての設定：事業所・類型・住居・使う機能・管理者をまとめて入力し、それに合わせて整える"""
    from . import runtime, setup_wizard
    from .db import set_setting
    from .entities import HOME_TYPE

    db = get_db()
    if db.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0:
        return redirect(url_for("auth.login"))
    values = setup_wizard.prefill(runtime.load_office())
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        pw = request.form.get("password", "")
        errors = setup_wizard.validate(request.form)
        problem = "管理者のユーザー名を入れてください。" if not username else password_problem(pw, username)
        if problem:
            errors.append(problem)
        if errors:
            for e in errors:
                flash(e, "error")
            values = request.form.to_dict()
            values["home_types"] = request.form.getlist("home_types")
            values["features"] = set(request.form.getlist("features"))
        else:
            db.execute(
                "INSERT INTO users (username, display_name, password_hash, role, active, must_change, created_at, updated_at)"
                " VALUES (?,?,?,?,1,0,?,?)",
                (username, request.form.get("display_name") or username, generate_password_hash(pw), "admin", now(), now()),
            )
            done = setup_wizard.apply(db, request.form, set_setting)
            user = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            _login(user)
            g.user = user
            log_event("login", detail="はじめての設定で管理者を作成")
            log_event("settings", detail="はじめての設定：" + "／".join(done))
            db.commit()
            flash("はじめての設定が終わりました。" + "。".join(done) + "。", "ok")
            return redirect(url_for("views.dashboard"))
    from .customize import FEATURES

    return render_template("setup.html", v=values, HOME_TYPE=HOME_TYPE, FEATURES=FEATURES, homes=range(1, setup_wizard.MAX_HOMES + 1))


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        db = get_db()
        username = request.form.get("username", "").strip()
        user = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        locked = user and user["locked_until"] and user["locked_until"] > now()
        if user and user["active"] and not locked and check_password_hash(user["password_hash"], request.form.get("password", "")):
            db.execute("UPDATE users SET failed_count=0, locked_until=NULL, last_login=? WHERE id=?", (now(), user["id"]))
            _login(user)
            g.user = user
            log_event("login")
            db.commit()
            return redirect(_safe_next())
        if locked:
            flash(f"パスワードを続けて間違えたため、しばらくログインできません。{LOCK_MINUTES}分ほど待つか、管理者にロック解除を頼んでください。", "error")
        else:
            flash("ユーザー名またはパスワードが違います。", "error")
        if user and user["active"] and not locked:
            _fail(user, None)
        else:
            log_event("login_failed", username=username[:50], detail="ロック中" if locked else None)
        db.commit()
    device = current_device()
    people = get_db().execute("SELECT id, display_name, role FROM users WHERE active=1 AND pin_hash IS NOT NULL "
                              "ORDER BY role DESC, display_name").fetchall() if device else []
    return render_template("login.html", device=device, people=people)


def _fail(user, detail):
    fails = (user["failed_count"] or 0) + 1
    until = (datetime.now() + timedelta(minutes=LOCK_MINUTES)).strftime("%Y-%m-%d %H:%M:%S") if fails >= MAX_FAILS else None
    get_db().execute("UPDATE users SET failed_count=?, locked_until=? WHERE id=?", (0 if until else fails, until, user["id"]))
    log_event("login_failed", username=user["username"], detail=detail)


@bp.route("/pin/<int:uid>", methods=["GET", "POST"])
def pin_login(uid):
    device = current_device()
    if device is None:
        flash("この端末はPINでのログインに登録されていません。ユーザー名とパスワードでログインしてください。", "error")
        return redirect(url_for("auth.login"))
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id=? AND active=1 AND pin_hash IS NOT NULL", (uid,)).fetchone()
    if user is None:
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        locked = user["locked_until"] and user["locked_until"] > now()
        if locked:
            flash(f"続けて間違えたため、しばらくログインできません。{LOCK_MINUTES}分ほど待つか、管理者にロック解除を頼んでください。", "error")
            log_event("login_failed", username=user["username"], detail=f"PIN・ロック中（{device['name']}）")
        elif check_password_hash(user["pin_hash"], request.form.get("pin", "")):
            db.execute("UPDATE users SET failed_count=0, locked_until=NULL, last_login=? WHERE id=?", (now(), uid))
            db.execute("UPDATE devices SET last_used=? WHERE id=?", (now(), device["id"]))
            _login(user, via="pin")
            g.user = user
            log_event("login", detail=f"PIN（{device['name']}）")
            db.commit()
            return redirect(url_for("views.dashboard"))
        else:
            _fail(user, f"PIN（{device['name']}）")
            flash("PINが違います。", "error")
        db.commit()
    return render_template("pin.html", person=user, device=device)


@bp.route("/reauth", methods=["GET", "POST"])
def reauth():
    """PINで入った管理者が、管理者の画面を開く前にパスワードで本人確認"""
    if not is_admin():
        abort(403)
    if request.method == "POST":
        if check_password_hash(g.user["password_hash"], request.form.get("password", "")):
            session["via"] = "password"
            log_event("reauth")
            get_db().commit()
            return redirect(_safe_next())
        _fail(g.user, "管理者画面の確認")
        get_db().commit()
        flash("パスワードが違います。", "error")
    return render_template("reauth.html")


@bp.route("/devices", methods=["POST"])
@admin_required
def devices():
    db = get_db()
    action = request.form.get("action")
    resp = redirect(url_for("auth.users"))
    if action == "register":
        name = request.form.get("name", "").strip() or "名前のない端末"
        token = secrets.token_urlsafe(32)
        cur = db.execute("INSERT INTO devices (name, token_hash, active, created_by, created_at) VALUES (?,?,1,?,?)",
                         (name, _hash_token(token), g.user["username"], now()))
        resp.set_cookie(DEVICE_COOKIE, token, max_age=DEVICE_DAYS * 86400, httponly=True, samesite="Lax")
        log_event("device_add", "devices", cur.lastrowid, name)
        flash(f"この端末を「{name}」としてPIN対応にしました。", "ok")
    elif action == "remove":
        did = request.form.get("id", type=int)
        row = db.execute("SELECT * FROM devices WHERE id=?", (did,)).fetchone()
        if row:
            db.execute("UPDATE devices SET active=0 WHERE id=?", (did,))
            log_event("device_remove", "devices", did, row["name"])
            flash(f"「{row['name']}」をPIN対応から外しました。", "ok")
    db.commit()
    return resp


@bp.route("/my-pin", methods=["POST"])
def my_pin():
    db = get_db()
    if not check_password_hash(g.user["password_hash"], request.form.get("current", "")):
        flash("今のパスワードが違います。", "error")
    elif request.form.get("action") == "clear":
        db.execute("UPDATE users SET pin_hash=NULL WHERE id=?", (g.user["id"],))
        log_event("pin_clear")
        flash("PINを消しました。", "ok")
    else:
        pin = request.form.get("pin", "")
        problem = pin_problem(pin)
        if problem:
            flash(problem, "error")
        elif pin != request.form.get("pin2", ""):
            flash("確認のために入れたPINが一致しません。", "error")
        else:
            db.execute("UPDATE users SET pin_hash=? WHERE id=?", (generate_password_hash(pin), g.user["id"]))
            log_event("pin_set")
            flash("PINを設定しました。登録された事業所の端末で、名前をえらんでPINでログインできます。", "ok")
    db.commit()
    return redirect(url_for("auth.my_password"))


@bp.route("/logout", methods=["POST"])
def logout():
    if g.get("user"):
        log_event("logout")
        get_db().commit()
    session.clear()
    flash("ログアウトしました。", "ok")
    return redirect(url_for("auth.login"))


def _active_admins(exclude=None):
    return get_db().execute("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1 AND id != ?",
                            (exclude or 0,)).fetchone()[0]


@bp.route("/users", methods=["GET", "POST"])
@admin_required
def users():
    db = get_db()
    if request.method == "POST":
        action = request.form.get("action")
        uid = request.form.get("id", type=int)
        target = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone() if uid else None
        if action == "add":
            username = request.form.get("username", "").strip()
            pw = request.form.get("password", "")
            problem = "ユーザー名を入力してください。" if not username else password_problem(pw, username)
            if problem:
                flash(problem, "error")
            elif db.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
                flash("そのユーザー名は既に使われています。", "error")
            else:
                role = "admin" if request.form.get("role") == "admin" else "staff"
                cur = db.execute(
                    "INSERT INTO users (username, display_name, password_hash, role, active, must_change, created_at, updated_at,"
                    " staff_id) VALUES (?,?,?,?,1,1,?,?,?)",
                    (username, request.form.get("display_name") or username, generate_password_hash(pw), role, now(), now(),
                     request.form.get("staff_id", type=int)),
                )
                log_event("user_add", "users", cur.lastrowid, f"{username}（{ROLES[role]}）")
                flash(f"「{username}」を追加しました。最初のログインでパスワードを変えてもらいます。", "ok")
        elif target is None:
            abort(400)
        elif action == "password":
            pw = request.form.get("password", "")
            problem = password_problem(pw, target["username"])
            if problem:
                flash(problem, "error")
            else:
                db.execute("UPDATE users SET password_hash=?, must_change=?, failed_count=0, locked_until=NULL, updated_at=? WHERE id=?",
                           (generate_password_hash(pw), 0 if uid == g.user["id"] else 1, now(), uid))
                log_event("user_password", "users", uid, target["username"])
                flash(f"「{target['username']}」のパスワードを再設定しました。次のログインで本人に変えてもらいます。", "ok")
        elif action == "role":
            role = "admin" if request.form.get("role") == "admin" else "staff"
            if role == "staff" and target["role"] == "admin" and _active_admins(exclude=uid) == 0:
                flash("管理者が1人もいなくなるため変更できません。", "error")
            else:
                db.execute("UPDATE users SET role=?, updated_at=? WHERE id=?", (role, now(), uid))
                log_event("user_role", "users", uid, f"{target['username']} → {ROLES[role]}")
                flash(f"「{target['username']}」の権限を{ROLES[role]}にしました。", "ok")
        elif action in ("disable", "enable"):
            if action == "disable" and (uid == g.user["id"] or (target["role"] == "admin" and _active_admins(exclude=uid) == 0)):
                flash("自分自身や、最後の管理者は停止できません。", "error")
            else:
                db.execute("UPDATE users SET active=?, updated_at=? WHERE id=?", (1 if action == "enable" else 0, now(), uid))
                log_event("user_" + action, "users", uid, target["username"])
                flash(f"「{target['username']}」を{'使えるように' if action == 'enable' else '停止'}しました。", "ok")
        elif action == "clear_pin":
            db.execute("UPDATE users SET pin_hash=NULL WHERE id=?", (uid,))
            log_event("user_pin_clear", "users", uid, target["username"])
            flash(f"「{target['username']}」のPINを消しました。本人が設定し直します。", "ok")
        elif action == "staff":
            db.execute("UPDATE users SET staff_id=?, updated_at=? WHERE id=?", (request.form.get("staff_id", type=int), now(), uid))
            log_event("user_staff", "users", uid, target["username"])
            flash(f"「{target['username']}」を職員の情報とつなぎました。タイムカード・給与明細に使います。", "ok")
        elif action == "unlock":
            db.execute("UPDATE users SET failed_count=0, locked_until=NULL WHERE id=?", (uid,))
            log_event("user_unlock", "users", uid, target["username"])
            flash(f"「{target['username']}」のロックを解除しました。", "ok")
        db.commit()
        return redirect(url_for("auth.users"))
    rows = db.execute("SELECT * FROM users ORDER BY active DESC, role, id").fetchall()
    devs = db.execute("SELECT * FROM devices WHERE active=1 ORDER BY id").fetchall()
    staff = db.execute("SELECT id, name FROM staff WHERE status IS NULL OR status != '退職' ORDER BY kana, name").fetchall()
    return render_template("users.html", rows=rows, now=now(), MAX_FAILS=MAX_FAILS, devs=devs, staff=staff)


@bp.route("/password", methods=["GET", "POST"])
def my_password():
    if request.method == "POST":
        pw = request.form.get("password", "")
        problem = password_problem(pw, g.user["username"])
        if not check_password_hash(g.user["password_hash"], request.form.get("current", "")):
            flash("今のパスワードが違います。", "error")
        elif problem:
            flash(problem, "error")
        elif pw != request.form.get("password2", ""):
            flash("確認のために入れた新しいパスワードが一致しません。", "error")
        elif check_password_hash(g.user["password_hash"], pw):
            flash("今と違うパスワードにしてください。", "error")
        else:
            db = get_db()
            db.execute("UPDATE users SET password_hash=?, must_change=0, updated_at=? WHERE id=?",
                       (generate_password_hash(pw), now(), g.user["id"]))
            log_event("password_change")
            db.commit()
            flash("パスワードを変更しました。", "ok")
            return redirect(url_for("views.dashboard"))
    return render_template("password.html", must=bool(g.user["must_change"]), has_pin=bool(g.user["pin_hash"]))
