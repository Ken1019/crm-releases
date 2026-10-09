"""バックアップ：毎日の自動バックアップ・2か所目（USBメモリ・外付けHDD・NAS）への保存・復元。

データのフォルダの backup に、次のように残す（いちばん新しいものから決まった数だけ）：
    daily\\          毎日 1つ（その日の最後の状態に近いもの。1時間ごとに新しくする）… 30日分
    monthly\\        毎月 1つ（その月にはじめて作ったもの）… 24か月分
    before_update\\  ソフトの版が変わる前（起動したときに自動）… 20こ
    before_install\\ インストーラーで入れかえる前（インストーラーが作る）… 20こ
    before_restore\\ 復元する前の状態 … 20こ
    manual\\         「今すぐバックアップ」… 20こ
2か所目を設定すると、daily・monthly・manual を「<保存先>\\GHMSバックアップ」にもコピーする。
作ったバックアップは、開いて中身がこわれていないか（quick_check）を確かめてから置く。
"""

import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import threading
import time
from datetime import datetime, timedelta

log = logging.getLogger(__name__)

ROOT2_NAME = "GHMSバックアップ"
KINDS = {"daily": "毎日", "monthly": "毎月", "manual": "手動", "before_update": "版が変わる前",
         "before_install": "インストールの前", "before_restore": "復元の前"}
KEEP = {"daily": 30, "monthly": 24, "manual": 20, "before_update": 20, "before_install": 20, "before_restore": 20}
TO_DEST2 = ("daily", "monthly", "manual")
FILE_RE = re.compile(r"^ghms[\w.\-]*\.sqlite3$")
REFRESH_SEC = 3600  # その日のバックアップを新しくする間かく
DEST2_STALE_DAYS = 7  # 2か所目にこの日数とどいていなければ知らせる
_lock = threading.Lock()


def local_root(db_path):
    return os.path.join(os.path.dirname(os.path.abspath(db_path)), "backup")


def dest2_root(dest):
    return os.path.join(dest, ROOT2_NAME)


def _status_path(db_path):
    return os.path.join(local_root(db_path), "status.json")


