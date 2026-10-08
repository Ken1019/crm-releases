"""年次有給休暇（労働基準法39条）。

- 付与：入職から6か月、その後は1年ごと。日数は勤続年数と、前の期間の出勤日数（タイムカード）を年に直した数で決める
  （初回は入職から6か月、2回目からは前の1年）。タイムカードが3か月分に満たないときは、職員の情報の「有給の区分」を使う
- 取得：勤務表の「有」（有給休暇）の日。古い付与から順に使い、付与から2年で時効
- 1日分の賃金：設定で「平均賃金（直近3か月）」か「通常の賃金」をえらぶ。月給の人は休んでも基本給はそのまま
- 年5日の取得義務：10日以上付与した人は、付与から1年以内に5日。実地指導チェックに出す

出勤率8割の要件は自動では見ていない（満たさないときは付与の行を消すか日数を直す）。
"""

from datetime import date, timedelta

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from .auth import admin_required, log_event
from .db import get_db, get_setting, now
from .views import parse_date

bp = Blueprint("leave", __name__, url_prefix="/leave")

# 勤続 0.5, 1.5, 2.5, 3.5, 4.5, 5.5, 6.5年以上
FULL = [10, 11, 12, 14, 16, 18, 20]
PART = {4: [7, 8, 10, 11, 12, 13, 15], 3: [5, 6, 6, 8, 9, 10, 11], 2: [3, 4, 4, 5, 6, 6, 7], 1: [1, 2, 2, 2, 3, 3, 3]}
CLASSES = ["通常（週5日以上・週30時間以上）", "週4日", "週3日", "週2日", "週1日"]
LEAVE_CODE = "有"


def add_months(d, n):
    y, m = divmod(d.month - 1 + n, 12)
    y += d.year
    m += 1
    for day in (d.day, 30, 29, 28):
        try:
            return date(y, m, day)
        except ValueError:
            continue


def grant_dates(hire, until):
    out = []
    if not hire:
        return out
    d, i = add_months(hire, 6), 0
    while d <= until:
        out.append((i, d))
        i += 1
        d = add_months(hire, 6 + 12 * i)
    return out


def _worked_days(staff_id, start, end):
    return get_db().execute("SELECT COUNT(DISTINCT date) FROM timecards WHERE staff_id=? AND date >= ? AND date < ? "
                            "AND clock_out IS NOT NULL", (staff_id, start.isoformat(), end.isoformat())).fetchone()[0]


def _first_card(staff_id):
    row = get_db().execute("SELECT MIN(date) FROM timecards WHERE staff_id=?", (staff_id,)).fetchone()
    return parse_date(row[0]) if row and row[0] else None


def days_for(s, index, gdate):
    """付与する日数と、その決め方の説明"""
    step = min(index, 6)
    if (s["weekly_hours"] or 0) >= 30:
        return FULL[step], "週30時間以上"
    start = parse_date(s["hire_date"]) if index == 0 else add_months(gdate, -12)
    first_card = _first_card(s["id"])
    covered_from = max(start, first_card) if first_card else None
    if covered_from and (gdate - covered_from).days >= 90:
        worked = _worked_days(s["id"], covered_from, gdate)
        per_year = round(worked * 365 / max(1, (gdate - covered_from).days))
        basis = f"出勤 {worked}日（年に直すと {per_year}日）"
        if per_year >= 217:
            return FULL[step], basis
        for wd, lo in ((4, 169), (3, 121), (2, 73), (1, 48)):
            if per_year >= lo:
                return PART[wd][step], basis + f"・週{wd}日相当"
        return 0, basis + "・年48日未満のため付与なし"
    cls = s["leave_class"] or CLASSES[0]
    if cls.startswith("通常"):
        return FULL[step], "登録の区分（通常）"
    wd = int(cls[1])
    return PART[wd][step], f"登録の区分（{cls}）"


def ensure_grants(s):
    """入職日から今日までの付与を、まだなければ作る（手で直した行はそのまま）"""
    db = get_db()
    hire = parse_date(s["hire_date"])
    have = {r["grant_date"] for r in db.execute("SELECT grant_date FROM leave_grants WHERE staff_id=?", (s["id"],))}
    made = 0
    for i, gd in grant_dates(hire, date.today()):
        if gd.isoformat() in have:
            continue
        n, basis = days_for(s, i, gd)
        db.execute("INSERT INTO leave_grants (staff_id, grant_date, days, basis, auto, updated_by, updated_at) VALUES (?,?,?,?,1,?,?)",
                   (s["id"], gd.isoformat(), n, basis, "自動", now()))
        made += 1
    return made


def taken_dates(staff_id, until=None):
    sql, args = "SELECT date FROM shifts WHERE staff_id=? AND code=?", [staff_id, LEAVE_CODE]
    if until:
        sql += " AND date <= ?"
        args.append(until.isoformat())
    return sorted(parse_date(r[0]) for r in get_db().execute(sql + " ORDER BY date", args))


