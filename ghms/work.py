"""出勤タイムカードと職員の体温（健康チェック）。

- 職員は自分の画面で「出勤する」「退勤する」を押すだけ。出勤のときに体温と体調をたずねる
- 夜勤のように日をまたいだ勤務は、出勤した日の記録として扱う（退勤が出勤より前の時刻なら翌日）
- 管理者は職員ごと・月ごとに打刻を直せる（直した人と日時が残る）。出勤簿をExcelで出せる
"""

from datetime import date, datetime, timedelta

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from . import excel
from .auth import admin_required, is_admin, log_event
from .billing import month_days
from .db import get_db, get_setting, now
from .views import parse_date, parse_ym

bp = Blueprint("work", __name__, url_prefix="/work")

SYMPTOMS = ["せき", "のどの痛み", "鼻水", "だるさ", "頭痛", "下痢", "吐き気・おう吐", "味やにおいがわかりにくい"]
WEEK = "月火水木金土日"


def setting_num(key, default):
    try:
        return float(get_setting(key, str(default)) or default)
    except ValueError:
        return float(default)


def fever_line():
    return setting_num("pay_fever", 37.5)


def night_band():
    """深夜手当の時間帯（設定。初期は22時〜翌9時。法律の深夜割増は22時〜翌5時で、それより広くするのはよい）"""
    s = _min(get_setting("pay_night_start", "22:00"))
    e = _min(get_setting("pay_night_end", "09:00"))
    return (22 * 60 if s is None else s), (9 * 60 if e is None else e)


def night_windows():
    """出勤日の0時を0とした分で、深夜の時間帯を前の日〜翌々日の分まで並べる"""
    s, e = night_band()
    if e <= s:
        return [(k * 1440 + s, (k + 1) * 1440 + e) for k in (-1, 0, 1)]
    return [(k * 1440 + s, k * 1440 + e) for k in (0, 1, 2)]


def night_label():
    s, e = night_band()
    return f"{s // 60}時{f'{s % 60}分' if s % 60 else ''}〜{'翌' if e <= s else ''}{e // 60}時{f'{e % 60}分' if e % 60 else ''}"


def _min(hhmm):
    try:
        h, m = (hhmm or "").split(":")[:2]
        return int(h) * 60 + int(m)
    except ValueError:
        return None


def work_minutes(card):
    """1回の勤務の 実働・残業（1日8時間をこえた分）・深夜（設定の時間帯）・夜勤かどうか"""
    start, end = _min(card["clock_in"]), _min(card["clock_out"])
    if start is None or end is None:
        return None
    if end <= start:
        end += 1440
    brk = card["break_min"] or 0
    total = max(0, end - start - brk)
    night = sum(max(0, min(end, b) - max(start, a)) for a, b in night_windows())
    return {"total": total, "over": max(0, total - 480), "night": max(0, night), "yakin": end > 1440 or night >= 240}


def hm(minutes):
    if not minutes:
        return ""
    return f"{minutes // 60}:{minutes % 60:02d}"


def my_staff_id():
    """ログインしている人の職員ID（ユーザー管理でつなぐ。つながっていなければ名前が同じ職員）"""
    if g.user["staff_id"]:
        return g.user["staff_id"]
    name = (g.user["display_name"] or "").replace(" ", "").replace("　", "")
    for s in get_db().execute("SELECT id, name FROM staff WHERE status IS NULL OR status != '退職'"):
        if (s["name"] or "").replace(" ", "").replace("　", "") == name and name:
            return s["id"]
    return None


def max_shift_minutes():
    """1回の勤務の上限。夜勤（例 16時〜翌10時）があるので設定で変えられる。これをこえた打刻は退勤を押せず、管理者が直す"""
    return int(setting_num("pay_max_shift_hours", 20) * 60)


