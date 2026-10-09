"""バックアップの画面：状態・今すぐバックアップ・2か所目の設定・ダウンロード・復元"""

import io
import os
import sqlite3
from datetime import datetime

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_file, url_for

from . import backup as bk
from .auth import admin_required, can_export, log_event
from .db import get_db, get_setting, set_setting

bp = Blueprint("backups", __name__, url_prefix="/backup")
RESTORE_WORD = "復元する"


def _db_path():
    return current_app.config["DATABASE"]


@bp.route("/", methods=["GET", "POST"], strict_slashes=False)
@admin_required
def index():
    db_path = _db_path()
    dest = get_setting("backup_dir2", "")
    if request.method == "POST":
        action = request.form.get("action")
        if action == "now":
            try:
                out, st = bk.backup_now(db_path)
            except Exception as e:  # noqa: BLE001
                flash(f"バックアップできませんでした：{e}", "error")
            else:
                msg = f"バックアップしました（{os.path.basename(out)}）。"
                s2 = st.get("dest2") or {}
                if dest and s2.get("error"):
                    flash(msg + f"2か所目には保存できませんでした：{s2['error']}", "error")
                else:
                    flash(msg + ("2か所目にも保存しました。" if dest else ""), "ok")
                log_event("backup", detail=os.path.basename(out))
                get_db().commit()
        elif action == "dest2":
            path = request.form.get("dest2", "").strip().strip('"')
            ok, why = bk.check_dest2(path, db_path)
            if not ok:
                flash(f"保存先にできません：{why}", "error")
            else:
                set_setting("backup_dir2", path)
                log_event("settings", detail=f"バックアップの2か所目：{path or '（なし）'}")
                get_db().commit()
                if path:
                    st = bk.run_auto(db_path)
                    err = (st.get("dest2") or {}).get("error")
                    if err:
                        flash(f"保存先を設定しましたが、コピーできませんでした：{err}", "error")
                    else:
                        flash(f"保存先を設定し、バックアップをコピーしました：{bk.dest2_root(path)}", "ok")
                else:
                    flash("2か所目の保存をやめました。", "ok")
        return redirect(url_for("backups.index"))
    st = bk.read_status(db_path)
    return render_template("backup.html", st=st, dest=dest, dest_root=bk.dest2_root(dest) if dest else "",
                           backups=bk.list_backups(db_path, dest)[:80], data_dir=os.path.dirname(db_path),
                           local_root=bk.local_root(db_path), health=bk.health(db_path, dest), KEEP=bk.KEEP,
                           KINDS=bk.KINDS, word=RESTORE_WORD)


@bp.route("/download")
@admin_required
def download():
    """いまのデータをファイルでダウンロード（Excelの出力ができる人だけ）"""
    if not can_export():
        abort(403)
    src = sqlite3.connect(_db_path())
    mem = sqlite3.connect(":memory:")
    src.backup(mem)
    src.close()
    data = mem.serialize()
    mem.close()
    name = f"ghms_backup_{datetime.now():%Y%m%d_%H%M}.sqlite3"
    return send_file(io.BytesIO(data), mimetype="application/octet-stream", as_attachment=True, download_name=name)


@bp.route("/restore", methods=["POST"])
@admin_required
def restore():
    """バックアップから元にもどす。一覧から選ぶか、ファイル（別のPCのバックアップなど）を選ぶ"""
    db_path = _db_path()
    if request.form.get("word", "").strip() != RESTORE_WORD:
        flash(f"元にもどすときは、確認のため「{RESTORE_WORD}」と入れてください。", "error")
        return redirect(url_for("backups.index"))
    get_db().commit()  # この画面の接続で書きかけのものがあると、置きかえを待ってしまうため
    tmp = None
    up = request.files.get("file")
    try:
        if up and up.filename:
            tmp = bk.upload_path(db_path)
            up.save(tmp)
            src, label = tmp, f"ファイル {os.path.basename(up.filename)[:80]}"
        else:
            src = bk.resolve(db_path, get_setting("backup_dir2", ""), request.form.get("id", ""))
            if not src:
                flash("元にもどすバックアップを選んでください。", "error")
                return redirect(url_for("backups.index"))
            label = os.path.basename(src)
        try:
            saved = bk.restore(db_path, src)
        except ValueError as e:
            flash(f"元にもどせませんでした：{e}", "error")
            return redirect(url_for("backups.index"))
    finally:
        if tmp and os.path.exists(tmp):
            os.remove(tmp)
    # 復元したデータの操作記録に残す（前のデータは before_restore に残っている）
    db = get_db()
    db.rollback()
    log_event("restore", detail=f"{label}（前のデータは {os.path.basename(saved)}）")
    db.commit()
    flash(f"バックアップ（{label}）から元にもどしました。もう一度ログインしてください。"
          f"元にもどす前のデータは「{bk.KINDS['before_restore']}」に残しています。", "ok")
    return redirect(url_for("auth.login"))
