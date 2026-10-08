"""動かしている環境（インストール版か、開発・ZIP版か）と、データ・設定の置き場所。

インストール版（PyInstallerで固めた GHMS.exe）では、プログラムは Program Files に、
データは C:\\ProgramData\\GHMS に置く。更新・アンインストールしてもデータは残る。
"""

import configparser
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
    return os.environ.get("GHMS_DATA_DIR") or os.path.join(base_dir(), "data")


def config_path():
    return os.path.join(base_dir(), "config.ini")


def _read_ini():
    """config.ini を読む。インストーラー（Windows）は日本語をShift-JIS（cp932）で書くため、UTF-8 でなければ cp932 で読む"""
    cp = configparser.ConfigParser()
    for enc in ("utf-8-sig", "cp932"):
        try:
            cp.read(config_path(), encoding=enc)
            return cp
        except (UnicodeError, configparser.Error):
            cp = configparser.ConfigParser()
    return cp


def load_config():
    """config.ini（インストーラーの選択や手で書いた設定）を読む"""
    cp = _read_ini()
    sec = cp["server"] if cp.has_section("server") else {}
    return {
        "lan": str(sec.get("lan", "0")).strip() in ("1", "true", "yes"),
        "port": int(sec.get("port", "8000") or 8000),
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