def _started(card):
    try:
        return datetime.strptime(f"{card['date']} {card['clock_in']}", "%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return None


def open_card(staff_id):
    """まだ退勤していない打刻。出勤から上限の時間をすぎたものは「退勤忘れ」として扱い、ここでは返さない"""
    since = (date.today() - timedelta(days=2)).isoformat()
    for c in get_db().execute("SELECT * FROM timecards WHERE staff_id=? AND clock_out IS NULL AND date >= ? ORDER BY date DESC, id DESC",
                              (staff_id, since)):
        st = _started(c)
        if st and datetime.now() - st <= timedelta(minutes=max_shift_minutes()):
            return c
    return None


def forgotten_cards(staff_id=None, days=31):
    """退勤の打刻がないまま上限をすぎたもの（管理者が直す）"""
    since = (date.today() - timedelta(days=days)).isoformat()
    sql, args = "SELECT * FROM timecards WHERE clock_out IS NULL AND date >= ?", [since]
    if staff_id:
        sql += " AND staff_id=?"
        args.append(staff_id)
    out = []
    for c in get_db().execute(sql + " ORDER BY date", args):
        st = _started(c)
        if st is None or datetime.now() - st > timedelta(minutes=max_shift_minutes()):
            out.append(c)
    return out


def default_break(card, out_time):
    cin, cout = _min(card["clock_in"]), _min(out_time)
    if cin is None or cout is None:
        return 0
    span = (cout - cin) % 1440
    return int(setting_num("pay_break_default", 60)) if span > 360 else 0


def month_cards(staff_id, first, last):
    return get_db().execute("SELECT * FROM timecards WHERE staff_id=? AND date BETWEEN ? AND ? ORDER BY date, clock_in",
                            (staff_id, first.isoformat(), last.isoformat())).fetchall()


def month_summary(staff_id, first, last):
    """月の合計。yk_〜 は夜勤の分だけ（夜勤を1回いくらで払うときは、時間の計算から外すため）"""
    s = {"days": set(), "day_days": set(), "total": 0, "over": 0, "night": 0, "yakin": 0, "missing": 0,
         "yk_total": 0, "yk_over": 0, "yk_night": 0, "yk_cards": []}
    for c in month_cards(staff_id, first, last):
        w = work_minutes(c)
        if w is None:
            s["missing"] += 1
            continue
        s["days"].add(c["date"])
        for k in ("total", "over", "night"):
            s[k] += w[k]
        if w["yakin"]:
            s["yakin"] += 1
            for k in ("total", "over", "night"):
                s["yk_" + k] += w[k]
            s["yk_cards"].append((c, w))
        else:
            s["day_days"].add(c["date"])
    s["days"], s["day_days"] = len(s["days"]), len(s["day_days"])
    return s


def health_of(staff_id, d):
    return get_db().execute("SELECT * FROM health_checks WHERE staff_id=? AND date=? ORDER BY time", (staff_id, d)).fetchall()


def is_unwell(h):
    return (h["temp"] or 0) >= fever_line() or bool(h["symptoms"])


def _save_health(staff_id, form, username):
    temp = form.get("temp", type=float)
    symptoms = "、".join(s for s in SYMPTOMS if form.get(f"sym::{s}"))
    if temp is None and not symptoms and not form.get("health_note"):
        return None
    t = datetime.now()
    get_db().execute("INSERT INTO health_checks (staff_id, date, time, temp, symptoms, note, updated_by, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                     (staff_id, t.date().isoformat(), t.strftime("%H:%M"), temp, symptoms, form.get("health_note") or "",
                      username, now()))
    return {"temp": temp, "symptoms": symptoms}


def punch(sid, action, form, username):
    """出勤・退勤・体温の記録。時刻はこのPCの時計で決める（本人は時刻を入れられない）。メッセージの一覧を返す"""
    db = get_db()
    t = datetime.now()
    card = open_card(sid)
    msgs = []
    if action == "in":
        if card:
            return [("error", f"すでに {card['clock_in']} に出勤しています。退勤のときは「退勤する」を押してください。")]
        if get_setting("pay_health_required", "1") == "1" and form.get("temp", type=float) is None:
            return [("error", "出勤の前に体温を入れてください。")]
        h = _save_health(sid, form, username)
        db.execute("INSERT INTO timecards (staff_id, date, clock_in, note, updated_by, updated_at) VALUES (?,?,?,?,?,?)",
                   (sid, t.date().isoformat(), t.strftime("%H:%M"), form.get("note") or "", username, now()))
        log_event("clock_in", "timecards", sid, t.strftime("%H:%M"), username=username)
        if h and ((h["temp"] or 0) >= fever_line() or h["symptoms"]):
            msgs.append(("error", "体調がよくないようです。勤務の前に管理者に連絡してください。管理者のホームにもお知らせが出ます。"))
        msgs.append(("ok", f"{t:%H:%M} 出勤しました。今日もよろしくお願いします。"))
    elif action == "out":
        if not card:
            return [("error", "出勤の打刻が見つかりません。出勤を押し忘れたときや、長い時間がたったときは管理者に直してもらってください。")]
        out = t.strftime("%H:%M")
        brk = form.get("break_min", type=int)
        db.execute("UPDATE timecards SET clock_out=?, break_min=?, updated_by=?, updated_at=? WHERE id=?",
                   (out, default_break(card, out) if brk is None else max(0, brk), username, now(), card["id"]))
        log_event("clock_out", "timecards", sid, out, username=username)
        msgs.append(("ok", f"{out} 退勤しました。おつかれさまでした。"))
    elif action == "health":
        if _save_health(sid, form, username) is not None:
            msgs.append(("ok", "体温・体調を記録しました。"))
    return msgs


