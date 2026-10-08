"""ネット経由の更新。

公開している更新情報（latest.json）を見て新しい版があるか調べ、管理者が「更新する」を押すと
インストーラーをダウンロード → SHA-256 で改ざんがないか確認 → データをバックアップ → インストーラーを起動する。

latest.json の形：
    {"version": "1.0.1", "installer": "https://.../GHMS-Setup-1.0.1.exe", "sha256": "...", "notes": "...", "date": "..."}
"""

import hashlib
import hmac
import json
import os
import re
import shutil
import sqlite3
import sys
import threading
import time
import urllib.request
from datetime import datetime

from flask import Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request, url_for

from . import VERSION, runtime
from .auth import admin_required, log_event
from .db import get_db, get_setting, now, set_setting

bp = Blueprint("system", __name__)

DEFAULT_URL = "https://github.com/Ken1019/crm-releases/releases/latest/download/latest.json"
CHECK_EVERY = 24 * 3600
TIMEOUT = 15


def parse_version(v):
    nums = re.findall(r"\d+", v or "")
    return tuple(int(n) for n in nums[:3]) + (0,) * (3 - len(nums[:3]))


def is_newer(latest, current=VERSION):
    return parse_version(latest) > parse_version(current)


def update_url():
    """更新情報の場所：設定 → config.ini の [product] update_url → 標準の配布先"""
    return get_setting("update_url", "") or runtime.load_product()["update_url"] or DEFAULT_URL


def fetch_manifest(url, opener=urllib.request.urlopen):
    req = urllib.request.Request(url, headers={"User-Agent": f"GHMS/{VERSION}"})
    with opener(req, timeout=TIMEOUT) as r:
        data = json.loads(r.read().decode("utf-8"))
    if not isinstance(data, dict) or not data.get("version"):
        raise ValueError("更新情報の形が正しくありません")
    return data


def check(opener=urllib.request.urlopen):
    """更新を確認して結果を保存する。戻り値は (manifest または None, エラー文 または None)"""
    try:
        m = fetch_manifest(update_url(), opener)
    except Exception as e:  # ネットにつながらない・まだ公開していない など
        set_setting("update_checked_at", now())
        set_setting("update_error", str(e)[:200])
        return None, "更新情報を取得できませんでした（インターネットの接続を確認してください）。"
    set_setting("update_checked_at", now())
    set_setting("update_error", "")
    set_setting("update_latest", json.dumps(m, ensure_ascii=False))
    return m, None


def cached_latest():
    try:
        m = json.loads(get_setting("update_latest", "") or "null")
    except ValueError:
        return None
    return m if m and is_newer(m.get("version")) else None


def maybe_check_in_background(app):
    """1日1回、裏で更新を確認（画面の表示は待たせない）"""
    if app.testing or get_setting("update_auto_check", "1") != "1":
        return
    last = get_setting("update_checked_at", "")
    try:
        if last and (datetime.now() - datetime.strptime(last, "%Y-%m-%d %H:%M:%S")).total_seconds() < CHECK_EVERY:
            return
    except ValueError:
        pass
    set_setting("update_checked_at", now())  # 同時に何度も確認しないよう先に印をつける

    def run():
        with app.app_context():
            check()

    threading.Thread(target=run, daemon=True).start()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download_installer(m, dest_dir, opener=urllib.request.urlopen):
    url = m.get("installer", "")
    if not url.startswith("https://"):
        raise ValueError("インストーラーの場所が https ではありません")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", m.get("sha256", "")):
        raise ValueError("更新情報に確認用の値（SHA-256）がありません")
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, f"GHMS-Setup-{re.sub(r'[^0-9.]', '', m['version'])}.exe")
    req = urllib.request.Request(url, headers={"User-Agent": f"GHMS/{VERSION}"})
    with opener(req, timeout=120) as r, open(path + ".part", "wb") as f:
        shutil.copyfileobj(r, f)
    if sha256_file(path + ".part").lower() != m["sha256"].lower():
        os.remove(path + ".part")
        raise ValueError("ダウンロードしたファイルが正しくありません（改ざん・破損のおそれ）。更新を中止しました。")
    os.replace(path + ".part", path)
    return path


def backup_before_update(db_path, version):
    dest = os.path.join(os.path.dirname(db_path), "backup")
    os.makedirs(dest, exist_ok=True)
    out = os.path.join(dest, f"before_update_{VERSION}_to_{version}_{datetime.now():%Y%m%d_%H%M%S}.sqlite3")
    src, dst = sqlite3.connect(db_path), sqlite3.connect(out)
    src.backup(dst)
    dst.close()
    src.close()
    return out


def is_local_request():
    return request.remote_addr in ("127.0.0.1", "::1")


def launch_installer(path):
    """インストーラーを起動（Windowsの確認画面が出る）。インストーラーが古い版を止めて入れ替え、起動し直す"""
    if sys.platform != "win32":
        raise RuntimeError("Windows でのみ更新できます")
    os.startfile(path, "open", "/SILENT /SUPPRESSMSGBOXES /NORESTART /SP-")  # noqa: S606


# ---------------------------------------------------------------- 画面
@bp.route("/update", methods=["GET", "POST"])
@admin_required
def update():
    installed = runtime.is_installed()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "check":
            m, err = check()
            log_event("update_check", detail=(m or {}).get("version") or err)
            get_db().commit()
            if err:
                flash(err, "error")
            elif is_newer(m["version"]):
                flash(f"新しい版 {m['version']} があります。", "ok")
            else:
                flash("最新の版を使っています。", "ok")
        elif action == "auto":
            set_setting("update_auto_check", "1" if request.form.get("on") else "0")
            flash("設定を保存しました。", "ok")
        elif action == "install":
            m = cached_latest()
            if m is None:
                flash("更新できる新しい版がありません。先に「更新を確認する」を押してください。", "error")
            elif not installed:
                flash("この版はインストーラーで入れたものではないため、自動では更新できません。", "error")
            elif not is_local_request():
                flash("更新は、システムを動かしているPC（サーバーのPC）の画面から行ってください。", "error")
            else:
                try:
                    path = download_installer(m, os.path.join(runtime.base_dir(), "updates"))
                    backup = backup_before_update(current_app.config["DATABASE"], m["version"])
                    log_event("update_install", detail=f"{VERSION} → {m['version']}（バックアップ {os.path.basename(backup)}）")
                    get_db().commit()
                    launch_installer(path)
                except Exception as e:
                    log_event("update_failed", detail=str(e)[:200])
                    get_db().commit()
                    flash(f"更新できませんでした：{e}", "error")
                else:
                    return render_template("updating.html", version=m["version"])
        return redirect(url_for("system.update"))
    return render_template("update.html", version=VERSION, latest=cached_latest(), installed=installed,
                           checked_at=get_setting("update_checked_at", ""), error=get_setting("update_error", ""),
                           auto=get_setting("update_auto_check", "1") == "1", local=is_local_request(),
                           data_dir=runtime.data_dir())


@bp.route("/__ping")
def ping():
    return jsonify(ok=True, version=VERSION)


@bp.route("/__shutdown", methods=["POST"])
def shutdown():
    """このPCの中からだけ、合い言葉つきで止められる（インストーラー・停止アイコン用）"""
    if not is_local_request():
        abort(403)
    try:
        with open(runtime.token_path(), encoding="utf-8") as f:
            token = f.read().strip()
    except OSError:
        abort(403)
    if not token or not hmac.compare_digest(token, request.headers.get("X-GHMS-Token", "")):
        abort(403)
    threading.Timer(0.5, lambda: os._exit(0)).start()
    return "stopping"
