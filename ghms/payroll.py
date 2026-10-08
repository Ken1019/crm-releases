"""給与計算・給与明細・賃金台帳と、事業所の収支。

給与はタイムカード（実働・残業・深夜・夜勤回数）と職員の情報（給与の形・手当・保険の加入）から自動で計算し、
管理者が1項目ずつ直してから「確定」する。「職員に見せる」を押した明細だけ、本人の画面に出る（1人分ずつ）。

保険料率・所得税は概算。料率は毎年変わるので「給与の設定」で直す。所得税は国税庁の
「電子計算機等を使用して源泉徴収税額を計算する方法」（月額）で計算するが、正確な額は源泉徴収税額表で確認すること。
"""

import json
import math
from datetime import date, timedelta
from decimal import ROUND_HALF_DOWN, Decimal, InvalidOperation

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from openpyxl import Workbook

from . import excel
from .auth import admin_required, log_event
from .billing import compute_benefit
from .db import get_db, get_setting, now, set_setting
from .views import parse_date, parse_ym
from .work import month_summary, my_staff_id, setting_num

bp = Blueprint("payroll", __name__, url_prefix="/payroll")

# 給与の設定（キー, 名前, 初期値, 説明）
PAY_SETTINGS = [
    ("pay_monthly_hours", "月の所定労働時間（時間）", "160", "月給の人の残業代・深夜手当の時間単価に使います"),
    ("pay_ot_rate", "時間外の割増（%）", "25", "1日8時間をこえた分"),
    ("pay_yakin_mode", "夜勤の払い方", "1回いくら", "「1回いくら」：夜勤は深夜手当もふくめて1回の金額だけ払う（時給・時間外・深夜の計算に入れない）／「時間で計算」：時給などで計算し、夜勤手当を上乗せ"),
    ("pay_yakin_flat", "夜勤1回の金額（円）", "10000", "職員の情報の「夜勤手当（1回）」が空欄の人に使います"),
    ("pay_min_wage", "最低賃金（時間額・円）", "1131", "北海道 令和8年10月1日からの例（答申額）。都道府県・年度で変わるので厚生労働省・労働局の発表で確認してください。夜勤1回の金額が最低賃金と割増を下回らないかの確認に使います。0にすると確認しません（宿直の許可を受けている場合など）"),
    ("pay_night_start", "深夜手当の時間帯（はじまり）", "22:00", "法律の深夜割増は22時〜翌5時。それより広くするのはかまいません"),
    ("pay_night_end", "深夜手当の時間帯（おわり）", "09:00", "例：09:00（翌朝9時まで）。就業規則・賃金規程と同じにしてください"),
    ("pay_night_rate", "深夜の割増（%）", "25", "上の時間帯に働いた分"),
    ("pay_break_default", "休憩の目安（分）", "60", "6時間をこえる勤務で退勤するときの初期値"),
    ("pay_max_shift_hours", "1回の勤務の上限（時間）", "20", "これをこえると退勤を押せず「退勤忘れ」として管理者が直します（夜勤に合わせて）"),
    ("pay_fever", "体温のお知らせ（℃以上）", "37.5", ""),
    ("pay_leave_method", "有給1日分の賃金（時給・日給の人）", "平均賃金", "「平均賃金」：直近3か月の賃金÷暦日数（最低保障は÷労働日数×60%）／「通常の賃金」：時給×1日の所定時間、日給。就業規則に合わせてください"),
    ("pay_late_grace", "遅刻・早退とみなすずれ（分）", "10", "勤務表の時刻とタイムカードがこの分数よりずれたら、タイムカードと実地指導チェックに出します"),
    ("ins_health", "健康保険料率（%・労使合計）", "10.28", "協会けんぽ北海道 令和8年度の例。上の「都道府県」をえらぶと入ります。毎年3月に変わるので確認してください"),
    ("ins_care", "介護保険料率（%・労使合計）", "1.62", "40〜64歳の人だけ。令和8年度・全国一律"),
    ("ins_kodomo", "子ども・子育て支援金率（%・労使合計）", "0.23", "令和8年4月分の保険料から（5月の給与から引く）。社会保険に入っている人"),
    ("ins_pension", "厚生年金保険料率（%・労使合計）", "18.3", ""),
    ("ins_emp_ee", "雇用保険料率（%・本人）", "0.55", "令和7年度・一般の事業の例"),
    ("ins_emp_er", "雇用保険料率（%・事業所）", "0.9", ""),
    ("ins_child", "子ども・子育て拠出金率（%・事業所）", "0.36", ""),
    ("ins_rosai", "労災保険料率（%・事業所）", "0.3", "社会福祉施設の例"),
    ("pay_day", "支給日（明細に出す）", "翌月25日", ""),
]