def balance(s, on=None):
    """付与ごとの使った日数・残り（古い付与から使う。2年で時効）"""
    on = on or date.today()
    grants = [dict(r) for r in get_db().execute("SELECT * FROM leave_grants WHERE staff_id=? ORDER BY grant_date", (s["id"],))]
    for gr in grants:
        gr["gd"] = parse_date(gr["grant_date"])
        gr["expires"] = add_months(gr["gd"], 24)
        gr["used"] = 0.0
    unassigned = []
    for d in taken_dates(s["id"]):
        pool = [gr for gr in grants if gr["gd"] <= d < gr["expires"] and gr["used"] < (gr["days"] or 0)]
        if pool:
            pool[0]["used"] += 1
        else:
            unassigned.append(d)
    remaining = sum((gr["days"] or 0) - gr["used"] for gr in grants if gr["gd"] <= on < gr["expires"])
    expiring = sum((gr["days"] or 0) - gr["used"] for gr in grants if gr["gd"] <= on < gr["expires"] <= on + timedelta(days=90))
    # 年5日：10日以上付与した日から1年間に取った日数
    five = []
    for gr in grants:
        if (gr["days"] or 0) >= 10:
            end = add_months(gr["gd"], 12)
            took = sum(1 for d in taken_dates(s["id"]) if gr["gd"] <= d < end)
            five.append({"grant": gr["gd"], "deadline": end, "took": took})
    nxt = next((gd for _, gd in grant_dates(parse_date(s["hire_date"]), on + timedelta(days=400)) if gd > on), None)
    return {"grants": grants, "remaining": remaining, "expiring": expiring, "unassigned": unassigned, "five": five,
            "next": nxt, "taken": len(taken_dates(s["id"]))}


def leave_days_in(staff_id, first, last):
    return len([d for d in taken_dates(staff_id) if first <= d <= last])


def leave_day_pay(s, first):
    """有給1日分の賃金。月給の人は0（基本給をそのまま払う）"""
    pt = s["pay_type"] or "月給"
    if pt == "月給":
        return 0
    method = get_setting("pay_leave_method", "平均賃金") or "平均賃金"
    if method == "通常の賃金":
        if pt == "日給":
            return int(s["daily_wage"] or 0)
        hours = (s["weekly_hours"] or 0) / 5 or 8
        return round((s["hourly_wage"] or 0) * hours)
    # 平均賃金：直近3か月の賃金の総額 ÷ 暦日数。最低保障は 総額 ÷ 労働日数 × 60%
    from .payroll import compute_pay, load_slip
    from .views import parse_ym

    total = worked = cal = 0
    m = first
    for _ in range(3):
        m = (m.replace(day=1) - timedelta(days=1)).replace(day=1)
        f, l = parse_ym(m.strftime("%Y-%m"))
        d = load_slip(s["id"], m.strftime("%Y-%m")) or compute_pay(s, f, l, leave=False)
        total += d["gross"]  # 平均賃金の「賃金の総額」には通勤手当も入る
        worked += d["work"]["days"]
        cal += (l - f).days + 1
    if not total:
        return 0
    return round(max(total / cal, (total / worked * 0.6) if worked else 0))


@bp.route("/")
@admin_required
def index():
    db = get_db()
    rows = []
    for s in db.execute("SELECT * FROM staff WHERE status IS NULL OR status != '退職' ORDER BY kana, name"):
        ensure_grants(s)
        rows.append({"s": s, "b": balance(s)})
    db.commit()
    return render_template("leave.html", rows=rows, today=date.today())


@bp.route("/<int:sid>", methods=["GET", "POST"])
@admin_required
def staff(sid):
    db = get_db()
    s = db.execute("SELECT * FROM staff WHERE id=?", (sid,)).fetchone() or abort(404)
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            gd = parse_date(request.form.get("grant_date"))
            n = request.form.get("days", type=float)
            if gd and n is not None:
                db.execute("INSERT INTO leave_grants (staff_id, grant_date, days, basis, auto, updated_by, updated_at)"
                           " VALUES (?,?,?,?,0,?,?)", (sid, gd.isoformat(), n, request.form.get("basis") or "手で追加",
                                                      g.user["username"], now()))
                flash("付与を追加しました。", "ok")
        elif action == "save":
            for r in db.execute("SELECT * FROM leave_grants WHERE staff_id=?", (sid,)).fetchall():
                if request.form.get(f"del_{r['id']}"):
                    db.execute("DELETE FROM leave_grants WHERE id=?", (r["id"],))
                    continue
                n = request.form.get(f"days_{r['id']}", type=float)
                if n is not None and n != r["days"]:
                    db.execute("UPDATE leave_grants SET days=?, basis=?, auto=0, updated_by=?, updated_at=? WHERE id=?",
                               (n, (r["basis"] or "") + "（手で直した）", g.user["username"], now(), r["id"]))
            flash("有給の付与を保存しました。", "ok")
        log_event("leave_edit", "leave_grants", sid, action)
        db.commit()
        return redirect(url_for("leave.staff", sid=sid))
    ensure_grants(s)
    db.commit()
    return render_template("leave_staff.html", s=s, b=balance(s), taken=taken_dates(sid))


def my_balance(staff_id):
    s = get_db().execute("SELECT * FROM staff WHERE id=?", (staff_id,)).fetchone()
    if not s or not s["hire_date"]:
        return None
    ensure_grants(s)
    get_db().commit()
    return balance(s)
