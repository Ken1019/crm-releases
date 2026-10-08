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
NIGHT_WINDOWS = [(-120, 300), (1320, 1740), (2760, 3180)]  # 22時〜翌5時（分。出勤日の0時が0）


def setting_num(key, default):
    try:
        return float(get_setting(key, str(default)) or default)
    except ValueError:
        return float(default)


def fever_line():
    return setting_num("pay_fever", 37.5)


def _min(hhmm):
    try:
        h, m = (hhmm or "").split(":")[:2]
        return int(h) * 60 + int(m)
    except ValueError:
        return None


def work_minutes(card):
    """1回の勤務の 実働・残業（1日8時間をこえた分）・深夜（22〜5時）・夜勤かどうか"""
    start, end = _min(card["clock_in"]), _min(card["clock_out"])
    if start is None or end is None:
        return None
    if end <= start:
        end += 1440
    brk = card["break_min"] or 0
    total = max(0, end - start - brk)
    night = sum(max(0, min(end, b) - max(start, a)) for a, b in NIGHT_WINDOWS)
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


def open_card(staff_id):
    """まだ退勤していない打刻（夜勤なら前の日の分）"""
    since = (date.today() - timedelta(days=1)).isoformat()
    return get_db().execute("SELECT * FROM timecards WHERE staff_id=? AND clock_out IS NULL AND date >= ? ORDER BY date DESC, id DESC",
                            (staff_id, since)).fetchone()


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
    s = {"days": set(), "total": 0, "over": 0, "night": 0, "yakin": 0, "missing": 0}
    for c in month_cards(staff_id, first, last):
        w = work_minutes(c)
        if w is None:
            s["missing"] += 1
            continue
        s["days"].add(c["date"])
        for k in ("total", "over", "night"):
            s[k] += w[k]
        s["yakin"] += 1 if w["yakin"] else 0
    s["days"] = len(s["days"])
    return s


def health_of(staff_id, d):
    return get_db().execute("SELECT * FROM health_checks WHERE staff_id=? AND date=? ORDER BY time", (staff_id, d)).fetchall()


def is_unwell(h):
    return (h["temp"] or 0) >= fever_line() or bool(h["symptoms"])


def _save_health(staff_id, form):
    temp = form.get("temp", type=float)
    symptoms = "、".join(s for s in SYMPTOMS if form.get(f"sym::{s}"))
    if temp is None and not symptoms and not form.get("health_note"):
        return None
    t = datetime.now()
    get_db().execute("INSERT INTO health_checks (staff_id, date, time, temp, symptoms, note, updated_by, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                     (staff_id, t.date().isoformat(), t.strftime("%H:%M"), temp, symptoms, form.get("health_note") or "",
                      g.user["username"], now()))
    return {"temp": temp, "symptoms": symptoms}


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
        t = datetime.now()
        action = request.form.get("action")
        if action == "in" and not card:
            h = _save_health(sid, request.form)
            if h is None and get_setting("pay_health_required", "1") == "1":
                flash("出勤の前に体温を入れてください。", "error")
                return redirect(url_for("work.clock"))
            db.execute("INSERT INTO timecards (staff_id, date, clock_in, note, updated_by, updated_at) VALUES (?,?,?,?,?,?)",
                       (sid, t.date().isoformat(), t.strftime("%H:%M"), request.form.get("note") or "", g.user["username"], now()))
            log_event("clock_in", "timecards", sid, t.strftime("%H:%M"))
            if h and ((h["temp"] or 0) >= fever_line() or h["symptoms"]):
                flash("体調がよくないようです。勤務の前に管理者に連絡してください。ホームの管理者画面にもお知らせが出ます。", "error")
            flash(f"{t:%H:%M} 出勤しました。今日もよろしくお願いします。", "ok")
        elif action == "out" and card:
            out = t.strftime("%H:%M")
            brk = request.form.get("break_min", type=int)
            db.execute("UPDATE timecards SET clock_out=?, break_min=?, updated_by=?, updated_at=? WHERE id=?",
                       (out, default_break(card, out) if brk is None else max(0, brk), g.user["username"], now(), card["id"]))
            log_event("clock_out", "timecards", sid, out)
            flash(f"{out} 退勤しました。おつかれさまでした。", "ok")
        elif action == "health":
            if _save_health(sid, request.form) is not None:
                flash("体温・体調を記録しました。", "ok")
        db.commit()
        return redirect(url_for("work.clock"))
    first, last = parse_ym(None)
    cards = []
    if sid:
        for c in reversed(month_cards(sid, first, last)[-10:]):
            cards.append({"c": c, "w": work_minutes(c)})
    return render_template("work_clock.html", staff=staff, card=card, cards=cards, SYMPTOMS=SYMPTOMS, hm=hm,
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
        for d in month_days(first, last):
            cs = by_day.get(d.isoformat(), [])
            rows.append({"d": d, "cards": [{"c": c, "w": work_minutes(c)} for c in cs], "health": temps.get(d.isoformat())})
    return render_template("work_timecards.html", staff=staff, staff_list=staff_list, rows=rows, ym=ym, first=first, WEEK=WEEK,
                           hm=hm, summary=month_summary(sid, first, last) if staff else None, admin=admin, fever=fever_line())


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
    working = [names.get(c["staff_id"], "?") + f"（{c['clock_in']}〜）" for c in db.execute(
        "SELECT * FROM timecards WHERE clock_out IS NULL AND date >= ? ORDER BY clock_in",
        ((date.today() - timedelta(days=1)).isoformat(),))]
    unwell = [(names.get(h["staff_id"], "?"), h) for h in db.execute("SELECT * FROM health_checks WHERE date=? ORDER BY time", (today,))
              if is_unwell(h)]
    return {"working": working, "unwell": unwell}
