"""グループホームのPCと本部のPCのデータの受け渡し（Firebase の Firestore を「受け渡し場所」に使う）。

- データは送る前に AES-256-GCM で暗号化する。鍵は事業所のPCにだけあり、Firebase（Google）からは中身が見えない
- どちらのPCが直してよいかを、データの表ごとに決める（同じものを2か所で直して、片方が消えないように）
    本部（hq）  ：請求・給与・経費・加算・処遇改善・設定・ログインする人・今日のやることの「済」など（HQ_TABLES）
    グループホーム（home）：それ以外（入居者・日誌・記録・実績・勤務表・勤務の確定など）
  それぞれ自分の表だけを送り（pkg_<役>）、相手の表を受け取って置きかえる
- 本部が入力を直すときは「本部操作」をオンにする：
    1. 本部が ctl_hq に「本部操作したい（req）」と書く
    2. グループホームは見るだけになり、すべての表を送って（full_home）ctl_home に「どうぞ（granted）」と書く
    3. 本部はそれを受け取ってから、すべてを直せるようになる。直したらすべてを送る（full_hq）
    4. 本部がオフにすると、最後のすべて（full_hq）を送って「おわり（release_seq）」と書く
    5. グループホームはそれを受け取ってから、また直せるようになり、ctl_home に「おわりを受け取った（released）」と書く
  グループホームのPCが動いていないときは「強制」もできる（グループホームの、その間の入力はバックアップに残して捨てる）
- ログインする人（users）は両方で変わる（パスワード・PIN）ので、行ごとに新しいほう（updated_at）を使う
- 操作の記録（audit_log）は、それぞれのPCの分を足し合わせる（origin に相手の役）
- 設定（sync.json）は暗号の鍵とパスワードを含むので、データベースには入れず、データのフォルダに置く
"""

import base64
import hashlib
import json
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from datetime import datetime

log = logging.getLogger(__name__)

HOME, HQ = "home", "hq"
ROLE_LABEL = {HOME: "グループホーム", HQ: "本部"}
OTHER = {HOME: HQ, HQ: HOME}
# 本部が直す表。それ以外はグループホームが直す
HQ_TABLES = {"invoices", "payslips", "expenses", "basic_units", "addons", "resident_addons", "addon_checks",
             "shogu_plans", "shogu_allocations", "shogu_requirements", "settings", "choice_options", "custom_fields",
             "record_columns", "renewal_items", "todo_done", "leave_grants", "career_grades", "evaluations", "users"}
LOCAL_TABLES = {"devices"}  # PINの端末はPCごと
MERGED = {"users", "audit_log", "settings"}
# PCごとの設定（送らない・受け取っても上書きしない）
LOCAL_SETTING_RE = re.compile(r"^(update_|backup_|sync_|app_version$|seeded|track_defaults)")
USER_PROFILE = ["username", "display_name", "password_hash", "role", "active", "must_change", "pin_hash", "staff_id",
                "created_at", "updated_at"]
CHUNK = 700_000  # Firestore の1つの文書は1MBまで
AUDIT_DAYS = 60  # ふだん送る操作の記録の日数（すべて送るときは全部）
POLL_SEC = 60
SITE_RE = re.compile(r"^[A-Za-z0-9_-]{3,40}$")
_lock = threading.RLock()


# ---------------------------------------------------------------- 表の持ち主・画面
def table_owner(table):
    return HQ if table in HQ_TABLES else HOME


# 画面（保存するとき）がどちらの表を直すか。ここにない画面はグループホーム
HQ_ENDPOINTS = {"billing.invoices", "payroll.edit", "payroll.index", "payroll.settings", "views.settings", "customize.choices",
                "customize.features", "customize.fields", "views.addon_check", "views.shogu_plan", "today.mark",
                "compliance.index", "docs.record_columns_settings", "docs.renewal", "leave.staff", "auth.users"}