def read_status(db_path):
    try:
        with open(_status_path(db_path), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _write_status(db_path, st):
    # 状態はデータベースに書かない（書くとデータが変わったことになり、バックアップが止まらなくなる）
    path = _status_path(db_path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _stamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def check_file(path):
    """GHMS のデータとして開けるか（こわれていないか）。(ok, 理由)"""
    if not os.path.isfile(path):
        return False, "ファイルがありません"
    try:
        with open(path, "rb") as f:
            if f.read(16) != b"SQLite format 3\x00":
                return False, "GHMS のデータ（バックアップ）のファイルではありません"
        con = sqlite3.connect(path)
        try:
            r = con.execute("PRAGMA quick_check").fetchone()
            if not r or r[0] != "ok":
                return False, "ファイルがこわれています"
            names = {x[0] for x in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            con.close()
    except sqlite3.Error as e:
        return False, f"ファイルを開けません（{e}）"
    if not {"users", "settings", "residents"} <= names:
        return False, "GHMS のデータ（バックアップ）のファイルではありません"
    return True, ""


def make_backup(db_path, out):
    """動かしたままでも安全にコピーできる SQLite のバックアップ機能でコピーし、こわれていないか確かめてから置く"""
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp = out + ".part"
    if os.path.exists(tmp):
        os.remove(tmp)
    src = sqlite3.connect(db_path, timeout=30)
    try:
        dst = sqlite3.connect(tmp)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    ok, why = check_file(tmp)
    if not ok:
        os.remove(tmp)
        raise RuntimeError(f"バックアップを確かめたところ、正しくできていませんでした（{why}）")
    os.replace(tmp, out)
    return out


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def copy_verified(src, out):
    """コピーして、中身が同じか（SHA-256）確かめてから置く"""
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp = out + ".part"
    shutil.copyfile(src, tmp)
    if _sha256(tmp) != _sha256(src):
        os.remove(tmp)
        raise RuntimeError("コピーした内容が元と違います（保存先の故障のおそれ）")
    os.replace(tmp, out)
    shutil.copystat(src, out)
    return out


def _files(folder):
    try:
        return sorted(n for n in os.listdir(folder) if FILE_RE.match(n))
    except OSError:
        return []


def prune(folder, keep):
    """古いものから消して keep こだけ残す（名前に日時が入っているので、名前の順＝古い順）"""
    names = _files(folder)
    for n in names[:max(0, len(names) - keep)]:
        try:
            os.remove(os.path.join(folder, n))
        except OSError:
            log.warning("古いバックアップを消せませんでした: %s", n)


def _needs_refresh(out, db_path):
    if not os.path.exists(out):
        return True
    return os.path.getmtime(db_path) > os.path.getmtime(out) and time.time() - os.path.getmtime(out) >= REFRESH_SEC


def get_dest2(db_path):
    """2か所目の保存先（設定の backup_dir2）。データベースから直接読む（画面の外のスレッドからも使う）"""
    try:
        con = sqlite3.connect(db_path, timeout=30)
        try:
            r = con.execute("SELECT value FROM settings WHERE key='backup_dir2'").fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return ""
    return (r[0] if r else "") or ""


def _sync_dest2(db_path, dest, st, kinds=TO_DEST2):
    """データのフォルダのバックアップを、2か所目にコピーする（ないもの・新しくなったものだけ）"""
    s2 = st.setdefault("dest2", {})
    s2["path"] = dest
    s2["tried_at"] = _stamp()
    try:
        if not os.path.isdir(dest):
            raise RuntimeError("保存先が見つかりません（USBメモリ・外付けHDDがつながっているか、ネットワークにつながっているか確かめてください）")
        root, root2 = local_root(db_path), dest2_root(dest)
        for kind in kinds:
            for n in _files(os.path.join(root, kind)):
                a, b = os.path.join(root, kind, n), os.path.join(root2, kind, n)
                if not os.path.exists(b) or os.path.getmtime(a) > os.path.getmtime(b) + 1 or os.path.getsize(a) != os.path.getsize(b):
                    copy_verified(a, b)
            prune(os.path.join(root2, kind), KEEP[kind])
        s2["last_at"], s2["error"] = _stamp(), ""
    except Exception as e:  # noqa: BLE001 — 失敗しても1か所目のバックアップは続ける
        s2["error"] = str(e)[:300]
        log.warning("2か所目へのバックアップに失敗しました: %s", e)


def run_auto(db_path, force=False):
    """毎日・毎月のバックアップ（なければ作る・1時間たっていて変わっていれば新しくする）と、2か所目へのコピー"""
    if not os.path.exists(db_path):
        return read_status(db_path)
    with _lock:
        st = read_status(db_path)
        root = local_root(db_path)
        now = datetime.now()
        try:
            daily = os.path.join(root, "daily", f"ghms_{now:%Y%m%d}.sqlite3")
            if force or _needs_refresh(daily, db_path):
                make_backup(db_path, daily)
            monthly = os.path.join(root, "monthly", f"ghms_{now:%Y%m}.sqlite3")
            if not os.path.exists(monthly):
                copy_verified(daily, monthly)
            for kind in KINDS:
                prune(os.path.join(root, kind), KEEP[kind])
            st.update(last_at=_stamp(), last_file=daily, error="")
        except Exception as e:  # noqa: BLE001
            st["error"] = str(e)[:300]
            st["error_at"] = _stamp()
            log.exception("バックアップに失敗しました")
        dest = get_dest2(db_path)
        if dest:
            _sync_dest2(db_path, dest, st)
        else:
            st.pop("dest2", None)
        _write_status(db_path, st)
        return st


def backup_now(db_path):
    """「今すぐバックアップ」：manual に作り、2か所目にもコピーする"""
    out = os.path.join(local_root(db_path), "manual", f"ghms_{datetime.now():%Y%m%d_%H%M%S}.sqlite3")
    with _lock:
        make_backup(db_path, out)
        prune(os.path.dirname(out), KEEP["manual"])
    st = run_auto(db_path)
    return out, st


def event_backup(db_path, kind, label):
    """版が変わる前・復元の前などのバックアップ"""
    out = os.path.join(local_root(db_path), kind, f"ghms_{datetime.now():%Y%m%d_%H%M%S}_{label}.sqlite3")
    with _lock:
        make_backup(db_path, out)
        prune(os.path.dirname(out), KEEP[kind])
    return out


# ---------------------------------------------------------------- 版が変わったとき
def _ver(v):
    try:
        return tuple(int(x) for x in str(v).split("."))
    except ValueError:
        return (0,)


def stored_version(db_path):
    try:
        con = sqlite3.connect(db_path, timeout=30)
        try:
            r = con.execute("SELECT value FROM settings WHERE key='app_version'").fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    return r[0] if r else ""


def before_version_change(db_path, version):
    """ソフトの版が変わって起動したとき、データの形を新しくする前にバックアップする。
    バックアップを作れなければ起動しない（データを守るため）"""
    if not os.path.exists(db_path):
        return None
    old = stored_version(db_path)
    if old == version:
        return None
    label = re.sub(r"[^0-9.]", "", f"{old or '0'}") + "_to_" + re.sub(r"[^0-9.]", "", version)
    try:
        return event_backup(db_path, "before_update", label)
    except Exception as e:
        raise RuntimeError(f"新しい版で起動する前のバックアップを作れませんでした。データを守るため起動を止めます：{e}") from e


def remember_version(db_path, version):
    """データベースに、使った版のうちいちばん新しいものを覚える（古い版に戻したときに上書きしない）"""
    old = stored_version(db_path)
    if old and _ver(old) > _ver(version):
        log.warning("データは新しい版（%s）で使われていました。いまの版は %s です", old, version)
        return
    if old != version:
        con = sqlite3.connect(db_path, timeout=30)
        try:
            con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('app_version', ?)", (version,))
            con.commit()
        finally:
            con.close()


# ---------------------------------------------------------------- 一覧・復元
def list_backups(db_path, dest=None):
    """[{id, where, kind, kind_label, name, at, size}]（新しい順）"""
    out = []
    places = [("local", local_root(db_path))]
    if dest:
        places.append(("dest2", dest2_root(dest)))
    for where, root in places:
        for kind, label in KINDS.items():
            folder = os.path.join(root, kind)
            for n in _files(folder):
                p = os.path.join(folder, n)
                try:
                    stt = os.stat(p)
                except OSError:
                    continue
                out.append({"id": f"{where}:{kind}:{n}", "where": where, "kind": kind, "kind_label": label, "name": n,
                            "at": datetime.fromtimestamp(stt.st_mtime).strftime("%Y-%m-%d %H:%M"), "size": stt.st_size,
                            "mtime": stt.st_mtime})
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out


def resolve(db_path, dest, backup_id):
    """一覧の id から、ファイルの場所を出す（一覧にあるフォルダの中だけ）"""
    where, _, rest = (backup_id or "").partition(":")
    kind, _, name = rest.partition(":")
    if kind not in KINDS or not FILE_RE.match(name) or where not in ("local", "dest2"):
        return None
    if where == "dest2" and not dest:
        return None
    root = local_root(db_path) if where == "local" else dest2_root(dest)
    p = os.path.join(root, kind, name)
    return p if os.path.isfile(p) else None


def restore(db_path, src_path):
    """src_path のバックアップで、いまのデータを置きかえる。先に、いまのデータを before_restore に残す"""
    ok, why = check_file(src_path)
    if not ok:
        raise ValueError(why)
    saved = event_backup(db_path, "before_restore", "now")
    with _lock:
        src = sqlite3.connect(src_path)
        try:
            dst = sqlite3.connect(db_path, timeout=30)
            try:
                src.backup(dst)
                # 復元したデータでも、前のログインはすべて入り直してもらう
                dst.execute("UPDATE users SET session_ver = COALESCE(session_ver, 0) + 1")
                dst.commit()
            finally:
                dst.close()
        finally:
            src.close()
    # 古い版のバックアップでも、いまの版の形（表・列）にそろえる
    from .db import init_db

    init_db(db_path)
    return saved


def upload_path(db_path):
    return os.path.join(os.path.dirname(os.path.abspath(db_path)), f"restore_upload_{secrets.token_hex(8)}.sqlite3")


def check_dest2(path, db_path):
    """2か所目の保存先として使えるか。(ok, 理由)"""
    if not path:
        return True, ""
    if not os.path.isabs(path):
        return False, "「D:\\」や「E:\\バックアップ」のように、ドライブから書いてください"
    a = os.path.normcase(os.path.abspath(path))
    data = os.path.normcase(os.path.dirname(os.path.abspath(db_path)))
    try:
        same = os.path.commonpath([a, data]) == data
    except ValueError:  # ドライブがちがう
        same = False
    if same:
        return False, "データのフォルダの中には置けません（同じ場所がこわれると両方なくなるため）"
    if not os.path.isdir(path):
        return False, "そのフォルダが見つかりません（USBメモリ・外付けHDDをつないでから設定してください）"
    try:
        os.makedirs(dest2_root(path), exist_ok=True)
        t = os.path.join(dest2_root(path), f".write_test_{secrets.token_hex(4)}")
        with open(t, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(t)
    except OSError as e:
        return False, f"そのフォルダに書きこめません（{e}）"
    return True, ""


def health(db_path, dest):
    """今日のやることに出すもの：[(level, title, detail)]"""
    out = []
    st = read_status(db_path)
    if st.get("error"):
        out.append(("over", "バックアップに失敗しています", f"{st.get('error_at', '')}：{st['error']}（バックアップの画面で確かめてください）"))
    elif st.get("last_at") and st["last_at"] < (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S"):
        out.append(("week", "バックアップが3日以上できていません", f"最後のバックアップ：{st['last_at']}"))
    if not dest:
        out.append(("month", "バックアップを2か所目（USBメモリ・外付けHDDなど）にも保存する",
                    "PCがこわれたときのために、別の場所にもバックアップを残す設定をしてください"))
    else:
        s2 = st.get("dest2") or {}
        last = s2.get("last_at") or ""
        cutoff = (datetime.now() - timedelta(days=DEST2_STALE_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
        if s2.get("tried_at") and not last:
            out.append(("week", "2か所目にバックアップできていません", s2.get("error") or "バックアップの画面で確かめてください"))
        elif last and last < cutoff:
            out.append(("week", f"2か所目のバックアップが{DEST2_STALE_DAYS}日以上とどいていません",
                        (s2.get("error") or "") + f"（最後にとどいた日時：{last}）"))
    return out


# ---------------------------------------------------------------- 自動で動かす
_started = set()


def start_auto(db_path, every_sec=1800):
    """起動中、30分ごとに run_auto を動かす（同じデータベースに2つ動かさない）"""
    if db_path in _started:
        return
    _started.add(db_path)

    def loop():
        time.sleep(20)  # 起動の直後は画面を早く出す
        while True:
            try:
                run_auto(db_path)
            except Exception:  # noqa: BLE001
                log.exception("自動バックアップでまちがいがありました")
            time.sleep(every_sec)

    threading.Thread(target=loop, name="ghms-backup", daemon=True).start()