EARNINGS = [("base", "基本給"), ("ot", "時間外手当"), ("night", "深夜手当"), ("yakin", "夜勤手当"), ("qual", "資格手当"),
            ("shogu", "処遇改善手当"), ("other", "その他手当"), ("leave", "有給休暇の賃金"), ("commute", "交通費（非課税）")]
DEDUCTIONS = [("health", "健康保険"), ("care", "介護保険"), ("kodomo", "子ども・子育て支援金"), ("pension", "厚生年金"), ("emp", "雇用保険"),
              ("itax", "所得税"), ("rtax", "住民税"), ("other_ded", "その他控除")]
WORK_ITEMS = [("days", "出勤日数", "日"), ("hours", "実働時間", ""), ("ot_h", "時間外", ""), ("night_h", "深夜", ""), ("yakin_n", "夜勤", "回"), ("leave_n", "有給", "日")]


def pset(key):
    return next(d for k, _, d, _ in PAY_SETTINGS if k == key)


def rate(key):
    return setting_num(key, pset(key)) / 100


def pct(key):
    """料率（%）を、小数の誤差が出ないように Decimal で（例 "10.15" → 0.1015）"""
    raw = (get_setting(key, pset(key)) or "").strip()
    try:
        return Decimal(raw) / 100
    except InvalidOperation:
        return Decimal(pset(key)) / 100


def half_down(x):
    """社会保険料の本人負担：50銭以下切り捨て、50銭をこえたら切り上げ（ちょうど50銭は切り捨て）"""
    d = x if isinstance(x, Decimal) else Decimal(repr(float(x)))
    return max(0, int(d.to_integral_value(rounding=ROUND_HALF_DOWN)))


# 健康保険の標準報酬月額の等級（1〜50級）：(報酬月額がこの額以上, 標準報酬月額)
HEALTH_GRADES = [(0, 58000), (63000, 68000), (73000, 78000), (83000, 88000), (93000, 98000), (101000, 104000),
                 (107000, 110000), (114000, 118000), (122000, 126000), (130000, 134000), (138000, 142000), (146000, 150000),
                 (155000, 160000), (165000, 170000), (175000, 180000), (185000, 190000), (195000, 200000), (210000, 220000),
                 (230000, 240000), (250000, 260000), (270000, 280000), (290000, 300000), (310000, 320000), (330000, 340000),
                 (350000, 360000), (370000, 380000), (395000, 410000), (425000, 440000), (455000, 470000), (485000, 500000),
                 (515000, 530000), (545000, 560000), (575000, 590000), (605000, 620000), (635000, 650000), (665000, 680000),
                 (695000, 710000), (730000, 750000), (770000, 790000), (810000, 830000), (855000, 880000), (905000, 930000),
                 (955000, 980000), (1005000, 1030000), (1055000, 1090000), (1115000, 1150000), (1175000, 1210000),
                 (1235000, 1270000), (1295000, 1330000), (1355000, 1390000)]
PENSION_MIN, PENSION_MAX = 88000, 650000


def health_grade(amount):
    """報酬月額 → 健康保険の標準報酬月額"""
    std = HEALTH_GRADES[0][1]
    for lo, v in HEALTH_GRADES:
        if amount >= lo:
            std = v
    return std


