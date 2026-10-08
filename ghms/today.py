"""今日のやることリスト。

ホームのいちばん上に、毎日・毎月の仕事と、規定に足りないものを「急ぐ順」に1つにまとめて出す。
データを見て終わっていれば自動で「済」になる。データでわからないもの（国保連への請求など）は「済」を押す。
このリストを上から片づければ、記録・請求・給与・研修・委員会などの運営の決まりを守れるようにする。
"""

from datetime import date, timedelta

from flask import Blueprint, abort, g, redirect, request, url_for

from .auth import is_admin, log_event
from .customize import feature_on
from .db import get_db, get_setting, now

bp = Blueprint("today", __name__, url_prefix="/today")

ACTIVE_RES = "status IS NULL OR status != '退居'"
LEVELS = [("over", "期限切れ・急ぎ"), ("today", "今日"), ("week", "今週"), ("month", "今月")]

# 押して「済」にする仕事（データでは終わったかわからないもの）：キー → (名前, 説明, いつ, 管理者だけ)
MANUAL = {
    "kokuho": ("国保連に請求する（10日まで）", "先月分の介護給付費等を電子請求受付システムで送る", "month", True),
    "record_check": ("実績記録票に利用者の確認をもらう", "先月分の記録票を印刷して、本人に確認欄へ記入してもらう", "month", True),
    "user_invoice": ("利用料の請求書をわたす", "家賃・食費などの請求書を本人・家族にわたす（または送る）", "month", True),
}


def _month(d):
    return d.strftime("%Y-%m")


def _prev_month(d):
    last = d.replace(day=1) - timedelta(days=1)
    return last.replace(day=1), last


def done_marks(period):
    return {r["key"]: r for r in get_db().execute("SELECT * FROM todo_done WHERE period=?", (period,))}


def _pay_day():
    import re

    m = re.search(r"(\d+)", get_setting("pay_day", "翌月25日") or "")
    return min(28, int(m.group(1))) if m else 25


