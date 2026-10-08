"""ログイン・権限・個人情報を守るための仕組み。

- 管理者／職員の2つの権限（職員は給与・人事・請求・設定を見られない）
- 一定時間操作がないと自動でログアウト（共用PCの置きっぱなし対策）
- パスワードを続けて間違えるとしばらくロック
- 管理者が作った・再設定したパスワードは、本人が最初のログインで必ず変更
- 退職者などのアカウントは「停止」にできる
- ログイン・閲覧・登録・変更・削除・Excel出力を操作の記録に残す
- 職員のExcel出力は管理者が許可したときだけ
"""

import functools
import re
import secrets
import time
from datetime import datetime, timedelta

from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_db, get_setting, now

bp = Blueprint("auth", __name__)

PUBLIC = {"auth.login", "auth.setup", "static"}
# パスワード変更が必要な人でも開ける画面
WHILE_MUST_CHANGE = {"auth.my_password", "auth.logout", "static"}
MAX_FAILS = 5
LOCK_MINUTES = 15
ROLES = {"admin": "管理者", "staff": "職員"}


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
    try:
        return max(int(get_setting("session_timeout_min", "30") or 30), 1) * 60
    except ValueError:
        return 30 * 60


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
                "ROLES": ROLES}


def is_admin():
    return bool(g.get("user")) and g.user["role"] == "admin"


def admin_required(view):
    @functools.wraps(view)
    def wrapped(*a, **kw):
        if not is_admin():
            abort(403)
        return view(*a, **kw)

    return wrapped


def _login(user):
    session.clear()
    session["uid"] = user["id"]
    session["csrf"] = secrets.token_hex(16)
    session["seen"] = time.time()


@bp.route("/setup", methods=["GET", "POST"])
def setup():
    db = get_db()
    if db.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0:
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        pw = request.form.get("password", "")
        problem = "ユーザー名を入力してください。" if not username else password_problem(pw, username)
        if problem:
            flash(problem, "error")
        else:
            db.execute(
                "INSERT INTO users (username, display_name, password_hash, role, active, must_change, created_at, updated_at)"
                " VALUES (?,?,?,?,1,0,?,?)",
                (username, request.form.get("display_name") or username, generate_password_hash(pw), "admin", now(), now()),
            )
            db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('office_name', ?)",
                       (request.form.get("office_name") or "グループホーム",))
            user = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            _login(user)
            g.user = user
            log_event("login", detail="初期設定で管理者を作成")
            db.commit()
            flash("管理者アカウントを作成しました。", "ok")
            return redirect(url_for("views.dashboard"))
    return render_template("setup.html")


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
            nxt = request.args.get("next", "")
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("views.dashboard"))
        if locked:
            flash(f"パスワードを続けて間違えたため、しばらくログインできません。{LOCK_MINUTES}分ほど待つか、管理者にロック解除を頼んでください。", "error")
        else:
            flash("ユーザー名またはパスワードが違います。", "error")
            if user and user["active"]:
                fails = (user["failed_count"] or 0) + 1
                until = (datetime.now() + timedelta(minutes=LOCK_MINUTES)).strftime("%Y-%m-%d %H:%M:%S") if fails >= MAX_FAILS else None
                db.execute("UPDATE users SET failed_count=?, locked_until=? WHERE id=?",
                           (0 if until else fails, until, user["id"]))
        log_event("login_failed", username=username[:50], detail="ロック中" if locked else None)
        db.commit()
    return render_template("login.html")


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
                    "INSERT INTO users (username, display_name, password_hash, role, active, must_change, created_at, updated_at)"
                    " VALUES (?,?,?,?,1,1,?,?)",
                    (username, request.form.get("display_name") or username, generate_password_hash(pw), role, now(), now()),
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
        elif action == "unlock":
            db.execute("UPDATE users SET failed_count=0, locked_until=NULL WHERE id=?", (uid,))
            log_event("user_unlock", "users", uid, target["username"])
            flash(f"「{target['username']}」のロックを解除しました。", "ok")
        db.commit()
        return redirect(url_for("auth.users"))
    rows = db.execute("SELECT * FROM users ORDER BY active DESC, role, id").fetchall()
    return render_template("users.html", rows=rows, now=now(), MAX_FAILS=MAX_FAILS)


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
    return render_template("password.html", must=bool(g.user["must_change"]))
