import os
import sqlite3
from datetime import datetime

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
    from .seed import seed

    seed(con)
    con.close()


def get_setting(key, default=""):
    row = get_db().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row and row["value"] is not None else default


def set_setting(key, value):
    db = get_db()
    db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    db.commit()