# ---------------------------------------------------------------- 事務所のPCの打刻画面（ログインしないで使う）
def _kiosk_people():
    db = get_db()
    rows = []
    for s in db.execute("SELECT s.*, u.id AS uid, u.pin_hash, u.locked_until FROM staff s JOIN users u ON u.staff_id=s.id "
                        "WHERE u.active=1 AND (s.status IS NULL OR s.status != '退職') ORDER BY s.kana, s.name"):
        rows.append({"s": s, "card": open_card(s["id"]), "pin": bool(s["pin_hash"])})
    return rows


@bp.route("/kiosk", methods=["GET", "POST"])
def kiosk():
    """名前を押して PIN → 出勤／退勤。押したらすぐ名前の一覧に戻る（ログインしたままにならない）。
    PINでログインできる端末として登録した事務所のPCでだけ開ける"""
    from flask import session
    from werkzeug.security import check_password_hash

    from .auth import LOCK_MINUTES, _fail, current_device

    device = current_device()
    if device is None:
        return render_template("work_kiosk.html", device=None, people=[], person=None)
    db = get_db()
    if not session.get("kiosk_csrf"):
        import secrets

        session["kiosk_csrf"] = secrets.token_hex(16)
    sid = request.values.get("staff_id", type=int)
    person = next((p for p in _kiosk_people() if p["s"]["id"] == sid), None) if sid else None
    if request.method == "POST" and person:
        if request.form.get("_k") != session.get("kiosk_csrf"):
            abort(400)
        user = db.execute("SELECT * FROM users WHERE id=?", (person["s"]["uid"],)).fetchone()
        if user["locked_until"] and user["locked_until"] > now():
            flash(f"PINを続けて間違えたため、{LOCK_MINUTES}分ほど打刻できません。管理者に連絡してください。", "error")
        elif not user["pin_hash"] or not check_password_hash(user["pin_hash"], request.form.get("pin", "")):
            _fail(user, f"打刻のPIN（{device['name']}）")
            flash("PINが違います。", "error")
            db.commit()
            return redirect(url_for("work.kiosk", staff_id=sid))
        else:
            db.execute("UPDATE users SET failed_count=0, locked_until=NULL WHERE id=?", (user["id"],))
            db.execute("UPDATE devices SET last_used=? WHERE id=?", (now(), device["id"]))
            msgs = punch(sid, request.form.get("action"), request.form, user["username"])
            for cat, msg in msgs:
                flash(f"{person['s']['name']} さん：{msg}", cat)
            db.commit()
            if any(cat == "error" for cat, _ in msgs) and request.form.get("action") == "in" and not person["card"]:
                return redirect(url_for("work.kiosk", staff_id=sid))
        db.commit()
        return redirect(url_for("work.kiosk"))
    return render_template("work_kiosk.html", device=device, people=_kiosk_people(), person=person, SYMPTOMS=SYMPTOMS,
                           default_break=int(setting_num("pay_break_default", 60)), now_time=datetime.now(),
                           kcsrf=session["kiosk_csrf"], fever=fever_line())


