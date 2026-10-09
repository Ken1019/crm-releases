"""動かしている環境（インストール版か、開発・ZIP版か）と、データ・設定の置き場所。

インストール版（PyInstallerで固めた GHMS.exe）では、プログラムは Program Files に、
設定（config.ini）とログは C:\\ProgramData\\GHMS に、データはインストールのときに選んだフォルダ
（初めは C:\\ProgramData\\GHMS\\data）に置く。更新・アンインストールしてもデータは残る。
"""

import configparser
import logging
import os
import sys

APP_DIR_NAME = "GHMS"


def is_installed():
    """インストーラーで入れた GHMS.exe として動いているか"""
    return bool(getattr(sys, "frozen", False))


def base_dir():
    """データ・設定・ログを置くフォルダ"""
    if os.environ.get("GHMS_HOME"):
        return os.environ["GHMS_HOME"]
    if is_installed():
        root = os.environ.get("PROGRAMDATA") or os.path.expanduser("~")
        return os.path.join(root, APP_DIR_NAME)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def data_dir():
    """データ（ghms.sqlite3）のフォルダ。インストールのときに選んだ場所（config.ini の [data] dir）。
    書いていなければ、設定のフォルダの data"""
    if os.environ.get("GHMS_DATA_DIR"):
        return os.environ["GHMS_DATA_DIR"]
    chosen = configured_data_dir()
    return chosen or os.path.join(base_dir(), "data")


REG_KEY = r"SOFTWARE\GHMS"


def _registry_data_dir():
    """インストーラーが記録したデータの場所（HKLM\SOFTWARE\GHMS の DataDir）。
    config.ini は Windows の言語の文字コードで書かれ、日本語のフォルダ名がこわれることがあるため、レジストリに置く"""
    if sys.platform != "win32":
        return ""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, REG_KEY) as k:
            return str(winreg.QueryValueEx(k, "DataDir")[0] or "").strip()
    except OSError:
        return ""


def configured_data_dir():
    """config.ini の [data] dir（手で書いたとき）→ インストーラーが記録した場所 の順"""
    cp = _read_ini()
    raw = (cp.get("data", "dir", fallback="") or "").strip().strip('"')
    if raw and os.path.isabs(raw):
        return raw
    if raw:
        logging.getLogger(__name__).warning("config.ini の [data] dir はドライブから書いてください（%r）。決まった場所を使います", raw)
    reg = _registry_data_dir() if is_installed() else ""
    return reg if reg and os.path.isabs(reg) else ""


def config_path():
    return os.path.join(base_dir(), "config.ini")


def _parser():
    # 「%」を特別な意味に使わない（パスワードやURLに % が入っていても、そのまま読む）
    return configparser.ConfigParser(interpolation=None)


def _read_ini():
    """config.ini を読む。インストーラー（Windows）は日本語をShift-JIS（cp932）で書くため、UTF-8 でなければ cp932 で読む"""
    path = config_path()
    if not os.path.exists(path):
        return _parser()
    errors = []
    for enc in ("utf-8-sig", "cp932"):
        cp = _parser()
        try:
            cp.read(path, encoding=enc)
            return cp
        except (UnicodeError, configparser.Error) as e:
            errors.append(f"{enc}: {e}")
    logging.getLogger(__name__).warning("config.ini を読めませんでした（初期の設定で動かします）: %s / %s", path, " / ".join(errors))
    return _parser()


DEFAULT_PORT = 8000


def load_config():
    """config.ini（インストーラーの選択や手で書いた設定）を読む。ポートの値がまちがっていれば 8000 にする"""
    cp = _read_ini()
    sec = cp["server"] if cp.has_section("server") else {}
    raw = str(sec.get("port", "") or "").strip()
    port = DEFAULT_PORT
    if raw:
        try:
            port = int(raw)
            if not 1 <= port <= 65535:
                raise ValueError(raw)
        except ValueError:
            logging.getLogger(__name__).warning("config.ini の port の値がまちがっています（%r）。%d を使います", raw, DEFAULT_PORT)
            port = DEFAULT_PORT
    app = cp["app"] if cp.has_section("app") else {}
    return {
        "lan": str(sec.get("lan", "0")).strip().lower() in ("1", "true", "yes"),
        "port": port,
        # 画面の開き方：window＝アプリの窓（Edge のアプリ表示。アドレス欄・タブなし）／browser＝ふつうのブラウザ
        "window": str(app.get("window", "window")).strip().lower() != "browser",
    }


def token_path():
    """起動中のサーバーを止めるための合い言葉（このPCの中だけで使う）"""
    return os.path.join(data_dir(), "run.token")


def load_product():
    """販売・配布するときの名前と連絡先（config.ini の [product]）。事業所ごとに変えられる。
    [product]
    name = グループホーム業務管理
    vendor = 販売元の会社名
    support = サポートの連絡先（電話・メール）
    update_url = 更新情報（latest.json）のURL（販売元ごとの配布先）
    """
    cp = _read_ini()
    sec = cp["product"] if cp.has_section("product") else {}
    return {"name": sec.get("name", "").strip() or "グループホーム業務管理", "vendor": sec.get("vendor", "").strip(),
            "support": sec.get("support", "").strip(), "update_url": sec.get("update_url", "").strip()}


def load_office():
    """インストーラーで入力した事業所名・事業所番号（config.ini の [office]）"""
    cp = _read_ini()
    sec = cp["office"] if cp.has_section("office") else {}
    return {"name": sec.get("name", "").strip(), "no": sec.get("no", "").strip()}
