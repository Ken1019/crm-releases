"""同期の設定の画面と、同期中の書きこみの制限・お知らせ"""

from flask import Blueprint, Response, current_app, flash, g, redirect, render_template, request, url_for

from . import sync as sy
from .auth import admin_required, log_event
from .db import get_db, get_setting

bp = Blueprint("sync", __name__, url_prefix="/sync")


def _db_path():
    return current_app.config["DATABASE"]


def install(app):
    @app.before_request
    def sync_guard():
        """同期を使っているとき：このPCで直してはいけないもの（相手の表・本部操作中）は保存させない"""
        if request.method != "POST":
            return None
        ok, msg = sy.can_write(app.config["DATABASE"], request.endpoint, request.view_args)
        if ok:
            return None
        flash(msg, "error")
        # 同じ画面を開き直す（保存はしない）。GET で開けない画面はホームへ
        adapter = app.url_map.bind("")
        try:
            adapter.match(request.path, method="GET")
            return redirect(request.full_path.rstrip("?"))
        except Exception:  # noqa: BLE001
            return redirect(url_for("views.dashboard"))

    @app.context_processor
    def sync_banner():
        if not g.get("user"):
            return {"sync_status": None}
        return {"sync_status": sy.status(app.config["DATABASE"])}


def _test_connection(db_path, cfg):
    store = sy.store_for(db_path, cfg)
    if isinstance(store, sy.Firestore):
        store.token()
    store.get("ctl_hq")


@bp.route("/", methods=["GET", "POST"], strict_slashes=False)
@admin_required
def index():
    db_path = _db_path()
    cfg = sy.load_cfg(db_path)
    if request.method == "POST":
        action = request.form.get("action", "")
        user = g.user["username"]
        if action in ("start", "join") and not cfg:
            errs = []
            if action == "start":
                # はじめに設定するPC（ふつうは本部）：暗号の鍵を作る
                role = request.form.get("role", sy.HQ)
                new = {"role": role, "site": request.form.get("site", "").strip(),
                       "api_key": request.form.get("api_key", "").strip(), "project": request.form.get("project", "").strip(),
                       "email": request.form.get("email", "").strip(), "password": request.form.get("password", ""),
                       "key": sy.new_key(), "origin": True}
                if role not in (sy.HOME, sy.HQ):
                    errs.append("このPCの役を選んでください")
                if not sy.SITE_RE.match(new["site"]):
                    errs.append("事業所のID は半角の英字・数字・-・_ で3〜40文字にしてください")
                if not all(new[k] for k in ("api_key", "project", "email", "password")):
                    errs.append("Firebase の4つの項目をすべて入れてください")
            else:
                # 参加するPC（ふつうはグループホーム）：本部で保存した接続ファイルを選ぶだけ
                text = request.form.get("connect", "")
                up = request.files.get("connect_file")
                if up and up.filename:
                    text = up.read(8192).decode("utf-8", errors="replace")
                new = sy.parse_connect(text)
                if new is None:
                    errs.append("接続ファイルが読めません（本部のPCで保存した ghms-connect.txt を選んでください）")
                else:
                    new["origin"] = False
            if not errs:
                try:
                    _test_connection(db_path, new)
                except sy.SyncError as e:
                    errs.append(str(e))
            if errs:
                flash("同期を始められません：" + "／".join(errs), "error")
                return redirect(url_for("sync.index"))
            role = new["role"]
            sy.save_cfg(db_path, new)
            sy.save_state(db_path, {})
            log_event("settings", detail=f"同期を{'始めた' if action == 'start' else '参加した'}（{sy.ROLE_LABEL[role]}・{new['site']}）")
            get_db().commit()
            st = sy.run_once(db_path)
            if st.get("error"):
                flash(f"同期を始めましたが、まちがいがありました：{st['error']}", "error")
            elif action == "start":
                flash(f"このPCを{sy.ROLE_LABEL[role]}として始めました。次に「接続ファイルを保存」して、"
                      f"{sy.ROLE_LABEL[sy.OTHER[role]]}のPCで選んでください。", "ok")
            else:
                flash(f"このPCを{sy.ROLE_LABEL[role]}として参加させました。{sy.ROLE_LABEL[sy.OTHER[role]]}のデータを受け取っています"
                      "（1〜2分）。受け取ったら、もう一度ログインしてください。", "ok")
        elif not cfg:
            return redirect(url_for("sync.index"))
        elif action == "now":
            st = sy.run_once(db_path)
            flash(f"同期できませんでした：{st['error']}" if st.get("error") else "同期しました。", "error" if st.get("error") else "ok")
        elif action == "lock_on" and cfg["role"] == sy.HQ:
            force = bool(request.form.get("force"))
            st = sy.lock_on(db_path, user, force=force)
            log_event("sync_lock", detail="本部操作をオン" + ("（強制）" if force else ""))
            get_db().commit()
            ready = (st.get("lock") or {}).get("ready")
            flash("本部操作をオンにしました。入力も直せます。" if ready else
                  "グループホームのPCに本部操作をお願いしました。返事がくると（ふつう1分ほど）入力も直せるようになります。", "ok")
        elif action == "lock_off" and cfg["role"] == sy.HQ:
            sy.lock_off(db_path)
            log_event("sync_lock", detail="本部操作をオフ")
            get_db().commit()
            flash("本部操作をオフにしました。グループホームのPCが受け取ると、また入力できるようになります。", "ok")
        elif action == "clear_note":
            st = sy.load_state(db_path)
            st.pop("forced_note", None)
            sy.save_state(db_path, st)
        elif action == "stop" and request.form.get("word", "").strip() == "やめる":
            import os

            for p in (sy.cfg_path(db_path), sy.state_path(db_path)):
                if os.path.exists(p):
                    os.remove(p)
            log_event("settings", detail="同期をやめた")
            get_db().commit()
            flash("同期をやめました（このPCのデータはそのままです）。", "ok")
        return redirect(url_for("sync.index"))
    return render_template("sync.html", cfg=cfg, s=sy.status(db_path), HOME=sy.HOME, HQ=sy.HQ,
                           default_site=(get_setting("office_no", "") or "").strip() or "office1")


@bp.route("/connect.txt")
@admin_required
def connect_file():
    """参加するPCに渡す接続ファイル（はじめに設定したPCだけ。同期用のパスワードと暗号の鍵が入っている）"""
    cfg = sy.load_cfg(_db_path())
    if not cfg or not sy.is_origin(cfg):
        return redirect(url_for("sync.index"))
    log_event("export", detail="同期の接続ファイル")
    get_db().commit()
    return Response(sy.connect_file_text(cfg), mimetype="text/plain",
                    headers={"Content-Disposition": "attachment; filename=ghms-connect.txt"})