# ---------------------------------------------------------------- 出勤・退勤（職員の画面）
@bp.route("/clock", methods=["GET", "POST"])
def clock():
    db = get_db()
    sid = my_staff_id()
    staff = db.execute("SELECT * FROM staff WHERE id=?", (sid,)).fetchone() if sid else None
    card = open_card(sid) if sid else None
    if request.method == "POST":
        if not staff:
            abort(400)
        for cat, msg in punch(sid, request.form.get("action"), request.form, g.user["username"]):
            flash(msg, cat)
        db.commit()
        return redirect(url_for("work.clock"))
    first, last = parse_ym(None)
    cards = []
    if sid:
        for c in reversed(month_cards(sid, first, last)[-10:]):
            cards.append({"c": c, "w": work_minutes(c)})
    return render_template("work_clock.html", staff=staff, card=card, cards=cards, SYMPTOMS=SYMPTOMS, hm=hm,
                           forgotten=forgotten_cards(sid) if sid else [], max_hours=max_shift_minutes() // 60,
                           default_break=int(setting_num("pay_break_default", 60)), fever=fever_line(),
                           summary=month_summary(sid, first, last) if sid else None,
                           today_health=health_of(sid, date.today().isoformat()) if sid else [], now_time=datetime.now())


# ---------------------------------------------------------------- タイムカード（月ごと。管理者は直せる）
def _staff_list():
    return get_db().execute("SELECT * FROM staff WHERE status IS NULL OR status != '退職' ORDER BY kana, name").fetchall()


@bp.route("/timecards", methods=["GET", "POST"])
def timecards():
    db = get_db()
    first, last = parse_ym(request.values.get("ym"))
    ym = first.strftime("%Y-%m")
    admin = is_admin()
    staff_list = _staff_list() if admin else []
    sid = request.values.get("staff_id", type=int) if admin else my_staff_id()
    if admin and not sid and staff_list:
        sid = staff_list[0]["id"]
    staff = db.execute("SELECT * FROM staff WHERE id=?", (sid,)).fetchone() if sid else None
    if request.method == "POST":
        if not admin or not staff:
            abort(403)
        changed = 0
        for c in month_cards(sid, first, last):
            if request.form.get(f"del_{c['id']}"):
                db.execute("DELETE FROM timecards WHERE id=?", (c["id"],))
                changed += 1
                continue
            vals = (request.form.get(f"in_{c['id']}") or None, request.form.get(f"out_{c['id']}") or None,
                    request.form.get(f"br_{c['id']}", type=int), request.form.get(f"note_{c['id']}") or "")
            if vals != (c["clock_in"], c["clock_out"], c["break_min"], c["note"] or ""):
                db.execute("UPDATE timecards SET clock_in=?, clock_out=?, break_min=?, note=?, updated_by=?, updated_at=? WHERE id=?",
                           vals + (g.user["username"], now(), c["id"]))
                changed += 1
        for d in month_days(first, last):
            cin = request.form.get(f"in_new_{d.day}")
            if cin:
                db.execute("INSERT INTO timecards (staff_id, date, clock_in, clock_out, break_min, note, updated_by, updated_at)"
                           " VALUES (?,?,?,?,?,?,?,?)", (sid, d.isoformat(), cin, request.form.get(f"out_new_{d.day}") or None,
                                                        request.form.get(f"br_new_{d.day}", type=int), request.form.get(f"note_new_{d.day}") or "",
                                                        g.user["username"], now()))
                changed += 1
        if changed:
            log_event("timecard_edit", "timecards", sid, f"{ym} {changed}件")
        db.commit()
        flash(f"{staff['name']} さんの{first:%Y年%m月}のタイムカードを保存しました（{changed}件）。", "ok")
        return redirect(url_for("work.timecards", ym=ym, staff_id=sid))
    rows = []
    if staff:
        by_day = {}
        for c in month_cards(sid, first, last):
            by_day.setdefault(c["date"], []).append(c)
        temps = {}
        for h in db.execute("SELECT * FROM health_checks WHERE staff_id=? AND date BETWEEN ? AND ? ORDER BY time",
                            (sid, first.isoformat(), last.isoformat())):
            temps.setdefault(h["date"], h)
        plan = planned_shifts(sid, first, last)
        tmap = shift_types_map()
        diffs = shift_differences(sid, first, last)
        for d in month_days(first, last):
            cs = by_day.get(d.isoformat(), [])
            code = plan.get(d.isoformat(), "")
            rows.append({"d": d, "cards": [{"c": c, "w": work_minutes(c)} for c in cs], "health": temps.get(d.isoformat()),
                         "plan": code, "plan_t": tmap.get(code), "diffs": diffs.get(d.isoformat(), [])})
    return render_template("work_timecards.html", staff=staff, staff_list=staff_list, rows=rows, ym=ym, first=first, WEEK=WEEK,
                           hm=hm, summary=month_summary(sid, first, last) if staff else None, admin=admin, fever=fever_line(),
                           night_label=night_label())