def build(user_admin=True, staff_id=None):
    """[(区分, [項目])] 。項目＝{key, title, detail, url, done, manual, level}"""
    db = get_db()
    today = date.today()
    items = []

    def add(level, title, detail, url, done=False, key=None, manual=False):
        items.append({"level": level, "title": title, "detail": detail, "url": url, "done": done, "key": key, "manual": manual})

    # ---------------- 毎日
    homes = db.execute("SELECT * FROM homes ORDER BY name").fetchall()
    logged = {r[0] for r in db.execute("SELECT home_id FROM daily_logs WHERE date=?", (today.isoformat(),))}
    for h in homes:
        add("today", f"{h['name']}の業務日誌を書く", "その日の様子・申し送り", url_for("views.journal", home_id=h["id"]),
            done=h["id"] in logged)
    present = []
    codes = {r[0]: r[1] for r in db.execute("SELECT resident_id, code FROM attendance WHERE date=?", (today.isoformat(),))}
    recorded = {r[0] for r in db.execute("SELECT DISTINCT resident_id FROM support_records WHERE date=?", (today.isoformat(),))}
    for r in db.execute(f"SELECT * FROM residents WHERE {ACTIVE_RES} ORDER BY kana"):
        if codes.get(r["id"], "○") in ("○", "日"):
            present.append(r)
    for h in homes:
        hs = [r for r in present if r["home_id"] == h["id"]]
        if hs:
            left = [r["name"] for r in hs if r["id"] not in recorded]
            add("today", f"{h['name']}の入居者の支援記録を書く（{len(hs)}名）",
                ("まだ：" + "、".join(left[:6]) + (" ほか" if len(left) > 6 else "")) if left else "全員書きました",
                url_for("views.journal", home_id=h["id"]), done=not left)
    if feature_on("timecard"):
        if staff_id:
            from .work import open_card

            mine = db.execute("SELECT 1 FROM timecards WHERE staff_id=? AND date=?", (staff_id, today.isoformat())).fetchone()
            add("today", "出勤の打刻と体温", "事務所のPCで名前を押してPIN", url_for("work.clock"), done=bool(mine or open_card(staff_id)))
        if user_admin:
            on = [r[0] for r in db.execute("SELECT DISTINCT staff_id FROM timecards WHERE date=?", (today.isoformat(),))]
            checked = {r[0] for r in db.execute("SELECT DISTINCT staff_id FROM health_checks WHERE date=?", (today.isoformat(),))}
            names = {s["id"]: s["name"] for s in db.execute("SELECT id, name FROM staff")}
            miss = [names.get(i, "?") for i in on if i not in checked]
            if on:
                add("today", "出勤した職員の体温を確かめる", ("未記録：" + "、".join(miss)) if miss else "全員記録しています",
                    url_for("work.health"), done=not miss)
    if feature_on("shift") and user_admin:
        night = db.execute("SELECT COUNT(*) FROM shifts s JOIN shift_types t ON t.code=s.code WHERE s.date=? AND t.night=1",
                           (today.isoformat(),)).fetchone()[0]
        has_roster = db.execute("SELECT COUNT(*) FROM shifts WHERE date=?", (today.isoformat(),)).fetchone()[0]
        if has_roster:
            add("today", "今夜の夜勤・宿直の人がいるか確かめる", f"勤務表で{night}人" if night else "勤務表に夜間の勤務者がいません",
                url_for("shift.index"), done=night > 0)

    # ---------------- 毎月（管理者）
    if user_admin:
        pf, pl = _prev_month(today)
        pym = _month(pf)
        marks = done_marks(pym)
        if feature_on("billing"):
            from .billing import in_residence, month_days, residents_in_month

            have = {(r[0], r[1]) for r in db.execute("SELECT resident_id, date FROM attendance WHERE date BETWEEN ? AND ?",
                                                     (pf.isoformat(), pl.isoformat()))}
            miss = sum(1 for r in residents_in_month(pf, pl) for d in month_days(pf, pl)
                       if in_residence(r, d) and (r["id"], d.isoformat()) not in have)
            lvl = "over" if today.day > 10 else ("today" if today.day >= 8 else "week")
            add(lvl, f"{pf.month}月の実績を全員・全日入れる", f"あと {miss}日分" if miss else "入っています",
                url_for("billing.attendance", ym=pym), done=not miss)
            for key in ("kokuho", "record_check"):
                t, dsc, _, _ = MANUAL[key]
                add(lvl if key == "kokuho" else "month", f"{pf.month}月分：{t}", dsc,
                    url_for("docs.record_sheets", ym=pym) if key == "record_check" else url_for("billing.benefit", ym=pym),
                    done=key in marks, key=f"{key}:{pym}", manual=True)
        if feature_on("invoices"):
            n_res = len(__import__("ghms.billing", fromlist=["x"]).residents_in_month(pf, pl))
            n_inv = db.execute("SELECT COUNT(*) FROM invoices WHERE ym=?", (pym,)).fetchone()[0]
            add("week" if today.day <= 15 else "over", f"{pf.month}月分の利用料の請求書を作る", f"{n_inv}/{n_res}名",
                url_for("billing.invoices", ym=pym), done=n_res > 0 and n_inv >= n_res)
            t, dsc, _, _ = MANUAL["user_invoice"]
            add("month", f"{pf.month}月分：{t}", dsc, url_for("billing.invoices", ym=pym), done="user_invoice" in marks,
                key=f"user_invoice:{pym}", manual=True)
        if feature_on("payroll"):
            worked = {r[0] for r in db.execute("SELECT DISTINCT staff_id FROM timecards WHERE substr(date,1,7)=?", (pym,))}
            fixed = {r[0] for r in db.execute("SELECT staff_id FROM payslips WHERE ym=? AND status='確定'", (pym,))}
            pay_day = _pay_day()
            lvl = "over" if today.day > pay_day else ("today" if today.day >= pay_day - 3 else "month")
            if worked:
                add(lvl, f"{pf.month}月分の給与を確定して明細をわたす（{pay_day}日支給）", f"確定 {len(worked & fixed)}/{len(worked)}人",
                    url_for("payroll.index", ym=pym), done=worked <= fixed)
        if feature_on("shift"):
            nf = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
            nstaff = db.execute("SELECT COUNT(*) FROM staff WHERE status IS NULL OR status='在籍'").fetchone()[0]
            made = db.execute("SELECT COUNT(DISTINCT staff_id) FROM shifts WHERE substr(date,1,7)=?", (_month(nf),)).fetchone()[0]
            days_left = (nf - today).days
            if days_left <= 14:
                add("week" if days_left > 3 else "today", f"{nf.month}月の勤務表を作る", f"{made}/{nstaff}人分",
                    url_for("shift.index", ym=_month(nf)), done=nstaff > 0 and made >= nstaff)
        if feature_on("menus") and homes:
            nmon = today + timedelta(days=7 - today.weekday())
            have = {r[0] for r in db.execute("SELECT DISTINCT home_id FROM menus WHERE date BETWEEN ? AND ?",
                                             (nmon.isoformat(), (nmon + timedelta(days=6)).isoformat()))}
            if today.weekday() >= 3:
                add("week", "来週の献立表を作る", f"{len(have)}/{len(homes)}住居", url_for("docs.menus", week=nmon.isoformat()),
                    done=len(have) >= len(homes))

    # ---------------- 規定に足りないもの（実地指導チェック）
    from .compliance import run_checks

    # 同じ種類は1行にまとめる（人数が多くても上から片づけられるように）
    daily_keys = {"journal", "records"}  # 今日の分は上で出しているので、過去の書きもれだけ
    found = run_checks(staff_id=None if user_admin else (staff_id or -1))
    for key in dict.fromkeys(x["check"] for x in found):
        xs = [x for x in found if x["check"] == key]
        ng = any(x["level"] == "ng" for x in xs)
        lv = "over" if ng else ("week" if key in daily_keys else "month")
        if len(xs) == 1:
            add(lv, f"{xs[0]['name']}：{xs[0]['who']}", xs[0]["msg"], xs[0]["url"])
        else:
            who = "、".join(dict.fromkeys(x["who"] for x in xs))
            add(lv, f"{xs[0]['name']}（{len(xs)}件）", who if len(who) < 70 else who[:70] + "…",
                url_for("compliance.index") + f"#c-{key}" if user_admin else xs[0]["url"])
    # 受給者証・計画などの期限（ホームのお知らせと同じもの）
    if user_admin:
        from .views import dashboard_alerts

        by_kind = {}
        for kind, name, d, url in dashboard_alerts():
            by_kind.setdefault(kind, []).append((name, d, url))
        for kind, xs in by_kind.items():
            over = any((not d) or d < today.isoformat() for _, d, _ in xs)
            soon = any(d and d <= (today + timedelta(days=14)).isoformat() for _, d, _ in xs)
            lv = "over" if over else ("week" if soon else "month")
            if len(xs) == 1:
                name, d, url = xs[0]
                add(lv, f"{kind}：{name}", f"期限 {d}" if d else "すぐに対応してください", url)
            else:
                add(lv, f"{kind}（{len(xs)}名）", "、".join(f"{n}（{d}）" if d else n for n, d, _ in xs[:5]) + (" ほか" if len(xs) > 5 else ""),
                    xs[0][2])

    groups = []
    for lv, label in LEVELS:
        xs = [x for x in items if x["level"] == lv]
        if xs:
            groups.append({"level": lv, "label": label, "items": sorted(xs, key=lambda x: x["done"]),
                           "left": sum(1 for x in xs if not x["done"])})
    total = len(items)
    left = sum(1 for x in items if not x["done"])
    return {"groups": groups, "total": total, "left": left, "done": total - left}


@bp.route("/done", methods=["POST"])
def mark():
    """押して「済」にする（もう一度押すと取り消し）"""
    key = request.form.get("key", "")
    name, _, period = key.partition(":")
    if name not in MANUAL or not period:
        abort(400)
    if MANUAL[name][3] and not is_admin():
        abort(403)
    db = get_db()
    if db.execute("SELECT 1 FROM todo_done WHERE key=? AND period=?", (name, period)).fetchone():
        db.execute("DELETE FROM todo_done WHERE key=? AND period=?", (name, period))
        log_event("todo_undo", "todo", None, key)
    else:
        db.execute("INSERT INTO todo_done (key, period, done_by, done_at) VALUES (?,?,?,?)", (name, period, g.user["username"], now()))
        log_event("todo_done", "todo", None, key)
    db.commit()
    return redirect(url_for("views.dashboard") + "#today")
