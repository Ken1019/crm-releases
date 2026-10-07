"""グループホーム業務管理システム 起動スクリプト

    python run.py              … このPCだけで使う（http://127.0.0.1:8000）
    python run.py --lan        … 同じ事業所内LANの他のPCからも使う
"""

import argparse
import threading
import webbrowser

from waitress import serve

from ghms import create_app


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--lan", action="store_true", help="LAN内の他のPCからの接続を許可する")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--no-browser", action="store_true")
    a = p.parse_args()
    host = "0.0.0.0" if a.lan else "127.0.0.1"
    app = create_app()
    url = f"http://127.0.0.1:{a.port}/"
    print(f"グループホーム業務管理システムを起動しました: {url}")
    if a.lan:
        print("LAN内の他のPCからは http://<このPCのIPアドレス>:%d/ で接続できます" % a.port)
    print("終了するにはこのウィンドウを閉じるか Ctrl+C を押してください。")
    if not a.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    serve(app, host=host, port=a.port, threads=8)


if __name__ == "__main__":
    main()