@bp.route("/timecards.xlsx")
@admin_required
def timecards_export():
    first, last = parse_ym(request.args.get("ym"))
    from openpyxl import Workbook

    wb = Workbook()
    wb.remove(wb.active)
    for s in _staff_list():
        cards = month_cards(s["id"], first, last)
        if not cards:
            continue
        rows = []
        for c in cards:
            d = parse_date(c["date"])
            w = work_minutes(c) or {}
            rows.append([f"{d.month}/{d.day}", WEEK[d.weekday()], c["clock_in"] or "", c["clock_out"] or "", c["break_min"] or 0,
                         hm(w.get("total")), hm(w.get("over")), hm(w.get("night")), "夜勤" if w.get("yakin") else "", c["note"] or ""])
        sm = month_summary(s["id"], first, last)
        rows.append(["合計", "", "", "", "", hm(sm["total"]), hm(sm["over"]), hm(sm["night"]), f"{sm['yakin']}回", f"出勤 {sm['days']}日"])
        excel.add_table(wb.create_sheet(excel.safe_sheet_title(s["name"])), f"出勤簿　{s['name']}　{first:%Y年%m月}",
                        ["日", "曜日", "出勤", "退勤", "休憩（分）", "実働", "残業", "深夜", "夜勤", "備考"], rows,
                        widths=[7, 5, 8, 8, 9, 8, 8, 8, 7, 24])
    if not wb.sheetnames:
        wb.create_sheet("なし")
    return excel.send_workbook(wb, f"出勤簿_{first:%Y%m}")


# ---------------------------------------------------------------- 体温（健康チェック）
@bp.route("/health")
def health():
    db = get_db()
    if not is_admin():
        sid = my_staff_id()
        rows = db.execute("SELECT * FROM health_checks WHERE staff_id=? ORDER BY date DESC, time DESC LIMIT 60", (sid,)).fetchall() if sid else []
        return render_template("work_health.html", mine=True, rows=rows, fever=fever_line(), day=date.today())
    day = parse_date(request.args.get("date"), date.today())
    checks = {}
    for h in db.execute("SELECT * FROM health_checks WHERE date=? ORDER BY time", (day.isoformat(),)):
        checks.setdefault(h["staff_id"], []).append(h)
    on_duty = {c["staff_id"] for c in db.execute("SELECT staff_id FROM timecards WHERE date=?", (day.isoformat(),))}
    rows = [{"s": s, "checks": checks.get(s["id"], []), "on": s["id"] in on_duty} for s in _staff_list()]
    rows.sort(key=lambda x: (not any(is_unwell(h) for h in x["checks"]), not x["on"]))
    return render_template("work_health.html", mine=False, rows=rows, fever=fever_line(), day=day, is_unwell=is_unwell,
                           prev=(day - timedelta(days=1)).isoformat(), next=(day + timedelta(days=1)).isoformat())


def today_status():
    """管理者のホーム用：出勤中の人・体調がよくない人・打刻漏れ"""
    db = get_db()
    today = date.today().isoformat()
    names = {s["id"]: s["name"] for s in db.execute("SELECT id, name FROM staff")}
    forgot = {c["id"] for c in forgotten_cards(days=3)}
    working = [names.get(c["staff_id"], "?") + f"（{c['clock_in']}〜）" for c in db.execute(
        "SELECT * FROM timecards WHERE clock_out IS NULL AND date >= ? ORDER BY clock_in",
        ((date.today() - timedelta(days=2)).isoformat(),)) if c["id"] not in forgot]
    unwell = [(names.get(h["staff_id"], "?"), h) for h in db.execute("SELECT * FROM health_checks WHERE date=? ORDER BY time", (today,))
              if is_unwell(h)]
    return {"working": working, "unwell": unwell}


