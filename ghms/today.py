"""今日のやることリスト。

ホームのいちばん上に、毎日・毎月・毎年の仕事と、規定に足りないものを「急ぐ順」に1つにまとめて出す。
データを見て終わっていれば自動で「済」になる。データでわからないもの（国保連への請求など）は「済」を押す。
このリストを上から片づければ、記録・請求・給与・研修・委員会などの運営の決まりを守れるようにする。
"""

import json
import re
from collections import Counter
from datetime import date, timedelta

from flask import Blueprint, abort, g, redirect, request, url_for

from .auth import is_admin, log_event
from .customize import feature_on
from .db import get_db, get_setting, now

bp = Blueprint("today", __name__, url_prefix="/today")

LEVELS = [("over", "期限切れ・急ぎ"), ("today", "今日"), ("week", "今週"), ("month", "今月"),
          ("tell", "管理者に伝えること")]
# 「管理者に伝えること」は職員が自分では直せないもの。のこりの件数には数えない
NOT_COUNTED = {"tell"}

# 押して「済」にする仕事（データでは終わったかわからないもの）：キー → (名前, 説明, くり返し, 管理者だけ)
# 期間（period）の書き方：毎月 "2026-09"、毎年・年度 "2026"、半年 "2026-H1"（1〜6月）／"2026-H2"（7〜12月）
MANUAL = {
    "kokuho": ("国保連に請求する（10日まで）", "先月分の介護給付費等を電子請求受付システムで送る", "month", True),
    "record_check": ("実績記録票に利用者の確認をもらう", "先月分の記録票を印刷して、本人に確認欄へ記入してもらう", "month", True),
    "user_invoice": ("利用料の請求書をわたす", "家賃・食費などの請求書を本人・家族にわたす（または送る）", "month", True),
    "gensen": ("源泉所得税を納める（毎月10日まで）",
               "前の月に払った給与から天引きした所得税を、納付書で納める。「納期の特例」を受けているときは1月20日・7月10日の年2回",
               "month", True),
    "shogu_plan": ("処遇改善加算の計画書を出す（4月15日ごろまで）",
                   "新しい年度の処遇改善計画書を指定権者（都道府県・市）に出す。締切は通知で確認", "year", True),
    "shogu_report": ("処遇改善加算の実績報告を出す（7月31日まで）", "前の年度に職員へ配った額の実績報告書を出す", "year", True),
    "rodo_hoken": ("労働保険の年度更新（6月1日〜7月10日）", "労災保険・雇用保険の保険料を申告して納める", "year", True),
    "santei": ("社会保険の算定基礎届（7月10日まで）", "4〜6月の給与をもとに、健康保険・厚生年金の標準報酬月額を届け出る", "year", True),
    "kenshin": ("職員の健康診断（年1回・夜勤の人は6か月ごと）",
                "常勤の職員は年1回、夜勤・宿直をする人は6か月ごと（10月ごろにもう1回）。結果は5年保管", "year", True),
    "shobo": ("消防設備の点検（6か月ごと）",
              "消火器・火災報知器・誘導灯などの点検（業者にたのむ）。目安です。消防署・自治体の指導で確認してください", "half", True),
    "kyoryoku": ("協力医療機関との確認（年1回）",
                 "協力医療機関と、急に具合が悪くなったときの対応を確かめ合い、記録を残す。目安です。自治体の指導で確認してください",
                 "year", True),
}
PERIOD_RE = re.compile(r"^\d{4}(-\d{2}|-H[12])?$")
# 今日のやることで自分で出しているので、実地指導チェックからは重ねて出さないもの
COVERED_CHECKS = {"attendance", "payroll"}
DAILY_CHECKS = {"journal", "records"}


def _month(d):
    return d.strftime("%Y-%m")


def _prev_month(d):
    last = d.replace(day=1) - timedelta(days=1)
    return last.replace(day=1), last