# どちらのPCでも、いつでも使える（そのPCだけのこと・ログイン・自分のパスワード）
FREE_ENDPOINTS = {"auth.login", "auth.logout", "auth.reauth", "auth.pin_login", "auth.my_password", "auth.my_pin",
                  "auth.devices", "auth.setup", "backups.index", "backups.download", "system.update", "system.shutdown",
                  "sync.index", "sync.connect_file", "static"}


def endpoint_owner(endpoint, view_args):
    if endpoint in HQ_ENDPOINTS:
        return HQ
    if endpoint and endpoint.startswith("crud."):
        return table_owner((view_args or {}).get("key", ""))
    return HOME


# ---------------------------------------------------------------- 設定・状態のファイル
def _dir(db_path):
    return os.path.dirname(os.path.abspath(db_path))


def cfg_path(db_path):
    return os.path.join(_dir(db_path), "sync.json")


def state_path(db_path):
    return os.path.join(_dir(db_path), "sync_state.json")


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _write_json(path, data):
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def load_cfg(db_path):
    c = _read_json(cfg_path(db_path))
    return c if c.get("role") in (HOME, HQ) and c.get("key") else None


def save_cfg(db_path, cfg):
    _write_json(cfg_path(db_path), cfg)


def load_state(db_path):
    return _read_json(state_path(db_path))


def save_state(db_path, st):
    _write_json(state_path(db_path), st)


def new_key():
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()


def parse_key(text):
    """鍵の文字（または鍵のファイルの中身）から32バイトの鍵。読めなければ None"""
    t = (text or "").strip()
    m = re.search(r"GHMS-SYNC-KEY:([A-Za-z0-9_-]+):([A-Za-z0-9_=-]+)", t)
    raw = m.group(2) if m else re.sub(r"\s+", "", t)
    try:
        k = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    except (ValueError, TypeError):
        return None
    return raw if len(k) == 32 else None


def key_file_text(cfg):
    return f"GHMS-SYNC-KEY:{cfg['site']}:{cfg['key']}\n"


CONNECT_HEAD = "GHMS-CONNECT:"


def connect_file_text(cfg):
    """参加するPCに渡す「接続ファイル」：受け渡し場所・同期用のログイン・暗号の鍵（参加するPCの役は相手の役）"""
    body = {k: cfg[k] for k in ("site", "api_key", "project", "email", "password", "key")}
    body["role"] = OTHER[cfg["role"]]
    return CONNECT_HEAD + base64.urlsafe_b64encode(json.dumps(body).encode()).decode() + "\n"


def parse_connect(text):
    t = (text or "").strip()
    i = t.find(CONNECT_HEAD)
    if i < 0:
        return None
    raw = t[i + len(CONNECT_HEAD):].split()[0]
    try:
        body = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode())
    except (ValueError, TypeError):
        return None
    need = ("site", "api_key", "project", "email", "password", "key", "role")
    if not all(isinstance(body.get(k), str) and body.get(k) for k in need) or body["role"] not in (HOME, HQ) \
            or not parse_key(body["key"]) or not SITE_RE.match(body["site"]):
        return None
    return {k: body[k] for k in need}


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------- 受け渡し場所（Firestore）
class SyncError(Exception):
    pass