def std_monthly(s, gross):
    """標準報酬月額（健康保険・厚生年金）。職員の情報に入っていればそれ、なければ今月の総支給額（交通費こみ）を等級に当てはめる"""
    entered = s["std_monthly"]
    health = int(entered) if entered else health_grade(gross)
    pension = min(PENSION_MAX, max(PENSION_MIN, health))
    return {"health": health, "pension": pension, "source": "職員の情報に入力" if entered else f"今月の総支給額 {gross:,}円から"}


def income_tax(amount, dependents, year):
    """源泉所得税（月額・甲欄）の概算。amount＝社会保険料等を引いた後の給与（交通費を除く）"""
    a = max(0, int(amount))
    if a < 1:
        return 0
    if year >= 2026:  # 令和8年1月以後（給与所得控除の最低額65万円・基礎控除58万円）
        if a <= 158333:
            ded = 54167
        elif a <= 299999:
            ded = math.ceil(a * 0.3 + 6667)
        elif a <= 549999:
            ded = math.ceil(a * 0.2 + 36667)
        elif a <= 708330:
            ded = math.ceil(a * 0.1 + 91667)
        else:
            ded = 162500
        basic = 48334 if a <= 2120833 else 40000 if a <= 2162499 else 26667 if a <= 2204166 else 13334 if a <= 2245833 else 0
    else:
        if a <= 135416:
            ded = 45834
        elif a <= 149999:
            ded = math.ceil(a * 0.4 - 8333)
        elif a <= 299999:
            ded = math.ceil(a * 0.3 + 6667)
        elif a <= 549999:
            ded = math.ceil(a * 0.2 + 36667)
        elif a <= 708330:
            ded = math.ceil(a * 0.1 + 91667)
        else:
            ded = 162500
        basic = 40000 if a <= 2162499 else 26667 if a <= 2204166 else 13334 if a <= 2245833 else 0
    c = a - ded - 31667 * max(0, int(dependents or 0)) - basic
    c = max(0, (c // 1000) * 1000) if c > 0 else 0
    if c <= 0:
        return 0
    for limit, r, sub in [(162500, 0.05105, 0), (275000, 0.1021, 8296), (579166, 0.2042, 36374), (750000, 0.23483, 54113),
                          (1500000, 0.33693, 130688), (3333333, 0.4084, 237893), (float("inf"), 0.45945, 408061)]:
        if c <= limit:
            tax = c * r - sub
            return max(0, int(round(tax / 10.0)) * 10)
    return 0


def _birthday_minus1(b, years):
    """years 歳の誕生日の前日（この日に年齢が上がる。2月29日生まれは2月28日）"""
    try:
        bd = date(b.year + years, b.month, b.day)
    except ValueError:
        bd = date(b.year + years, 3, 1)
    return bd - timedelta(days=1)


def care_applies(birth, last):
    """介護保険料を引く月か：40歳になる日（誕生日の前日）がある月から、65歳になる日がある月の前の月まで"""
    b = parse_date(birth)
    if not b:
        return False
    return _birthday_minus1(b, 40) <= last < _birthday_minus1(b, 65)


def shogu_monthly(staff_id, first):
    fy = first.year if first.month >= 4 else first.year - 1
    row = get_db().execute("SELECT SUM(a.monthly) FROM shogu_allocations a JOIN shogu_plans p ON p.id=a.plan_id "
                           "WHERE a.staff_id=? AND p.fiscal_year=?", (staff_id, fy)).fetchone()
    return int(row[0] or 0)


def compute_pay(s, first, last, earnings=None, leave=True):
    """1人1か月分。earnings を渡すと、その支給額から保険料・税を計算しなおす"""
    sm = month_summary(s["id"], first, last)
    from .leave import leave_day_pay, leave_days_in

    leave_n = leave_days_in(s["id"], first, last)
    hours, over_h, night_h = sm["total"] / 60, sm["over"] / 60, sm["night"] / 60
    flat = (get_setting("pay_yakin_mode", pset("pay_yakin_mode")) or "").startswith("1回")
    na = s["night_allowance"]
    na_blank = na is None or str(na).strip() == ""
    # 職員の情報の「夜勤手当（1回）」が空欄のときだけ設定の金額。0と入れた人は0のまま
    per_yakin = int(setting_num("pay_yakin_flat", pset("pay_yakin_flat")) if na_blank else float(na)) if flat \
        else (0 if na_blank else int(float(na)))
    days_for_pay = sm["days"]
    warnings = []
    if flat:
        # 夜勤は1回の金額だけ（深夜手当こみ）。時間・日数の計算から夜勤の分を外す
        hours, over_h, night_h = (sm["total"] - sm["yk_total"]) / 60, (sm["over"] - sm["yk_over"]) / 60, (sm["night"] - sm["yk_night"]) / 60
        days_for_pay = sm["day_days"]
        mw = setting_num("pay_min_wage", pset("pay_min_wage"))
        for c, w in sm["yk_cards"]:
            need = mw * (w["total"] / 60) + mw * rate("pay_night_rate") * (w["night"] / 60) + mw * rate("pay_ot_rate") * (w["over"] / 60)
            if mw and per_yakin < need:
                warnings.append(f"{c['date'][5:].replace('-', '/')}の夜勤：実働 {w['total'] // 60}時間{w['total'] % 60:02d}分だと、"
                                f"最低賃金と割増で {round(need):,}円 以上が必要です（1回 {per_yakin:,}円）")
    pt = s["pay_type"] or "月給"
    base_salary, hourly, daily = s["base_salary"] or 0, s["hourly_wage"] or 0, s["daily_wage"] or 0
    shogu = shogu_monthly(s["id"], first)
    # 残業代・深夜手当の時間単価には、通勤手当以外の毎月の手当（資格手当・その他手当・処遇改善手当）も入れる
    allowances = (s["allowance_qual"] or 0) + (s["allowance_other"] or 0) + shogu
    if pt == "時給":
        unit = hourly + allowances / max(hours, 1)
    elif pt == "日給":
        unit = daily / 8 + allowances / max(hours, 1)
    else:
        unit = (base_salary + allowances) / (setting_num("pay_monthly_hours", 160) or 160)
    if earnings is None:
        base = round(hourly * hours) if pt == "時給" else round(daily * days_for_pay) if pt == "日給" else int(base_salary)
        ot_factor = rate("pay_ot_rate") + (0 if pt == "時給" else 1)
        ctype = s["commute_type"] or "支給しない"
        commute = (s["commute"] or 0) if ctype == "毎月定額" else (s["commute"] or 0) * sm["days"] if ctype.startswith("1日") else 0
        earnings = {"base": base, "ot": round(unit * over_h * ot_factor), "night": round(unit * night_h * rate("pay_night_rate")),
                    "yakin": per_yakin * sm["yakin"], "qual": int(s["allowance_qual"] or 0),
                    "shogu": shogu, "other": int(s["allowance_other"] or 0), "commute": int(commute),
                    "leave": leave_n * leave_day_pay(s, first) if leave and leave_n else 0}
    gross = sum(int(earnings.get(k) or 0) for k, _ in EARNINGS)
    commute = int(earnings.get("commute") or 0)
    ded = {k: 0 for k, _ in DEDUCTIONS}
    # 労災保険は賃金の総額（交通費もふくむ）
    er = {"health": 0, "care": 0, "kodomo": 0, "pension": 0, "emp": 0, "child": 0, "rosai": round(gross * rate("ins_rosai"))}
    std = None
    if s["social_insurance"]:
        std = std_monthly(s, gross)
        hs, ps = Decimal(std["health"]), Decimal(std["pension"])
        ded["health"] = half_down(hs * pct("ins_health") / 2)
        if care_applies(s["birthdate"], last):
            ded["care"] = half_down(hs * pct("ins_care") / 2)
        ded["pension"] = half_down(ps * pct("ins_pension") / 2)
        if (first.year, first.month) >= (2026, 5):  # 4月分の保険料（5月の給与）から
            ded["kodomo"] = half_down(hs * pct("ins_kodomo") / 2)
        er.update(health=ded["health"], care=ded["care"], pension=ded["pension"], kodomo=ded["kodomo"],
                  child=round(std["pension"] * rate("ins_child")))
    if s["employment_insurance"]:
        ded["emp"] = half_down(Decimal(gross) * pct("ins_emp_ee"))
        er["emp"] = round(gross * rate("ins_emp_er"))
    social = ded["health"] + ded["care"] + ded["kodomo"] + ded["pension"] + ded["emp"]
    ded["itax"] = income_tax(gross - commute - social, s["dependents"], first.year)
    ded["rtax"] = int(s["resident_tax"] or 0)
    total_ded = sum(ded.values())
    return {"earnings": {k: int(earnings.get(k) or 0) for k, _ in EARNINGS}, "deductions": ded,
            "work": {"days": sm["days"], "hours": sm["total"], "ot_h": sm["over"], "night_h": sm["night"], "yakin_n": sm["yakin"],
                     "leave_n": leave_n},
            "missing": sm["missing"], "warnings": warnings, "gross": gross, "total_ded": total_ded, "net": gross - total_ded,
            "employer": er, "employer_total": sum(er.values()), "pay_type": pt, "unit": round(unit), "std": std}


def _totals(data):
    data["gross"] = sum(int(v or 0) for v in data["earnings"].values())
    data["total_ded"] = sum(int(v or 0) for v in data["deductions"].values())
    data["net"] = data["gross"] - data["total_ded"]
    return data


def load_slip(staff_id, ym):
    row = get_db().execute("SELECT * FROM payslips WHERE staff_id=? AND ym=?", (staff_id, ym)).fetchone()
    if not row:
        return None
    data = json.loads(row["data"])
    for k, _ in EARNINGS:
        data.setdefault("earnings", {}).setdefault(k, 0)
    for k, _ in DEDUCTIONS:  # 前に保存した明細には、あとから増えた控除（子ども・子育て支援金など）がない
        data.setdefault("deductions", {}).setdefault(k, 0)
    for k, _, _ in WORK_ITEMS:
        data.setdefault("work", {}).setdefault(k, 0)
    data.update(status=row["status"], updated_by=row["updated_by"], updated_at=row["updated_at"])
    return data


def save_slip(staff_id, ym, data, status):
    _totals(data)
    keep = {k: data[k] for k in ("earnings", "deductions", "work", "gross", "total_ded", "net", "employer", "employer_total",
                                 "pay_type", "unit", "std", "shared", "memo", "warnings") if k in data}
    get_db().execute("INSERT OR REPLACE INTO payslips (staff_id, ym, data, gross, deductions, net, status, updated_by, updated_at)"
                     " VALUES (?,?,?,?,?,?,?,?,?)", (staff_id, ym, json.dumps(keep, ensure_ascii=False), data["gross"],
                                                     data["total_ded"], data["net"], status, g.user["username"], now()))


def payroll_staff(first, last):
    """その月に在籍していた職員。入職日がその月の末日より後の人は出さない（入職日が空欄の人は出す）。
    退職した人は、その月にタイムカードか保存した給与があるときだけ出す"""
    ym = first.strftime("%Y-%m")
    return get_db().execute(
        "SELECT * FROM staff WHERE ((hire_date IS NULL OR hire_date = '' OR hire_date <= ?) AND "
        "(status IS NULL OR status != '退職' OR id IN (SELECT staff_id FROM timecards WHERE date BETWEEN ? AND ?)))"
        " OR id IN (SELECT staff_id FROM payslips WHERE ym = ?) ORDER BY kana, name",
        (last.isoformat(), first.isoformat(), last.isoformat(), ym)).fetchall()


def month_payroll(first, last):
    ym = first.strftime("%Y-%m")
    rows = []
    for s in payroll_staff(first, last):
        slip = load_slip(s["id"], ym)
        rows.append({"s": s, "d": slip or compute_pay(s, first, last), "saved": slip is not None})
    return rows


def hm(minutes):
    return f"{int(minutes) // 60}:{int(minutes) % 60:02d}" if minutes else "0:00"


@bp.route("/", methods=["GET", "POST"])
@admin_required
def index():
    first, last = parse_ym(request.values.get("ym"))
    ym = first.strftime("%Y-%m")
    rows = month_payroll(first, last)
    if request.method == "POST" and request.form.get("action") == "save_all":
        n = 0
        for r in rows:
            if not r["saved"]:
                save_slip(r["s"]["id"], ym, r["d"], "下書き")
                n += 1
        log_event("payroll_save", "payslips", None, f"{ym} {n}人")
        get_db().commit()
        flash(f"{first:%Y年%m月}の給与を{n}人分、下書きとして保存しました。内容を確かめて「確定」してください。", "ok")
        return redirect(url_for("payroll.index", ym=ym))
    total = {k: sum(r["d"][k] for r in rows) for k in ("gross", "total_ded", "net", "employer_total")}
    return render_template("payroll.html", rows=rows, ym=ym, first=first, total=total, hm=hm, EARNINGS=EARNINGS)


@bp.route("/<int:sid>/<ym>", methods=["GET", "POST"])
@admin_required
def edit(sid, ym):
    db = get_db()
    s = db.execute("SELECT * FROM staff WHERE id=?", (sid,)).fetchone() or abort(404)
    first, last = parse_ym(ym)
    ym = first.strftime("%Y-%m")
    slip = load_slip(sid, ym)
    auto = compute_pay(s, first, last)
    if request.method == "POST":
        action = request.form.get("action")
        if slip and slip["status"] == "確定" and action not in ("unlock", "share", "unshare"):
            flash("確定した給与は直せません。直すときは「確定を取り消す」を押してください。", "error")
            return redirect(url_for("payroll.edit", sid=sid, ym=ym))
        if action == "reset":
            db.execute("DELETE FROM payslips WHERE staff_id=? AND ym=?", (sid, ym))
            flash("自動計算に戻しました。", "ok")
        elif action in ("unlock", "share", "unshare"):
            if not slip:
                abort(400)
            if action == "unlock":
                save_slip(sid, ym, dict(slip, shared=False), "下書き")
                flash("確定を取り消しました（職員の画面からも見えなくなります）。", "ok")
            else:
                if slip["status"] != "確定":
                    flash("職員に見せるのは、確定してからです。", "error")
                    return redirect(url_for("payroll.edit", sid=sid, ym=ym))
                save_slip(sid, ym, dict(slip, shared=action == "share"), "確定")
                flash("職員の画面に明細を出しました。" if action == "share" else "職員の画面から明細を外しました。", "ok")
            log_event("payroll_" + action, "payslips", sid, ym)
        else:
            earnings = {k: request.form.get(f"e_{k}", type=int) or 0 for k, _ in EARNINGS}
            if action == "recalc":
                data = compute_pay(s, first, last, earnings)
                flash("支給額から保険料・税を計算しなおしました（まだ保存していません。下の「保存する」を押してください）。", "ok")
                return render_template("payroll_edit.html", s=s, ym=ym, first=first, d=data, auto=auto, slip=slip,
                                       EARNINGS=EARNINGS, DEDUCTIONS=DEDUCTIONS, WORK_ITEMS=WORK_ITEMS, hm=hm, unsaved=True)
            base = slip or auto
            data = dict(base, earnings=earnings, deductions={k: request.form.get(f"d_{k}", type=int) or 0 for k, _ in DEDUCTIONS},
                        memo=request.form.get("memo", ""), shared=False)
            status = "確定" if action == "confirm" else "下書き"
            save_slip(sid, ym, data, status)
            log_event("payroll_" + ("confirm" if status == "確定" else "edit"), "payslips", sid, ym)
            flash(f"{s['name']} さんの{first:%Y年%m月}の給与を{'確定' if status == '確定' else '保存'}しました。", "ok")
        db.commit()
        return redirect(url_for("payroll.edit", sid=sid, ym=ym))
    return render_template("payroll_edit.html", s=s, ym=ym, first=first, d=slip or auto, auto=auto, slip=slip,
                           EARNINGS=EARNINGS, DEDUCTIONS=DEDUCTIONS, WORK_ITEMS=WORK_ITEMS, hm=hm, unsaved=False)


def _slip_view(s, ym, d):
    first, _ = parse_ym(ym)
    return render_template("payroll_slip.html", s=s, first=first, d=d, EARNINGS=EARNINGS, DEDUCTIONS=DEDUCTIONS,
                           WORK_ITEMS=WORK_ITEMS, hm=hm, pay_day=get_setting("pay_day", pset("pay_day")),
                           office={"office_name": get_setting("office_name"), "corp_name": get_setting("corp_name")})


@bp.route("/<int:sid>/<ym>/slip")
@admin_required
def slip(sid, ym):
    s = get_db().execute("SELECT * FROM staff WHERE id=?", (sid,)).fetchone() or abort(404)
    first, last = parse_ym(ym)
    d = load_slip(sid, first.strftime("%Y-%m")) or compute_pay(s, first, last)
    return _slip_view(s, first.strftime("%Y-%m"), d)


@bp.route("/mine")
def mine():
    """職員：自分の給与明細（管理者が「職員に見せる」を押した月だけ）"""
    sid = my_staff_id()
    rows = []
    if sid:
        for r in get_db().execute("SELECT * FROM payslips WHERE staff_id=? AND status='確定' ORDER BY ym DESC", (sid,)):
            if json.loads(r["data"]).get("shared"):
                rows.append(r)
    return render_template("payroll_mine.html", rows=rows, linked=bool(sid))


@bp.route("/mine/<ym>")
def mine_slip(ym):
    sid = my_staff_id()
    d = load_slip(sid, ym) if sid else None
    if not d or d["status"] != "確定" or not d.get("shared"):
        abort(404)
    s = get_db().execute("SELECT * FROM staff WHERE id=?", (sid,)).fetchone()
    log_event("view", "payslips", sid, f"{ym} 本人")
    get_db().commit()
    return _slip_view(s, ym, d)


@bp.route("/ledger.xlsx")
@admin_required
def ledger():
    """賃金台帳（1年分・職員ごとに1枚）"""
    year = request.args.get("year", type=int) or date.today().year
    wb = Workbook()
    wb.remove(wb.active)
    for s in get_db().execute("SELECT * FROM staff ORDER BY kana, name"):
        months = []
        for m in range(1, 13):
            first, last = parse_ym(f"{year}-{m:02d}")
            months.append(load_slip(s["id"], f"{year}-{m:02d}"))
        if not any(months):
            continue
        rows = []
        for group, items in (("work", WORK_ITEMS), ("earnings", EARNINGS), ("deductions", DEDUCTIONS)):
            for k, label, *unit in items:
                vals = [((x or {}).get(group) or {}).get(k, 0) or 0 for x in months]
                if group == "work" and k in ("hours", "ot_h", "night_h"):
                    rows.append([label] + [hm(v) for v in vals] + [hm(sum(vals))])
                else:
                    rows.append([label] + vals + [sum(vals)])
            if group == "earnings":
                vals = [(x or {}).get("gross", 0) for x in months]
                rows.append(["総支給額"] + vals + [sum(vals)])
        vals = [(x or {}).get("total_ded", 0) for x in months]
        rows.append(["控除合計"] + vals + [sum(vals)])
        vals = [(x or {}).get("net", 0) for x in months]
        rows.append(["差引支給額"] + vals + [sum(vals)])
        excel.add_table(wb.create_sheet(excel.safe_sheet_title(s["name"])), f"賃金台帳　{year}年　{s['name']}",
                        ["項目"] + [f"{m}月" for m in range(1, 13)] + ["合計"], rows,
                        subtitle=f"入職日 {s['hire_date'] or ''}　職種 {s['job'] or ''}　雇用形態 {s['employment'] or ''}（確定・下書きで保存した月）",
                        widths=[14] + [10] * 13)
    if not wb.sheetnames:
        wb.create_sheet("なし")
    return excel.send_workbook(wb, f"賃金台帳_{year}")


@bp.route("/settings", methods=["GET", "POST"])
@admin_required
def settings():
    if request.method == "POST" and request.form.get("action") == "prefecture":
        from .presets import apply_prefecture

        done = apply_prefecture(request.form.get("prefecture"), set_setting)
        log_event("settings", "payroll", None, f"都道府県 {request.form.get('prefecture')}")
        get_db().commit()
        flash("入れました：" + "、".join(done) + "。最低賃金は都道府県の発表を見て入れてください。" if done else "都道府県をえらんでください。",
              "ok" if done else "error")
        return redirect(url_for("payroll.settings"))
    if request.method == "POST":
        for k, _, d, _ in PAY_SETTINGS:
            v = (request.form.get(k) or "").strip()
            set_setting(k, v or d)
        set_setting("pay_health_required", "1" if request.form.get("pay_health_required") else "0")
        log_event("settings", "payroll", None, "給与の設定")
        get_db().commit()
        flash("給与の設定を保存しました。", "ok")
        return redirect(url_for("payroll.settings"))
    values = {k: get_setting(k, d) for k, _, d, _ in PAY_SETTINGS}
    from .presets import PREFECTURES, RATE_YEAR

    return render_template("payroll_settings.html", PAY_SETTINGS=PAY_SETTINGS, values=values, PREFECTURES=PREFECTURES,
                           RATE_YEAR=RATE_YEAR, prefecture=get_setting("prefecture", ""),
                           health_required=get_setting("pay_health_required", "1") == "1")


# ---------------------------------------------------------------- 事業所の収支
INVOICE_ITEMS = [("rent", "家賃"), ("food", "食費"), ("utility", "光熱水費"), ("daily_goods", "日用品費"), ("other_amount", "その他")]


def month_profit(first, last):
    db = get_db()
    ym = first.strftime("%Y-%m")
    results, _, _ = compute_benefit(first, last)
    inc = {"給付費（国保連・概算）": sum(x["yen"] - x["burden"] for x in results),
           "利用者負担": sum(x["burden"] for x in results)}
    inv = db.execute("SELECT " + ", ".join(f"SUM({k})" for k, _ in INVOICE_ITEMS) + " FROM invoices WHERE ym=?", (ym,)).fetchone()
    for (k, label), v in zip(INVOICE_ITEMS, inv):
        inc[label] = int(v or 0)
    pay = month_payroll(first, last)
    out = {"給与（総支給）": sum(r["d"]["gross"] for r in pay), "法定福利費（事業所負担の保険料）": sum(r["d"]["employer_total"] for r in pay)}
    for row in db.execute("SELECT kind, SUM(amount) FROM expenses WHERE date BETWEEN ? AND ? GROUP BY kind",
                          (first.isoformat(), last.isoformat())):
        out[row[0] or "その他"] = int(row[1] or 0)
    income, expense = sum(inc.values()), sum(out.values())
    labor = out["給与（総支給）"] + out["法定福利費（事業所負担の保険料）"]
    return {"first": first, "income": inc, "expense": out, "income_total": income, "expense_total": expense,
            "profit": income - expense, "labor": labor, "labor_ratio": round(labor * 100 / income, 1) if income else None,
            "estimated": not all(r["saved"] for r in pay) or not any(inv)}


@bp.route("/profit")
@admin_required
def profit():
    today = date.today()
    fy = request.args.get("fy", type=int) or (today.year if today.month >= 4 else today.year - 1)
    months = []
    for i in range(12):
        y, m = (fy, 4 + i) if i < 9 else (fy + 1, i - 8)
        first, last = parse_ym(f"{y}-{m:02d}")
        if first > today.replace(day=1):
            break
        months.append(month_profit(first, last))
    inc_keys = list(dict.fromkeys(k for x in months for k in x["income"]))
    exp_keys = list(dict.fromkeys(k for x in months for k in x["expense"]))
    return render_template("payroll_profit.html", fy=fy, months=months, inc_keys=inc_keys, exp_keys=exp_keys,
                           inc_tot={k: sum(x["income"].get(k, 0) for x in months) for k in inc_keys},
                           exp_tot={k: sum(x["expense"].get(k, 0) for x in months) for k in exp_keys},
                           total={k: sum(x[k] for x in months) for k in ("income_total", "expense_total", "profit", "labor")})