# ---------------------------------------------------------------- 勤務表（管理者が組む）とのつき合わせ
def shift_types_map():
    return {t["code"]: t for t in get_db().execute("SELECT * FROM shift_types")}


def planned_shifts(staff_id, first, last):
    return {s["date"]: s["code"] for s in get_db().execute(
        "SELECT * FROM shifts WHERE staff_id=? AND date BETWEEN ? AND ?", (staff_id, first.isoformat(), last.isoformat()))}


def _is_work_type(t):
    return bool(t and t["start"] and _min(t["start"]) is not None)


def shift_differences(staff_id, first, last):
    """勤務表と打刻のちがい（今日より前の日だけ）。{日付: [(種類, 説明)]}"""
    grace = int(setting_num("pay_late_grace", 10))
    tmap = shift_types_map()
    plan = planned_shifts(staff_id, first, last)
    cards = {}
    for c in month_cards(staff_id, first, last):
        cards.setdefault(c["date"], []).append(c)
    out = {}
    today = date.today()
    for d in month_days(first, last):
        if d >= today:
            break
        key = d.isoformat()
        t = tmap.get(plan.get(key, ""))
        cs = cards.get(key, [])
        diffs = []
        if _is_work_type(t) and not cs:
            diffs.append(("打刻なし", f"勤務表は「{t['name']}」ですが打刻がありません"))
        elif cs and not _is_work_type(t):
            diffs.append(("予定外", f"勤務表は「{t['name'] if t else '空欄'}」ですが {cs[0]['clock_in']} に打刻があります"))
        elif cs and t:
            first_in = min(_min(c["clock_in"]) for c in cs if _min(c["clock_in"]) is not None)
            ps, pe = _min(t["start"]), _min(t["end"])
            if first_in is not None and ps is not None and first_in > ps + grace:
                diffs.append(("遅刻", f"予定 {t['start']} → 出勤 {first_in // 60}:{first_in % 60:02d}"))
            last = cs[-1]
            w = work_minutes(last)
            if pe is not None and last["clock_out"] and w is not None:
                end_plan = pe + (1440 if pe <= ps else 0)
                end_real = _min(last["clock_out"]) + (1440 if _min(last["clock_out"]) <= _min(last["clock_in"]) else 0)
                if end_real < end_plan - grace:
                    diffs.append(("早退", f"予定 {t['end']} → 退勤 {last['clock_out']}"))
            yk = any((work_minutes(c) or {}).get("yakin") for c in cs)
            if bool(t["night"]) != yk:
                diffs.append(("夜勤のちがい", "勤務表は夜勤ですが、打刻は夜勤になっていません" if t["night"]
                              else "勤務表は夜勤ではありませんが、打刻が夜勤（日をまたぐ）になっています"))
        if diffs:
            out[key] = diffs
    return out


def my_upcoming_shifts(staff_id, days=14):
    tmap = shift_types_map()
    start = date.today()
    end = start + timedelta(days=days - 1)
    plan = {}
    for s in get_db().execute("SELECT * FROM shifts WHERE staff_id=? AND date BETWEEN ? AND ?",
                              (staff_id, start.isoformat(), end.isoformat())):
        plan[s["date"]] = s["code"]
    rows = []
    for i in range(days):
        d = start + timedelta(days=i)
        t = tmap.get(plan.get(d.isoformat(), ""))
        rows.append({"d": d, "w": WEEK[d.weekday()], "code": plan.get(d.isoformat(), ""), "t": t})
    return rows


@bp.route("/my-shift")
def my_shift():
    """職員：自分の勤務表（見るだけ。勤務表は管理者が組む）"""
    first, last = parse_ym(request.args.get("ym"))
    sid = my_staff_id()
    tmap = shift_types_map()
    plan = planned_shifts(sid, first, last) if sid else {}
    rows = [{"d": d, "w": WEEK[d.weekday()], "code": plan.get(d.isoformat(), ""), "t": tmap.get(plan.get(d.isoformat(), ""))}
            for d in month_days(first, last)]
    hours = sum((r["t"]["hours"] or 0) for r in rows if r["t"])
    return render_template("work_my_shift.html", rows=rows, first=first, ym=first.strftime("%Y-%m"), linked=bool(sid),
                           hours=hours, today=date.today(), types=list(tmap.values()))
