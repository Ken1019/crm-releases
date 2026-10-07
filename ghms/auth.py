import functools
import secrets

from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_db, now

bp = Blueprint("auth", __name__)

PUBLIC = {"auth.login", "auth.setup", "static"}


def install(app):
    @app.before_request
    def load_user():
        g.user = None
        uid = session.get("uid")
        if uid:
            g.user = get_db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if request.endpoint in PUBLIC:
            return None
        if get_db().execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            return redirect(url_for("auth.setup"))
        if g.user is None:
            return redirect(url_for("auth.login", next=request.path))
        # CSRF対策: 変更系リクエストはセッションのトークンと照合
        if request.method == "POST" and request.form.get("_csrf") != session.get("csrf"):
            abort(400, "画面の有効期限が切れました。もう一度開き直してください。")
        return None

    @app.context_processor
    def csrf():
        if "csrf" not in session:
            session["csrf"] = secrets.token_hex(16)
        return {"csrf_token": session["csrf"], "is_admin": is_admin()}


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


@bp.route("/setup", methods=["GET", "POST"])
def setup():
    db = get_db()
    if db.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0:
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        pw = request.form.get("password", "")
        if not username or len(pw) < 8:
            flash("ユーザー名と8文字以上のパスワードを入力してください。", "error")
        else:
            db.execute(
                "INSERT INTO users (username, display_name, password_hash, role, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?)",
                (username, request.form.get("display_name") or username, generate_password_hash(pw), "admin", now(), now()),
            )
            db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('office_name', ?)",
                       (request.form.get("office_name") or "グループホーム",))
            db.commit()
            _login(db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone())
            flash("管理者アカウントを作成しました。", "ok")
            return redirect(url_for("views.dashboard"))
    return render_template("setup.html")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = get_db().execute("SELECT * FROM users WHERE username=?", (request.form.get("username", "").strip(),)).fetchone()
        if user and check_password_hash(user["password_hash"], request.form.get("password", "")):
            _login(user)
            nxt = request.args.get("next", "")
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("views.dashboard"))
        flash("ユーザー名またはパスワードが違います。", "error")
    return render_template("login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


@bp.route("/users", methods=["GET", "POST"])
@admin_required
def users():
    db = get_db()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            username = request.form.get("username", "").strip()
            pw = request.form.get("password", "")
            if not username or len(pw) < 8:
                flash("ユーザー名と8文字以上のパスワードを入力してください。", "error")
            elif db.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
                flash("そのユーザー名は既に使われています。", "error")
            else:
                role = "admin" if request.form.get("role") == "admin" else "staff"
                db.execute(
                    "INSERT INTO users (username, display_name, password_hash, role, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,?)",
                    (username, request.form.get("display_name") or username, generate_password_hash(pw), role, now(), now()),
                )
                db.commit()
                flash("ユーザーを追加しました。", "ok")
        elif action == "password":
            pw = request.form.get("password", "")
            if len(pw) < 8:
                flash("パスワードは8文字以上にしてください。", "error")
            else:
                db.execute("UPDATE users SET password_hash=?, updated_at=? WHERE id=?",
                           (generate_password_hash(pw), now(), request.form.get("id")))
                db.commit()
                flash("パスワードを変更しました。", "ok")
        elif action == "delete":
            uid = int(request.form.get("id", 0))
            if uid == g.user["id"]:
                flash("自分自身は削除できません。", "error")
            else:
                db.execute("DELETE FROM users WHERE id=?", (uid,))
                db.commit()
                flash("ユーザーを削除しました。", "ok")
        return redirect(url_for("auth.users"))
    rows = db.execute("SELECT * FROM users ORDER BY id").fetchall()
    return render_template("users.html", rows=rows)


@bp.route("/password", methods=["GET", "POST"])
def my_password():
    if request.method == "POST":
        if not check_password_hash(g.user["password_hash"], request.form.get("current", "")):
            flash("現在のパスワードが違います。", "error")
        elif len(request.form.get("password", "")) < 8:
            flash("新しいパスワードは8文字以上にしてください。", "error")
        else:
            db = get_db()
            db.execute("UPDATE users SET password_hash=?, updated_at=? WHERE id=?",
                       (generate_password_hash(request.form["password"]), now(), g.user["id"]))
            db.commit()
            flash("パスワードを変更しました。", "ok")
            return redirect(url_for("views.dashboard"))
    return render_template("password.html")