class Firestore:
    """Firebase の Firestore（REST）。ログインは Firebase Authentication のメールとパスワード"""

    def __init__(self, cfg):
        self.cfg = cfg
        self.base = (f"https://firestore.googleapis.com/v1/projects/{urllib.parse.quote(cfg['project'])}"
                     f"/databases/(default)/documents/ghms_sync/{cfg['site']}/files")
        self._token, self._exp = None, 0

    def _req(self, method, url, body=None, auth=True):
        headers = {"Content-Type": "application/json"}
        if auth:
            headers["Authorization"] = f"Bearer {self.token()}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310（https の決まったURLだけ）
                txt = r.read().decode()
                return json.loads(txt) if txt else {}
        except urllib.error.HTTPError as e:
            if e.code == 404 and method == "GET":
                return None
            detail = e.read().decode(errors="replace")[:300]
            raise SyncError(f"Firebase がエラーを返しました（{e.code}）：{detail}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise SyncError(f"Firebase につながりません（インターネットの接続を確かめてください）：{e}") from e

    def token(self):
        if self._token and time.time() < self._exp - 120:
            return self._token
        key = urllib.parse.quote(self.cfg["api_key"])
        r = self._req("POST", f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={key}",
                      {"email": self.cfg["email"], "password": self.cfg["password"], "returnSecureToken": True}, auth=False)
        self._token, self._exp = r["idToken"], time.time() + int(r.get("expiresIn", 3600))
        return self._token

    @staticmethod
    def _enc(v):
        if isinstance(v, bool):
            return {"booleanValue": v}
        if isinstance(v, int):
            return {"integerValue": str(v)}
        return {"stringValue": "" if v is None else str(v)}

    @staticmethod
    def _dec(f):
        if "integerValue" in f:
            return int(f["integerValue"])
        if "booleanValue" in f:
            return f["booleanValue"]
        return f.get("stringValue", "")

    def get(self, name):
        r = self._req("GET", f"{self.base}/{name}")
        if r is None:
            return None
        return {k: self._dec(v) for k, v in (r.get("fields") or {}).items()}

    def put(self, name, data):
        self._req("PATCH", f"{self.base}/{name}", {"fields": {k: self._enc(v) for k, v in data.items()}})

    def delete(self, name):
        try:
            self._req("DELETE", f"{self.base}/{name}")
        except SyncError:
            log.warning("古いデータを消せませんでした: %s", name)


class MemoryStore:
    """テスト用の受け渡し場所（Firestore のかわり）"""

    def __init__(self):
        self.docs = {}

    def get(self, name):
        d = self.docs.get(name)
        return dict(d) if d is not None else None

    def put(self, name, data):
        self.docs[name] = dict(data)

    def delete(self, name):
        self.docs.pop(name, None)


_stores = {}  # テストで差しかえる：{db_path: store}


def store_for(db_path, cfg):
    return _stores.get(db_path) or Firestore(cfg)


# ---------------------------------------------------------------- 暗号化
def _aesgcm(key):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    return AESGCM(base64.urlsafe_b64decode(key + "=" * (-len(key) % 4)))


def encrypt(cfg, kind, seq, data):
    blob = zlib.compress(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode(), 6)
    nonce = secrets.token_bytes(12)
    aad = f"ghms|{cfg['site']}|{kind}|{seq}".encode()
    return base64.b64encode(nonce + _aesgcm(cfg["key"]).encrypt(nonce, blob, aad)).decode()


def decrypt(cfg, kind, seq, payload):
    from cryptography.exceptions import InvalidTag

    raw = base64.b64decode(payload)
    aad = f"ghms|{cfg['site']}|{kind}|{seq}".encode()
    try:
        blob = _aesgcm(cfg["key"]).decrypt(raw[:12], raw[12:], aad)
    except InvalidTag as e:
        raise SyncError("受け取ったデータを開けません（鍵がちがうか、データがこわれています）") from e
    return json.loads(zlib.decompress(blob).decode())


# ---------------------------------------------------------------- データの包み・置きかえ
def _tables(con):
    return [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]


def _cols(con, table):
    return [r[1] for r in con.execute(f'PRAGMA table_info("{table}")')]


def build(db_path, role, full):
    """送るデータ。full でなければ自分の表（＋ログインする人・自分の操作の記録）だけ"""
    con = sqlite3.connect(db_path, timeout=30)
    try:
        out = {}
        for t in _tables(con):
            if t in LOCAL_TABLES or t == "audit_log":
                continue
            if not full and table_owner(t) != role and t != "users":
                continue
            cols = _cols(con, t)
            rows = [list(r) for r in con.execute(f'SELECT * FROM "{t}" ORDER BY rowid')]
            if t == "settings":
                rows = [r for r in rows if not LOCAL_SETTING_RE.match(str(r[0]))]
            out[t] = {"cols": cols, "rows": rows}
        acols = _cols(con, "audit_log")
        sql = "SELECT * FROM audit_log WHERE origin IS NULL"
        args = ()
        if not full:
            sql += " AND at >= ?"
            args = (datetime.fromtimestamp(time.time() - AUDIT_DAYS * 86400).strftime("%Y-%m-%d %H:%M:%S"),)
        audit = {"cols": acols, "rows": [list(r) for r in con.execute(sql + " ORDER BY id", args)]}
    finally:
        con.close()
    return {"from": role, "full": bool(full), "tables": out, "audit": audit}


def digest(pkg):
    return hashlib.sha256(json.dumps([pkg["tables"], pkg["audit"]], ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def apply(db_path, pkg, tables_from, replace_users=False):
    """受け取ったデータで置きかえる。tables_from＝置きかえてよい表の持ち主（{"home"}・{"hq"}・{"home","hq"}）。
    ログインする人は行ごとに新しいほう（はじめて参加するときは replace_users で、そっくり置きかえる）、
    操作の記録は足し合わせる"""
    sender = pkg["from"]
    tables = pkg["tables"]
    con = sqlite3.connect(db_path, timeout=30)
    try:
        con.execute("PRAGMA foreign_keys=OFF")
        local = set(_tables(con))
        # 独自の項目は先に（表に列を足すため）
        if "custom_fields" in tables and HQ in tables_from:
            _replace(con, "custom_fields", tables["custom_fields"])
            con.commit()
            from .customize import apply_custom_fields

            apply_custom_fields(con)
        for t, data in tables.items():
            if t not in local or t in LOCAL_TABLES or t == "custom_fields":
                continue
            if t == "users" and replace_users:
                _replace(con, t, data)
            elif t == "users":
                _merge_users(con, data)
            elif table_owner(t) not in tables_from:
                continue
            elif t == "settings":
                keep = [r for r in con.execute("SELECT key, value FROM settings") if LOCAL_SETTING_RE.match(str(r[0]))]
                _replace(con, t, data)
                for k, v in keep:
                    con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, v))
            else:
                _replace(con, t, data)
        _merge_audit(con, pkg.get("audit") or {}, sender)
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    try:
        from .customize import forget_features_cache

        forget_features_cache()
    except Exception:  # noqa: BLE001
        pass


def _replace(con, t, data):
    mine = _cols(con, t)
    idx = [i for i, c in enumerate(data["cols"]) if c in mine]
    cols = [data["cols"][i] for i in idx]
    con.execute(f'DELETE FROM "{t}"')
    if cols:
        q = ",".join("?" * len(cols))
        names = ",".join(f'"{c}"' for c in cols)
        con.executemany(f'INSERT INTO "{t}" ({names}) VALUES ({q})', ([r[i] for i in idx] for r in data["rows"]))


def _merge_users(con, data):
    mine = set(_cols(con, "users"))
    rows = [dict(zip(data["cols"], r)) for r in data["rows"]]
    local = {r[0]: r for r in con.execute("SELECT id, updated_at, session_ver FROM users")}
    for r in rows:
        prof = {k: r[k] for k in USER_PROFILE if k in r and k in mine}
        lr = local.get(r["id"])
        if lr is None:
            names = ["id"] + list(prof) + (["session_ver"] if "session_ver" in r and "session_ver" in mine else [])
            vals = [r["id"]] + list(prof.values()) + ([r["session_ver"]] if "session_ver" in names else [])
            try:
                con.execute(f'INSERT INTO users ({",".join(names)}) VALUES ({",".join("?" * len(names))})', vals)
            except sqlite3.IntegrityError:
                log.warning("同じ名前のログインがあるため、受け取れませんでした: %s", r.get("username"))
            continue
        if (r.get("updated_at") or "") > (lr[1] or ""):
            sets = ",".join(f'"{k}"=?' for k in prof)
            try:
                con.execute(f"UPDATE users SET {sets} WHERE id=?", list(prof.values()) + [r["id"]])
            except sqlite3.IntegrityError:
                log.warning("同じ名前のログインがあるため、受け取れませんでした: %s", r.get("username"))
        if "session_ver" in r:
            con.execute("UPDATE users SET session_ver=MAX(COALESCE(session_ver,0), ?) WHERE id=?", (r["session_ver"] or 0, r["id"]))


def _merge_audit(con, data, sender):
    if not data.get("rows"):
        return
    mine = _cols(con, "audit_log")
    idx = [i for i, c in enumerate(data["cols"]) if c in mine and c not in ("id", "origin")]
    cols = [data["cols"][i] for i in idx]
    at_i = data["cols"].index("at")
    since = min((r[at_i] or "") for r in data["rows"])
    con.execute("DELETE FROM audit_log WHERE origin=? AND at >= ?", (sender, since))
    names = ",".join(f'"{c}"' for c in cols) + ',"origin"'
    q = ",".join("?" * (len(cols) + 1))
    con.executemany(f"INSERT INTO audit_log ({names}) VALUES ({q})", ([r[i] for i in idx] + [sender] for r in data["rows"]))


# ---------------------------------------------------------------- 送る・受け取る
def _seq(st, name):
    return max(int(time.time() * 1000), int((st.get("up", {}).get(name) or {}).get("seq", 0)) + 1)


def upload(db_path, cfg, store, st, full, force=False):
    """送る。変わっていなければ送らない（force のときは送る）。送った番号（seq）。送らなければ None"""
    role = cfg["role"]
    name = f"{'full' if full else 'pkg'}_{role}"
    pkg = build(db_path, role, full)
    h = digest(pkg)
    prev = st.setdefault("up", {}).get(name) or {}
    if not force and prev.get("hash") == h:
        return None
    seq = _seq(st, name)
    pkg["seq"], pkg["created"] = seq, now()
    from . import VERSION

    pkg["app"] = VERSION
    payload = encrypt(cfg, name, seq, pkg)
    parts = [payload[i:i + CHUNK] for i in range(0, len(payload), CHUNK)] or [""]
    for i, p in enumerate(parts):
        store.put(f"{name}_{seq}_{i}", {"d": p})
    store.put(name, {"seq": seq, "n": len(parts), "sha": hashlib.sha256(payload.encode()).hexdigest(), "at": now(),
                     "app": VERSION})
    for i in range(int(prev.get("n", 0))):
        store.delete(f"{name}_{prev.get('seq')}_{i}")
    st["up"][name] = {"seq": seq, "n": len(parts), "hash": h, "at": now()}
    return seq


def download(cfg, store, name, after_seq):
    """after_seq より新しいものがあれば受け取る（なければ None）"""
    m = store.get(name)
    if not m or int(m.get("seq", 0)) <= int(after_seq or 0):
        return None
    seq, n = int(m["seq"]), int(m["n"])
    parts = []
    for i in range(n):
        d = store.get(f"{name}_{seq}_{i}")
        if d is None:
            raise SyncError("受け取っている途中で、相手が新しいデータを送りました。次の同期で受け取ります")
        parts.append(d.get("d", ""))
    payload = "".join(parts)
    if hashlib.sha256(payload.encode()).hexdigest() != m.get("sha"):
        raise SyncError("受け取ったデータがこわれています（次の同期でやり直します）")
    pkg = decrypt(cfg, name, seq, payload)
    if pkg.get("seq") != seq:
        raise SyncError("受け取ったデータの番号が合いません")
    return pkg


def _receive(db_path, cfg, store, st, name, tables_from, after=None, replace_users=False):
    """受け取って置きかえる。受け取ったら番号を返す"""
    down = st.setdefault("down", {})
    pkg = download(cfg, store, name, max(int(down.get(name, 0)), int(after or 0)))
    if pkg is None:
        return None
    if pkg.get("from") != OTHER[cfg["role"]]:
        raise SyncError("相手ではないPCのデータが届きました（設定を確かめてください）")
    if not st.get("backed_up_today") == datetime.now().strftime("%Y%m%d"):
        from .backup import event_backup

        event_backup(db_path, "before_sync", "sync")
        st["backed_up_today"] = datetime.now().strftime("%Y%m%d")
    apply(db_path, pkg, tables_from, replace_users=replace_users)
    down[name] = pkg["seq"]
    st["last_down_at"] = now()
    st["other_app"] = pkg.get("app", "")
    return pkg["seq"]


def _put_ctl(store, cfg, st):
    store.put(f"ctl_{cfg['role']}", st.setdefault("ctl", {}))


# ---------------------------------------------------------------- 1回の同期
def run_once(db_path):
    """設定があれば1回同期する。状態（dict）を返す"""
    cfg = load_cfg(db_path)
    if not cfg:
        return {}
    with _lock:
        st = load_state(db_path)
        store = store_for(db_path, cfg)
        try:
            if cfg["role"] == HOME:
                _run_home(db_path, cfg, store, st)
            else:
                _run_hq(db_path, cfg, store, st)
            st["last_ok_at"], st["error"] = now(), ""
        except Exception as e:  # noqa: BLE001
            st["error"], st["error_at"] = str(e)[:300], now()
            log.warning("同期できませんでした: %s", e)
        save_state(db_path, st)
        return st


def is_origin(cfg):
    """はじめに設定したPC（データを持っていて、相手が参加してくる）か。ふつうは本部"""
    return bool(cfg.get("origin", cfg["role"] == HOME))


def _answer_full(db_path, cfg, store, st, other):
    """相手が「すべてほしい」（はじめての参加・取り直し）と言っていれば、すべてを送る"""
    ctl = st.setdefault("ctl", {})
    if other.get("need_full") and ctl.get("full_for") != other["need_full"] and not st.get("locked"):
        seq = upload(db_path, cfg, store, st, full=True, force=True)
        ctl.update(full_for=other["need_full"], full_seq=seq, at=now())
        _put_ctl(store, cfg, st)
        # すべてを送ったときから、相手の表は相手が直す（このあとこのPCで直すと、相手が受け取らないため）
        st["partner_joined"] = True
    if other.get("joined"):
        st["partner_joined"] = True


def _join(db_path, cfg, store, st, other):
    """参加するPC：相手のすべてを受け取って、このPCのデータを置きかえる。終わったら True"""
    ctl = st.setdefault("ctl", {})
    if not ctl.get("need_full"):
        ctl.update(need_full=secrets.token_hex(8), at=now())
        _put_ctl(store, cfg, st)
        return False
    if other.get("full_for") != ctl["need_full"]:
        return False
    o = OTHER[cfg["role"]]
    seq = _receive(db_path, cfg, store, st, f"full_{o}", {HOME, HQ}, after=int(other.get("full_seq") or 0) - 1,
                   replace_users=True)
    if seq is None:
        return False
    # このPCで前にログインしていた人は、すべて入り直し（同じIDが別の人になっているため）
    con = sqlite3.connect(db_path, timeout=30)
    try:
        con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('sync_epoch', ?)", (secrets.token_hex(8),))
        con.commit()
    finally:
        con.close()
    st["joined"] = True
    st.setdefault("down", {})[f"pkg_{o}"] = seq
    st.setdefault("up", {})  # 受け取った内容と同じものは送り直さない
    ctl.pop("need_full", None)
    ctl["joined"] = True
    _put_ctl(store, cfg, st)
    return True


def _run_home(db_path, cfg, store, st):
    hq = store.get("ctl_hq") or {}
    ctl = st.setdefault("ctl", {})
    req = hq.get("req", "")
    if not is_origin(cfg) and not st.get("joined"):
        _join(db_path, cfg, store, st, hq)
        return
    _answer_full(db_path, cfg, store, st, hq)
    if hq.get("want") == HQ and req and ctl.get("granted") != req:
        # 本部操作が始まる：先に見るだけにしてから、すべてを送る
        st["locked"] = {"req": req, "by": hq.get("by", ""), "since": hq.get("at", ""), "force": bool(hq.get("force"))}
        save_state(db_path, st)
        if hq.get("force"):
            from .backup import event_backup

            event_backup(db_path, "before_sync", "forced")
            st["forced_note"] = (f"{hq.get('at', '')} に本部が強制で本部操作を始めました。それまでにこのPCで入力して、"
                                 "まだ送れていなかったものは反映されていません（バックアップの「同期の前」に残しています）。")
            seq = 0
        else:
            seq = upload(db_path, cfg, store, st, full=True, force=True)
        ctl.update(granted=req, grant_seq=seq, at=now())
        _put_ctl(store, cfg, st)
        return
    if st.get("locked"):
        lock = st["locked"]
        # 本部操作のあいだも、本部が直したものを受け取って見られるようにする
        _receive(db_path, cfg, store, st, "full_hq", {HOME, HQ})
        if hq.get("want") != HQ and hq.get("req") == lock["req"]:
            if int(st.get("down", {}).get("full_hq", 0)) >= int(hq.get("release_seq") or 0):
                st["locked"] = None
                ctl.update(released=lock["req"], after_seq=_seq(st, f"pkg_{HOME}") - 1, at=now())
                _put_ctl(store, cfg, st)
        return
    _receive(db_path, cfg, store, st, f"pkg_{HQ}", {HQ})
    upload(db_path, cfg, store, st, full=False)


def _run_hq(db_path, cfg, store, st):
    home = store.get("ctl_home") or {}
    ctl = st.setdefault("ctl", {})
    if not is_origin(cfg) and not st.get("joined"):
        _join(db_path, cfg, store, st, home)
        return
    _answer_full(db_path, cfg, store, st, home)
    lock = st.get("lock")
    if lock:
        if not lock.get("ready"):
            if lock.get("force"):
                lock["ready"] = True
            elif home.get("granted") == lock["req"]:
                seq = _receive(db_path, cfg, store, st, f"full_{HOME}", {HOME}, after=int(home.get("grant_seq") or 0) - 1)
                if seq is not None or int(st.get("down", {}).get(f"full_{HOME}", 0)) >= int(home.get("grant_seq") or 0):
                    lock["ready"] = True
            if not lock.get("ready"):
                return
        upload(db_path, cfg, store, st, full=True)
        return
    wait = st.get("wait_release")
    if wait:
        if home.get("released") != wait:
            upload(db_path, cfg, store, st, full=False)
            return
        st["wait_release"] = None
        st["min_home_seq"] = int(home.get("after_seq") or 0)
    _receive(db_path, cfg, store, st, f"pkg_{HOME}", {HOME}, after=st.get("min_home_seq"))
    upload(db_path, cfg, store, st, full=False)


# ---------------------------------------------------------------- 本部操作（本部のPC）
def lock_on(db_path, user, force=False):
    cfg = load_cfg(db_path)
    with _lock:
        st = load_state(db_path)
        if st.get("lock"):
            return st
        req = secrets.token_hex(8)
        st["lock"] = {"req": req, "ready": False, "force": bool(force), "by": user, "since": now()}
        st.setdefault("ctl", {}).update(want=HQ, req=req, force=bool(force), by=user, at=now(), release_seq=0)
        _put_ctl(store_for(db_path, cfg), cfg, st)
        save_state(db_path, st)
    return run_once(db_path)


def lock_off(db_path):
    cfg = load_cfg(db_path)
    with _lock:
        st = load_state(db_path)
        lock = st.get("lock")
        if not lock:
            return st
        store = store_for(db_path, cfg)
        seq = upload(db_path, cfg, store, st, full=True, force=True) if lock.get("ready") else 0
        st.setdefault("ctl", {}).update(want="none", req=lock["req"], release_seq=seq or 0, at=now())
        _put_ctl(store, cfg, st)
        st["lock"] = None
        st["wait_release"] = lock["req"]
        save_state(db_path, st)
    return run_once(db_path)


# ---------------------------------------------------------------- 画面で使う
def status(db_path):
    cfg = load_cfg(db_path)
    if not cfg:
        return None
    st = load_state(db_path)
    role = cfg["role"]
    s = {"role": role, "label": ROLE_LABEL[role], "site": cfg["site"], "st": st, "mode": "normal", "origin": is_origin(cfg)}
    if not s["origin"] and not st.get("joined"):
        s["mode"] = "joining"
    elif s["origin"] and not st.get("partner_joined"):
        s["mode"] = "alone"  # 相手がまだ参加していない：このPCで全部直してよい
    elif role == HOME and st.get("locked"):
        s["mode"] = "locked"
    elif role == HQ:
        lock = st.get("lock")
        if lock and lock.get("ready"):
            s["mode"] = "hq_all"
        elif lock:
            s["mode"] = "waiting"
        elif st.get("wait_release"):
            s["mode"] = "releasing"
    return s


def can_write(db_path, endpoint, view_args):
    """(書いてよいか, だめなときの説明)"""
    if endpoint in FREE_ENDPOINTS or not endpoint:
        return True, ""
    s = status(db_path)
    if s is None:
        return True, ""
    if endpoint == "backups.restore":
        return False, "同期を使っているあいだは、バックアップから元にもどせません（「同期の設定」で同期をやめてから行ってください）。"
    owner = endpoint_owner(endpoint, view_args)
    if s["mode"] == "joining":
        return False, f"{ROLE_LABEL[OTHER[s['role']]]}のデータを受け取っています。少し待ってから、もう一度行ってください。"
    if s["mode"] == "alone":
        return True, ""
    if s["role"] == HOME:
        if s["mode"] == "locked":
            return False, "本部操作中です。この間はグループホームのPCでは見るだけです（本部が終わると、また入力できます）。"
        if owner == HQ:
            return False, "請求・給与・設定などは本部のPCで行います（このPCでは見るだけです）。"
        return True, ""
    if s["mode"] == "hq_all":
        return True, ""
    if owner == HOME:
        msg = "入力（日誌・記録・実績・勤務表など）を直すときは、「同期の設定」で「本部操作」をオンにしてください。"
        if s["mode"] == "waiting":
            msg = "グループホームのPCの返事を待っています。返事がくると、入力も直せるようになります。"
        return False, msg
    return True, ""


def health(db_path):
    """今日のやることに出すもの：[(level, title, detail)]"""
    s = status(db_path)
    if not s:
        return []
    st = s["st"]
    out = []
    if st.get("error") and (not st.get("last_ok_at") or st["last_ok_at"] < st.get("error_at", "")):
        out.append(("week", "本部とグループホームの同期ができていません", f"{st.get('error_at', '')}：{st['error']}"))
    if st.get("forced_note"):
        out.append(("week", "本部が強制で本部操作をしました", st["forced_note"]))
    return out


_started = set()


def start_auto(db_path):
    if db_path in _started:
        return
    _started.add(db_path)

    def loop():
        time.sleep(15)
        while True:
            try:
                if load_cfg(db_path):
                    st = run_once(db_path)
                    busy = (st.get("lock") and not st["lock"].get("ready")) or st.get("wait_release") or \
                        (not is_origin(load_cfg(db_path) or {"role": HOME}) and not st.get("joined"))
                    time.sleep(10 if busy else POLL_SEC)
                    continue
            except Exception:  # noqa: BLE001
                log.exception("同期でまちがいがありました")
            time.sleep(POLL_SEC)

    threading.Thread(target=loop, name="ghms-sync", daemon=True).start()
