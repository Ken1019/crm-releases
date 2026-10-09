import os
import sqlite3
from datetime import date, datetime

from flask import current_app, g

from .entities import ENTITIES

SQL_TYPE = {"number": "REAL", "check": "INTEGER", "ref": "INTEGER"}


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def init_db(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL,"
        " display_name TEXT, password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'staff',"
        " created_at TEXT, updated_at TEXT)"
    )
    con.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
    con.execute(
        "CREATE TABLE IF NOT EXISTS addon_checks (addon_id INTEGER, ym TEXT, item TEXT,"
        " checked INTEGER, checked_by TEXT, checked_at TEXT, PRIMARY KEY (addon_id, ym, item))"
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS audit_log (id INTEGER PRIMARY KEY, at TEXT, username TEXT,"
        " action TEXT, entity TEXT, record_id INTEGER)"
    )
    # ログインの安全対策・操作記録の詳細（既存DBにも列を追加）
    _ensure_columns(con, "users", {"active": "INTEGER DEFAULT 1", "must_change": "INTEGER DEFAULT 0",
                                   "failed_count": "INTEGER DEFAULT 0", "locked_until": "TEXT", "last_login": "TEXT",
                                   "pin_hash": "TEXT", "session_ver": "INTEGER DEFAULT 0"})
    # PINでログインできる事業所の端末（端末に保存する合い言葉はハッシュで保管）
    con.execute(
        "CREATE TABLE IF NOT EXISTS devices (id INTEGER PRIMARY KEY, name TEXT, token_hash TEXT UNIQUE,"
        " active INTEGER DEFAULT 1, created_by TEXT, created_at TEXT, last_used TEXT)"
    )
    _ensure_columns(con, "audit_log", {"detail": "TEXT", "ip": "TEXT", "origin": "TEXT"})  # origin：同期で受け取った相手のPC
    # 月ごとのグリッド入力（在居・外泊などの実績、勤務表）
    con.execute(
        "CREATE TABLE IF NOT EXISTS attendance (resident_id INTEGER, date TEXT, code TEXT,"
        " updated_by TEXT, updated_at TEXT, PRIMARY KEY (resident_id, date))"
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS shifts (staff_id INTEGER, date TEXT, code TEXT,"
        " updated_by TEXT, updated_at TEXT, PRIMARY KEY (staff_id, date))"
    )
    # 献立表・指定更新の書類チェックリスト
    con.execute("CREATE TABLE IF NOT EXISTS menus (home_id INTEGER, date TEXT, meal TEXT, text TEXT, updated_by TEXT,"
                " updated_at TEXT, PRIMARY KEY (home_id, date, meal))")
    con.execute("CREATE TABLE IF NOT EXISTS renewal_items (id INTEGER PRIMARY KEY, sort INTEGER, name TEXT, endpoint TEXT,"
                " done INTEGER DEFAULT 0, note TEXT)")
    # 実績記録票の項目（日中支援・夜間支援など）と、日ごとの○
    con.execute("CREATE TABLE IF NOT EXISTS record_columns (id INTEGER PRIMARY KEY, label TEXT, sort INTEGER,"
                " active INTEGER DEFAULT 1, auto TEXT, builtin TEXT)")
    _ensure_columns(con, "record_columns", {"mark": "TEXT", "unit": "TEXT"})  # 記録票に出す記号（1・○など）
    con.execute("CREATE TABLE IF NOT EXISTS record_marks (resident_id INTEGER, date TEXT, col_id INTEGER, value TEXT,"
                " updated_by TEXT, updated_at TEXT, PRIMARY KEY (resident_id, date, col_id))")
    # タイムカード・体温（健康チェック）・給与明細
    con.execute("CREATE TABLE IF NOT EXISTS timecards (id INTEGER PRIMARY KEY, staff_id INTEGER, date TEXT, clock_in TEXT,"
                " clock_out TEXT, break_min INTEGER, note TEXT, updated_by TEXT, updated_at TEXT)")
    con.execute("CREATE INDEX IF NOT EXISTS timecards_staff_date ON timecards (staff_id, date)")
    con.execute("CREATE TABLE IF NOT EXISTS health_checks (id INTEGER PRIMARY KEY, staff_id INTEGER, date TEXT, time TEXT,"
                " temp REAL, symptoms TEXT, note TEXT, updated_by TEXT, updated_at TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS payslips (staff_id INTEGER, ym TEXT, data TEXT, gross INTEGER, deductions INTEGER,"
                " net INTEGER, status TEXT, updated_by TEXT, updated_at TEXT, PRIMARY KEY (staff_id, ym))")
    _ensure_columns(con, "users", {"staff_id": "INTEGER"})
    # 勤務の確定（勤務表から：出勤＝勤務表の時間でタイムカードを作る／休み・有給＝勤務表をそのように直す）
    con.execute("CREATE TABLE IF NOT EXISTS attend_days (staff_id INTEGER, date TEXT, status TEXT, planned_code TEXT, card_id INTEGER,"
                " confirmed_by TEXT, confirmed_at TEXT, PRIMARY KEY (staff_id, date))")
    con.execute("CREATE TABLE IF NOT EXISTS todo_done (key TEXT, period TEXT, done_by TEXT, done_at TEXT, PRIMARY KEY (key, period))")
    con.execute("CREATE TABLE IF NOT EXISTS leave_grants (id INTEGER PRIMARY KEY, staff_id INTEGER, grant_date TEXT, days REAL,"
                " basis TEXT, auto INTEGER, updated_by TEXT, updated_at TEXT)")  # ログインする人と職員の情報をつなぐ
    # 事業所ごとのカスタマイズ（選択肢・独自の項目）
    con.execute("CREATE TABLE IF NOT EXISTS choice_options (field TEXT, value TEXT, sort INTEGER, active INTEGER DEFAULT 1,"
                " track_days INTEGER, PRIMARY KEY (field, value))")
    con.execute("CREATE TABLE IF NOT EXISTS custom_fields (id INTEGER PRIMARY KEY, entity TEXT, label TEXT, type TEXT,"
                " options TEXT, list_show INTEGER DEFAULT 0, sort INTEGER, active INTEGER DEFAULT 1, created_at TEXT)")
    for key, ent in ENTITIES.items():
        cols = ", ".join(f'"{f["name"]}" {SQL_TYPE.get(f["type"], "TEXT")}' for f in ent["fields"])
        con.execute(
            f'CREATE TABLE IF NOT EXISTS "{key}" (id INTEGER PRIMARY KEY, {cols},'
            " created_at TEXT, updated_at TEXT, updated_by TEXT)"
        )
        existing = {r[1] for r in con.execute(f'PRAGMA table_info("{key}")')}
        for f in ent["fields"]:
            if f["name"] not in existing:
                con.execute(f'ALTER TABLE "{key}" ADD COLUMN "{f["name"]}" {SQL_TYPE.get(f["type"], "TEXT")}')
    con.commit()
    from .customize import apply_custom_fields, seed_choices
    from .seed import seed

    seed(con)
    seed_choices(con)
    apply_custom_fields(con)
    from .docs import seed_record_columns

    seed_record_columns(con)
    ensure_system_start(con)
    con.close()


def ensure_system_start(con):
    """「使い始めた日」（system_start）。前の版から使っているデータベースで、まだ入っていなければ1回だけ入れる：
    住居・入居者・職員をいちばん早く登録した日（なければ今日）。はじめての設定の前（ログインする人がいない）は入れない"""
    if con.execute("SELECT 1 FROM settings WHERE key='system_start'").fetchone():
        return
    if not con.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
        return
    days = []
    for t in ("homes", "residents", "staff"):
        v = con.execute(f"SELECT MIN(substr(created_at, 1, 10)) FROM {t} WHERE created_at IS NOT NULL AND created_at != ''").fetchone()[0]
        try:
            days.append(date.fromisoformat(v))
        except (TypeError, ValueError):
            pass
    start = min(days + [date.today()])
    con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('system_start', ?)", (start.isoformat(),))
    con.commit()


def start_today():
    set_setting("system_start", date.today().isoformat())


def system_start():
    """このシステムを使い始めた日。これより前の日の書きもれ・前の月の仕事は、今日のやること・実地指導チェックに出さない"""
    v = (get_setting("system_start", "") or "").strip()
    try:
        return date.fromisoformat(v[:10])
    except ValueError:
        return None


def _ensure_columns(con, table, cols):
    existing = {r[1] for r in con.execute(f'PRAGMA table_info("{table}")')}
    for name, decl in cols.items():
        if name not in existing:
            con.execute(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {decl}')


def _settings_cache():
    """1回の画面表示のあいだだけ設定を覚えておく（同じ設定を何百回も読まないように）"""
    from flask import g, has_app_context

    if not has_app_context():
        return None
    if "_settings" not in g:
        g._settings = {r["key"]: r["value"] for r in get_db().execute("SELECT key, value FROM settings")}
    return g._settings


def get_setting(key, default=""):
    cache = _settings_cache()
    if cache is not None:
        v = cache.get(key)
        return v if v is not None else default
    row = get_db().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row and row["value"] is not None else default


def set_setting(key, value):
    db = get_db()
    db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    db.commit()
    cache = _settings_cache()
    if cache is not None:
        cache[key] = value
