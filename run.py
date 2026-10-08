"""グループホーム業務管理システム 起動スクリプト

    python run.py              … このPCだけで使う（http://127.0.0.1:8000）
    python run.py --lan        … 同じ事業所内LANの他のPCからも使う
    python run.py --stop       … 起動中のシステムを止める（インストーラー・更新で使う）
    python run.py --backup D:\\GHMSバックアップ … データを日付付きでバックアップ（タスクスケジューラ用）

インストール版（GHMS.exe）も同じ。すでに起動していればブラウザを開くだけにする。
LAN・ポートは config.ini（インストール版は C:\\ProgramData\\GHMS\\config.ini）でも指定できる。
"""

import argparse
import logging
import os
import secrets
import socket
import sys
import threading
import time
import urllib.request
import webbrowser

from ghms import runtime


def _port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def stop(port):
    """起動中のサーバーに、このPCの中から止めるよう頼む"""
    try:
        with open(runtime.token_path(), encoding="utf-8") as f:
            token = f.read().strip()
    except OSError:
        return False
    req = urllib.request.Request(f"http://127.0.0.1:{port}/__shutdown", data=b"", method="POST",
                                 headers={"X-GHMS-Token": token})
    try:
        urllib.request.urlopen(req, timeout=5).read()
    except OSError:
        pass
    for _ in range(20):  # 止まるまで少し待つ
        if not _port_in_use(port):
            return True
        time.sleep(0.25)
    return not _port_in_use(port)


def backup(dest):
    """動かしたままでも安全にコピーできる SQLite のバックアップ機能を使う"""
    import sqlite3
    from datetime import datetime

    src_path = os.path.join(runtime.data_dir(), "ghms.sqlite3")
    if not os.path.exists(src_path):
        print("データが見つかりません:", src_path)
        return False
    os.makedirs(dest, exist_ok=True)
    out = os.path.join(dest, f"ghms_{datetime.now():%Y%m%d_%H%M}.sqlite3")
    src, dst = sqlite3.connect(src_path), sqlite3.connect(out)
    src.backup(dst)
    dst.close()
    src.close()
    print("バックアップしました:", out)
    return True


def _setup_logging():
    log_dir = os.path.join(runtime.base_dir(), "logs")
    os.makedirs(log_dir, exist_ok=True)
    logging.basicConfig(filename=os.path.join(log_dir, "ghms.log"), level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", encoding="utf-8")


def main():
    # 設定を読む前にログを用意する（--windowed では画面に何も出ないため、まちがいはログに残す）
    if runtime.is_installed():
        try:
            _setup_logging()
        except OSError:
            pass
    cfg = runtime.load_config()
    p = argparse.ArgumentParser()
    p.add_argument("--lan", action="store_true", default=cfg["lan"], help="LAN内の他のPCからの接続を許可する")
    p.add_argument("--port", type=int, default=cfg["port"])
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--stop", action="store_true", help="起動中のシステムを止める")
    p.add_argument("--backup", metavar="フォルダ", help="データを指定のフォルダにバックアップして終わる")
    a = p.parse_args()
    url = f"http://127.0.0.1:{a.port}/"

    if a.stop:
        sys.exit(0 if stop(a.port) else 1)
    if a.backup:
        sys.exit(0 if backup(a.backup) else 1)
    if _port_in_use(a.port):  # すでに起動中 → 画面を開くだけ
        if not a.no_browser:
            webbrowser.open(url)
        return

    os.environ.setdefault("GHMS_DATA_DIR", runtime.data_dir())
    os.makedirs(runtime.data_dir(), exist_ok=True)
    with open(runtime.token_path(), "w", encoding="utf-8") as f:
        f.write(secrets.token_hex(24))

    from waitress import serve

    from ghms import create_app

    app = create_app()
    host = "0.0.0.0" if a.lan else "127.0.0.1"
    print(f"グループホーム業務管理システムを起動しました: {url}")
    if a.lan:
        print("LAN内の他のPCからは http://<このPCのIPアドレス>:%d/ で接続できます" % a.port)
    print("終了するにはこのウィンドウを閉じるか Ctrl+C を押してください。")
    if not a.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    serve(app, host=host, port=a.port, threads=8)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # 画面のない起動（--windowed）でも、止まった理由をログ（logs\ghms.log）に残す
        logging.getLogger("ghms").exception("起動できませんでした")
        raise