def _month_back(d, back):
    """d の月から back か月前の (1日, 月末)"""
    y, m = divmod(d.year * 12 + d.month - 1 - back, 12)
    first = date(y, m + 1, 1)
    return first, (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


def _fy(d):
    return d.year if d.month >= 4 else d.year - 1


def done_marks(period):
    return {r["key"]: r for r in get_db().execute("SELECT * FROM todo_done WHERE period=?", (period,))}


def _pay_day():
    m = re.search(r"(\d+)", get_setting("pay_day", "翌月25日") or "")
    return min(28, int(m.group(1))) if m else 25


def _due_level(today, deadline):
    days = (deadline - today).days
    if days < 0:
        return "over"
    if days <= 3:
        return "today"
    return "week" if days <= 14 else "month"


def roster_state(first, last, active, staff_home):
    """その月の勤務表ができているか → (済, くわしい説明)。
    先月に勤務があった職員が全員1日以上入っていれば済。はじめての月は、住居ごとに1人以上の勤務が入っていれば済"""
    db = get_db()
    pf, pl = _prev_month(first)

    def who(a, b):
        return {r[0] for r in db.execute("SELECT DISTINCT staff_id FROM shifts WHERE date BETWEEN ? AND ?",
                                         (a.isoformat(), b.isoformat()))} & active

    made, before = who(first, last), who(pf, pl)
    if before:
        return before <= made, f"{len(made & before)}/{len(before)}人分（先月に勤務があった職員が、1日以上入っていれば済）"
    homes = {staff_home[i] for i in active if staff_home.get(i)}
    if homes:
        covered = {staff_home[i] for i in made if staff_home.get(i)} & homes
        return homes <= covered, f"{len(covered)}/{len(homes)}住居（はじめての勤務表：住居ごとに1人以上の勤務が入っていれば済）"
    return bool(made), f"{len(made)}人分（はじめての勤務表：1人以上の勤務が入っていれば済）"


def build(user_admin=True, staff_id=None, alerts=None):
    """{groups: [{level, label, items, left}], total, left, done}。項目＝{key, title, detail, url, done, manual, level}
    alerts：ホームのお知らせ（dashboard_alerts）。もう作ってあれば渡す（同じ計算を2回しない）"""
    from .billing import in_residence
    from .compliance import RES_FOR_JOURNAL, before_start, lived_there

    db = get_db()
    today = date.today()
    ts = today.isoformat()
    items = []

    def add(level, title, detail, url, done=False, key=None, manual=False):
        items.append({"level": level, "title": title, "detail": detail, "url": url, "done": done, "key": key, "manual": manual})

    # ---------------- 毎日
    homes = db.execute("SELECT * FROM homes ORDER BY name").fetchall()
    all_homes = homes
    if not user_admin and staff_id:
        # 職員は、自分の「主な勤務住居」が入っていれば、その住居の日誌・支援記録だけ（入っていなければ全部）
        me = db.execute("SELECT home_id FROM staff WHERE id=?", (staff_id,)).fetchone()
        if me and me["home_id"]:
            homes = [h for h in homes if h["id"] == me["home_id"]] or homes
    residents = db.execute(f"SELECT * FROM residents WHERE {RES_FOR_JOURNAL} ORDER BY kana").fetchall()
    codes = {r[0]: r[1] for r in db.execute("SELECT resident_id, code FROM attendance WHERE date=?", (ts,))}
    here = [r for r in residents if lived_there(r, today)]  # 今日、入居している人（入居日〜退居日）

    def is_present(r):
        """今日、住居にいる人（外泊・帰省・入院の人と、入院中で実績がまだない人はのぞく）"""
        c = codes.get(r["id"])
        return c in ("○", "日") if c else r["status"] != "入院中"

    present = [r for r in here if is_present(r)]
    logged = {r[0] for r in db.execute("SELECT home_id FROM daily_logs WHERE date=?", (ts,))}
    recorded = {r[0] for r in db.execute("SELECT DISTINCT resident_id FROM support_records WHERE date=?", (ts,))}
    for h in homes:
        if (h["created_at"] or "")[:10] > ts or not any(r["home_id"] == h["id"] for r in here):
            continue  # 入居者がいない住居は日誌なし
        add("today", f"{h['name']}の業務日誌を書く", "その日の様子・申し送り", url_for("views.journal", home_id=h["id"]),
            done=h["id"] in logged)
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

            mine = db.execute("SELECT 1 FROM timecards WHERE staff_id=? AND date=?", (staff_id, ts)).fetchone()
            add("today", "出勤の打刻と体温", "事務所のPCで名前を押してPIN", url_for("work.clock"), done=bool(mine or open_card(staff_id)))
        if user_admin:
            on = [r[0] for r in db.execute("SELECT DISTINCT staff_id FROM timecards WHERE date=?", (ts,))]
            if on:
                checked = {r[0] for r in db.execute("SELECT DISTINCT staff_id FROM health_checks WHERE date=?", (ts,))}
                names = {s["id"]: s["name"] for s in db.execute("SELECT id, name FROM staff")}
                miss = [names.get(i, "?") for i in on if i not in checked]
                add("today", "出勤した職員の体温を確かめる", ("未記録：" + "、".join(miss)) if miss else "全員記録しています",
                    url_for("work.health"), done=not miss)

    active, staff_home = set(), {}
    if user_admin and feature_on("shift"):
        for s in db.execute("SELECT id, home_id FROM staff WHERE status IS NULL OR status='在籍'"):
            active.add(s["id"])
            staff_home[s["id"]] = s["home_id"]
    if active:
        # 夜勤・宿直：入居者がいる住居ごとに1人以上（職員の「主な勤務住居」で住居を見る）
        present_homes = {r["home_id"] for r in present if r["home_id"]}
        rows = db.execute("SELECT s.staff_id, st.home_id, t.night FROM shifts s LEFT JOIN staff st ON st.id=s.staff_id "
                          "LEFT JOIN shift_types t ON t.code=s.code WHERE s.date=?", (ts,)).fetchall()
        if present_homes and not rows:
            add("today", "今日の勤務表がありません", "勤務表に今日の勤務（夜勤・宿直の人）を入れてください",
                url_for("shift.index", ym=_month(today)))
        elif present_homes:
            nights = [r for r in rows if r["night"]]
            by_home = Counter(r["home_id"] for r in nights if r["home_id"])
            free = sum(1 for r in nights if not r["home_id"])  # 住居が未設定の職員
            lacking = [h["name"] for h in homes if h["id"] in present_homes and not by_home[h["id"]]]
            if not lacking:
                detail, ok = f"入居者がいる住居すべてにいます（{len(nights)}人）", True
            elif free >= len(lacking):
                detail, ok = (f"勤務表で{len(nights)}人（住居が未設定の職員をふくむ。職員の「主な勤務住居」を入れると"
                              "住居ごとに確かめられます）"), True
            else:
                detail, ok = "夜間の勤務者がいない住居：" + "、".join(lacking), False
            add("today", "今夜の夜勤・宿直の人がいるか確かめる", detail, url_for("shift.index", ym=_month(today)), done=ok)

    # ---------------- 毎月・毎年（管理者）
    if user_admin:
        marks = {(r["key"], r["period"]): r for r in db.execute("SELECT * FROM todo_done")}

        def manual(level, key, period, url, title=None, done=None):
            t, dsc, _, _ = MANUAL[key]
            add(level, title or t, dsc, url, done=((key, period) in marks) if done is None else done,
                key=f"{key}:{period}", manual=True)

        pf, pl = _prev_month(today)
        pym = _month(pf)
        older = [_month_back(today, b) for b in (2, 3)]  # 2・3か月前（データがある月だけ見る）
        att_months = {r[0] for r in db.execute("SELECT DISTINCT substr(date,1,7) FROM attendance WHERE date BETWEEN ? AND ?",
                                               (older[-1][0].isoformat(), pl.isoformat()))}
        prev_off = before_start(pl)  # 先月がまるごと使い始める前なら、先月の仕事は出さない
        if feature_on("billing"):
            from .billing import NO_HOME, month_days, residents_in_month

            lvl = "over" if today.day > 10 else ("today" if today.day >= 8 else "week")
            if not prev_off:
                have = {(r[0], r[1]) for r in db.execute("SELECT resident_id, date FROM attendance WHERE date BETWEEN ? AND ?",
                                                         (pf.isoformat(), pl.isoformat()))}
                miss = Counter()
                in_month = residents_in_month(pf, pl)
                for r in in_month:
                    miss[r["home_id"] or NO_HOME] += sum(1 for d in month_days(pf, pl)
                                                         if in_residence(r, d) and (r["id"], d.isoformat()) not in have)
                # 住居ごと（入居者がいた住居だけ）。住居が入っていない方は「住居未設定」
                hids = [h["id"] for h in all_homes if any(r["home_id"] == h["id"] for r in in_month)]
                if any(not r["home_id"] for r in in_month):
                    hids.append(NO_HOME)
                hname = {h["id"]: h["name"] for h in all_homes}
                for hid in hids:
                    n = miss[hid]
                    add(lvl, f"{pf.month}月の実績を全員・全日入れる（{hname.get(hid, '住居未設定')}）",
                        f"あと {n}日分" if n else "入っています", url_for("billing.attendance", ym=pym, home_id=hid), done=not n)
            for f, l in [(pf, pl)] + older:
                ym = _month(f)
                if before_start(l) or (f != pf and ym not in att_months):
                    continue
                for key in ("kokuho", "record_check"):
                    if f != pf and (key, ym) in marks:
                        continue  # 前の月は、済んでいないものだけ出す
                    url = url_for("docs.record_sheets", ym=ym) if key == "record_check" else url_for("billing.benefit", ym=ym)
                    manual("over" if f != pf else (lvl if key == "kokuho" else "month"), key, ym, url,
                           title=f"{f.month}月分：{MANUAL[key][0]}")
        if feature_on("invoices"):
            from .billing import residents_in_month

            n_res = len(residents_in_month(pf, pl))
            n_inv = db.execute("SELECT COUNT(*) FROM invoices WHERE ym=?", (pym,)).fetchone()[0]
            if not prev_off:
                add("week" if today.day <= 15 else "over", f"{pf.month}月分の利用料の請求書を作る",
                    f"{n_inv}/{n_res}名" if n_res else "対象の入居者がいません",
                    url_for("billing.invoices", ym=pym), done=n_inv >= n_res)
            inv_months = {r[0] for r in db.execute("SELECT DISTINCT ym FROM invoices WHERE ym BETWEEN ? AND ?",
                                                   (_month(older[-1][0]), pym))}
            for f, l in [(pf, pl)] + older:
                ym = _month(f)
                if before_start(l) or (f != pf and (ym not in att_months | inv_months or ("user_invoice", ym) in marks)):
                    continue
                manual("month" if f == pf else "over", "user_invoice", ym, url_for("billing.invoices", ym=ym),
                       title=f"{f.month}月分：{MANUAL['user_invoice'][0]}")
        if feature_on("payroll"):
            from .compliance import unconfirmed_payroll

            pay_day = _pay_day()
            for f, _, n_left, n in unconfirmed_payroll():
                if f == pf:
                    lvl = "over" if today.day > pay_day else ("today" if today.day >= pay_day - 3 else "month")
                    add(lvl, f"{pf.month}月分の給与を確定して明細をわたす（{pay_day}日支給）", f"確定 {n - n_left}/{n}人",
                        url_for("payroll.index", ym=pym), done=not n_left)
                elif n_left:
                    add("over", f"{f.month}月分の給与を確定する", f"まだ確定していない職員が {n_left}人います",
                        url_for("payroll.index", ym=_month(f)))
            ym = _month(today)
            if not prev_off:
                # 納付書に書く額は、給与計算の画面のいちばん下（先月分の所得税の合計）
                itax = sum(json.loads(r[0] or "{}").get("deductions", {}).get("itax", 0) or 0 for r in db.execute(
                    "SELECT data FROM payslips WHERE ym=? AND status='確定'", (pym,)))
                manual("over" if today.day > 10 else ("today" if today.day >= 7 else "week"), "gensen", ym,
                       url_for("payroll.index", ym=pym) + "#nofu", title=f"{today.month}月10日まで：源泉所得税を納める")
                if itax:
                    items[-1]["detail"] = f"{pf.month}月分の給与の所得税 合計 {itax:,}円（確定した分）。" + items[-1]["detail"]
            if date(today.year, 6, 1) <= today <= date(today.year, 7, 10):
                manual(_due_level(today, date(today.year, 7, 10)), "rodo_hoken", str(today.year), url_for("payroll.index"))
            if date(today.year, 6, 15) <= today <= date(today.year, 7, 10):
                manual(_due_level(today, date(today.year, 7, 10)), "santei", str(today.year), url_for("payroll.index"))
        if feature_on("shift") and active:
            first = today.replace(day=1)
            nf = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
            ok, detail = roster_state(first, nf - timedelta(days=1), active, staff_home)
            if not ok:
                add("over", f"今月（{today.month}月）の勤務表を作る", detail, url_for("shift.index", ym=_month(today)))
            days_left = (nf - today).days
            if days_left <= 14:
                nl = (nf.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
                ok, detail = roster_state(nf, nl, active, staff_home)
                add("week" if days_left > 3 else "today", f"{nf.month}月の勤務表を作る", detail,
                    url_for("shift.index", ym=_month(nf)), done=ok)
        if feature_on("menus") and homes:
            nmon = today + timedelta(days=7 - today.weekday())
            have = {r[0] for r in db.execute("SELECT DISTINCT home_id FROM menus WHERE date BETWEEN ? AND ?",
                                             (nmon.isoformat(), (nmon + timedelta(days=6)).isoformat()))}
            if today.weekday() >= 3:
                add("week", "来週の献立表を作る", f"{len(have)}/{len(homes)}住居", url_for("docs.menus", week=nmon.isoformat()),
                    done=len(have) >= len(homes))

        # 毎年の仕事（その時期だけ出す）
        home_url = url_for("views.dashboard") + "#today"
        shogu = {r["fiscal_year"]: r for r in db.execute("SELECT * FROM shogu_plans")} if feature_on("shogu") else {}
        shogu_url = url_for("views.shogu_index") if feature_on("shogu") else home_url
        if date(today.year, 3, 15) <= today <= date(today.year, 4, 15):
            p = shogu.get(today.year)
            manual(_due_level(today, date(today.year, 4, 15)), "shogu_plan", str(today.year), shogu_url,
                   title=f"{today.year}年度の{MANUAL['shogu_plan'][0]}",
                   done=bool(p and p["plan_submitted"]) or ("shogu_plan", str(today.year)) in marks)
        if date(today.year, 7, 1) <= today <= date(today.year, 7, 31):
            p = shogu.get(today.year - 1)
            manual(_due_level(today, date(today.year, 7, 31)), "shogu_report", str(today.year - 1), shogu_url,
                   title=f"{today.year - 1}年度の{MANUAL['shogu_report'][0]}",
                   done=bool(p and p["report_submitted"]) or ("shogu_report", str(today.year - 1)) in marks)
        if today.month == 4:
            manual(_due_level(today, date(today.year, 4, 30)), "kenshin", str(today.year), url_for("crud.index", key="staff"))

        # いつやってもよいが、期間内に1回やるもの：済んでいなければ出す（済んだら7日だけ「済」で見せる）
        def recent_or_left(key, period):
            m = marks.get((key, period))
            return not m or (m["done_at"] or "")[:10] >= (today - timedelta(days=7)).isoformat()

        half = f"{today.year}-H{1 if today.month <= 6 else 2}"
        if recent_or_left("shobo", half):
            end = date(today.year, 6, 30) if today.month <= 6 else date(today.year, 12, 31)
            manual(_due_level(today, end), "shobo", half, home_url)
        fy = str(_fy(today))
        if recent_or_left("kyoryoku", fy):
            manual(_due_level(today, date(_fy(today) + 1, 3, 31)), "kyoryoku", fy,
                   url_for("crud.new", key="contact_logs", counterpart="医療機関・病院") if feature_on("contact_logs") else home_url,
                   title=f"{fy}年度：{MANUAL['kyoryoku'][0]}")

    # ---------------- 規定に足りないもの（実地指導チェック）
    from .compliance import LEAVE5_EXPIRED, STAFF_CANT_FIX, run_checks

    found = run_checks(staff_id=None if user_admin else (staff_id or -1), include_done=True)
    groups = {}
    for x in found:
        if user_admin and x["check"] in COVERED_CHECKS:
            continue  # 上の「毎月」で出している
        expired = x["check"] == "leave5" and LEAVE5_EXPIRED in x["msg"]
        groups.setdefault((x["check"], expired), []).append(x)
    for (key, expired), xs in groups.items():
        pending = [x for x in xs if not x["done"]]
        ng = any(x["level"] == "ng" for x in (pending or xs))
        tell = not user_admin and key in STAFF_CANT_FIX
        if tell:
            lv = "tell"
        elif expired:
            lv = "month"
        else:
            lv = "over" if ng else ("week" if key in DAILY_CHECKS else "month")
        pre = "管理者に連絡：" if tell else ""
        if len(xs) == 1:
            add(lv, f"{xs[0]['name']}：{xs[0]['who']}", pre + xs[0]["msg"], xs[0]["url"], done=not pending)
        else:
            who = "、".join(dict.fromkeys(x["who"] for x in (pending or xs)))
            add(lv, f"{xs[0]['name']}（{len(pending or xs)}件）", pre + (who if len(who) < 70 else who[:70] + "…"),
                url_for("compliance.index") + f"#c-{key}" if user_admin else xs[0]["url"], done=not pending)
    # 受給者証・計画などの期限（ホームのお知らせと同じもの）
    if user_admin:
        if alerts is None:
            from .views import dashboard_alerts

            alerts = dashboard_alerts()
        by_kind = {}
        for kind, name, d, url in alerts:
            by_kind.setdefault(kind, []).append((name, d, url))
        for kind, xs in by_kind.items():
            over = any((not d) or d < ts for _, d, _ in xs)
            soon = any(d and d <= (today + timedelta(days=14)).isoformat() for _, d, _ in xs)
            lv = "over" if over else ("week" if soon else "month")
            if len(xs) == 1:
                name, d, url = xs[0]
                add(lv, f"{kind}：{name}", f"期限 {d}" if d else "すぐに対応してください", url)
            else:
                add(lv, f"{kind}（{len(xs)}名）", "、".join(f"{n}（{d}）" if d else n for n, d, _ in xs[:5]) + (" ほか" if len(xs) > 5 else ""),
                    xs[0][2])

    out = []
    for lv, label in LEVELS:
        xs = [x for x in items if x["level"] == lv]
        if xs:
            out.append({"level": lv, "label": label, "items": sorted(xs, key=lambda x: x["done"]),
                        "left": sum(1 for x in xs if not x["done"]), "counted": lv not in NOT_COUNTED})
    counted = [x for x in items if x["level"] not in NOT_COUNTED]
    total = len(counted)
    left = sum(1 for x in counted if not x["done"])
    return {"groups": out, "total": total, "left": left, "done": total - left}


@bp.route("/done", methods=["POST"])
def mark():
    """押して「済」にする（もう一度押すと取り消し）"""
    key = request.form.get("key", "")
    name, _, period = key.partition(":")
    if name not in MANUAL or not PERIOD_RE.match(period):
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
