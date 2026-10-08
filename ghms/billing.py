"""請求・お金：月ごとの実績（在居・外泊・入院など）、給付費の概算、利用料の請求書、預り金。

給付費・利用者負担額はあくまで概算です。国保連への請求は請求ソフト（電子請求受付システム等）で行い、
その結果を正としてください。ここでの概算は、請求前の確認（実績の入れ忘れ・加算の漏れの発見）に使います。
"""

import calendar
from datetime import date, timedelta
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal, InvalidOperation

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from openpyxl import Workbook

from . import excel
from .auth import admin_required
from .crud import COMPUTE, audit, save
from .db import get_db, get_setting, now
from .forms import clamp, db_int, safe_float
from .views import fiscal_year, parse_date, parse_ym


def sync_open():
    from .absences import sync_open as _s

    _s()


class InputError(Exception):
    """保存の前に見つかった入力の誤り。画面に戻してメッセージを出す"""

bp = Blueprint("billing", __name__, url_prefix="/billing")

# 実績の記号
CODES = [
    ("○", "在居（ふつうに過ごした）"),
    ("日", "在居＋日中支援（日中も住居で支援）"),
    ("外", "外泊"),
    ("帰", "帰宅（家族のもとへ帰省）"),
    ("入", "入院"),
]
BILLABLE = {"○", "日"}


def month_days(first, last):
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


NO_HOME = 0  # 「住居未設定」をえらんだとき（住居が入っていない入居者）


def residents_in_month(first, last, home_id=None):
    """その月に1日でも入居していた入居者（home_id=0 なら住居が未設定の方だけ）"""
    sql = ("SELECT r.*, h.name AS hname, h.home_type FROM residents r LEFT JOIN homes h ON h.id=r.home_id "
           "WHERE (r.move_in IS NULL OR r.move_in <= ?) AND (r.move_out IS NULL OR r.move_out >= ?) "
           "AND (r.status IS NULL OR r.status != '退居' OR r.move_out >= ?)")
    params = [last.isoformat(), first.isoformat(), first.isoformat()]
    if home_id == NO_HOME:
        sql += " AND r.home_id IS NULL"
    elif home_id:
        sql += " AND r.home_id = ?"
        params.append(home_id)
    return get_db().execute(sql + " ORDER BY h.name, r.room, r.kana", params).fetchall()


def pick_home(homes):
    """画面でえらんだ住居。何もえらんでいなければ最初の住居（住居がなければ「住居未設定」）"""
    home_id = request.values.get("home_id", type=db_int)
    if home_id is None:
        home_id = homes[0]["id"] if homes else NO_HOME
    return home_id


def no_home_count():
    """住居が入っていない入居中の方の人数（「住居未設定」をえらべるようにするため）"""
    return get_db().execute("SELECT COUNT(*) FROM residents WHERE home_id IS NULL AND (status IS NULL OR status != '退居')"
                            ).fetchone()[0]


def in_residence(r, d):
    mi, mo = parse_date(r["move_in"]), parse_date(r["move_out"])
    return (mi is None or mi <= d) and (mo is None or d <= mo)


def residence_days(r, first, last):
    return sum(1 for d in month_days(first, last) if in_residence(r, d))


def attendance_map(first, last, ids=None):
    rows = get_db().execute("SELECT * FROM attendance WHERE date BETWEEN ? AND ?", (first.isoformat(), last.isoformat()))
    return {(a["resident_id"], a["date"]): a["code"] for a in rows if ids is None or a["resident_id"] in ids}


def unit_price():
    """1単位の単価。小数の誤差が出ないよう Decimal で扱う（10.45 など）"""
    try:
        from .forms import normalize_num

        v = Decimal(normalize_num(get_setting("unit_price", "10")) or "10")
        return v if v.is_finite() and 1 <= v <= 100 else Decimal("10")
    except (InvalidOperation, ValueError):
        return Decimal("10")


def units_to_yen(units, price):
    """単位数 × 単価。1円未満は切り捨て（180単位 × 10.45円 = 1,881円）"""
    return int((Decimal(str(units)) * Decimal(str(price))).to_integral_value(rounding=ROUND_FLOOR))


def shogu_units(subtotal, rate):
    """処遇改善加算の単位数。1単位未満は四捨五入"""
    return int((Decimal(str(subtotal)) * Decimal(str(rate or 0)) / 100).to_integral_value(rounding=ROUND_HALF_UP))


def _num(v):
    return v or 0


# ---------------------------------------------------------------- 実績（在居・外泊・入院）の入力
@bp.route("/attendance", methods=["GET", "POST"])
def attendance():
    db = get_db()
    first, last = parse_ym(request.values.get("ym"))
    ym = first.strftime("%Y-%m")
    homes = db.execute("SELECT * FROM homes ORDER BY name").fetchall()
    home_id = pick_home(homes)
    residents = residents_in_month(first, last, home_id)
    days = month_days(first, last)
    if request.method == "GET":
        sync_open()
    valid = {c for c, _ in CODES}
    if request.method == "POST":
        for r in residents:
            old = {a["date"]: a for a in db.execute("SELECT * FROM attendance WHERE resident_id=? AND date BETWEEN ? AND ?",
                                                    (r["id"], first.isoformat(), last.isoformat()))}
            for d in days:
                key = d.isoformat()
                code = request.form.get(f"a{r['id']}_{d.day}", "")
                if not in_residence(r, d) or code not in valid:
                    # 入居前・退居後の日は実績を持たない。空欄にした日は消す
                    if key in old:
                        db.execute("DELETE FROM attendance WHERE resident_id=? AND date=?", (r["id"], key))
                    continue
                prev = old.get(key)
                if prev is not None and prev["code"] == code:
                    continue  # 変えていない日はそのまま（入院・外泊の登録から入った日は、その登録のものとして残す）
                db.execute("INSERT OR REPLACE INTO attendance (resident_id, date, code, updated_by, updated_at) VALUES (?,?,?,?,?)",
                           (r["id"], key, code, g.user["username"], now()))
        db.commit()
        flash(f"{first:%Y年%m月}の実績を保存しました。", "ok")
        return redirect(url_for("billing.attendance", ym=ym, home_id=home_id))
    amap = attendance_map(first, last)
    rows = []
    for r in residents:
        cells = [(d, amap.get((r["id"], d.isoformat()), "") if in_residence(r, d) else "", in_residence(r, d)) for d in days]
        counts = {c: sum(1 for _, v, _ in cells if v == c) for c, _ in CODES}
        rows.append({"r": r, "cells": cells, "counts": counts})
    return render_template("billing_attendance.html", ym=ym, first=first, homes=homes, home_id=home_id, rows=rows,
                           days=days, CODES=CODES, WEEK="月火水木金土日", no_home=no_home_count())


# ---------------------------------------------------------------- 給付費の概算
def _basic_units(r):
    rows = get_db().execute(
        "SELECT * FROM basic_units WHERE active=1 AND support_level=? ORDER BY home_type IS NULL", (r["support_level"],)
    ).fetchall()
    for b in rows:
        if b["home_type"] in (None, "", r["home_type"]):
            return b
    return None


def _shogu_rate(first):
    row = get_db().execute("SELECT rate FROM shogu_plans WHERE fiscal_year=? ORDER BY id DESC LIMIT 1",
                           (fiscal_year(first),)).fetchone()
    return (row["rate"] or 0) if row else 0


def _present(c):
    """在居として数える日（○・日、または実績が未入力の在居日）"""
    return c in BILLABLE or c == ""


def _addon_days(name, codes, on):
    """加算の日数。codes は在居期間外が None、on はその日が加算の対象期間か"""
    days = [c for c, ok in zip(codes, on) if ok and c is not None]
    if "日中支援" in name:
        return sum(1 for c in days if c == "日")
    if "帰宅" in name:
        return sum(1 for c in days if c == "帰")
    if "入院" in name:
        return sum(1 for c in days if c == "入")
    return sum(1 for c in days if _present(c))


def _in_range(d, start, end):
    s, e = parse_date(start), parse_date(end)
    return (s is None or s <= d) and (e is None or d <= e)


def compute_benefit(first, last, home_id=None):
    from .docs import mark_of, marks_map, record_columns

    db = get_db()
    sync_open()
    residents = residents_in_month(first, last, home_id)
    days = month_days(first, last)
    amap = attendance_map(first, last)
    price, rate = unit_price(), _shogu_rate(first)
    addons = db.execute("SELECT * FROM addons WHERE active=1 AND name NOT LIKE '%処遇改善%'").fetchall()
    # 回数で数える加算は、実績記録票の同じ名前の項目のチェックを数える
    cols = {c["label"]: c for c in record_columns()}
    mmap = marks_map(first, last)
    results = []
    for r in residents:
        # 在居期間外の日は None（実績が入っていても数えない）。在居中で未入力の日は ""
        day_codes = [amap.get((r["id"], d.isoformat()), "") if in_residence(r, d) else None for d in days]
        codes = [c or "" for c in day_codes]
        missing = sum(1 for c in day_codes if c == "")
        billable = sum(1 for c in day_codes if c is not None and _present(c))
        entered = missing == 0
        lines, warnings = [], []
        if missing:
            warnings.append(f"実績が未入力の日が{missing}日あります（在居として計算）")
        b = _basic_units(r)
        if b:
            lines.append((f"共同生活援助サービス費（{b['label'] or r['support_level']}）", b["units"], billable, "日"))
        else:
            warnings.append("基本報酬の単位数が未設定（区分：%s）" % (r["support_level"] or "未入力"))
        mine = {}
        for ra in db.execute("SELECT addon_id, start_on, end_on FROM resident_addons WHERE resident_id=?", (r["id"],)):
            mine.setdefault(ra["addon_id"], []).append((ra["start_on"], ra["end_on"]))
        for a in addons:
            individual = (a["kind"] or "").startswith("個別")
            if individual and a["id"] not in mine:
                continue
            # 加算の対象になる日：加算の算定開始日以降、かつ（利用者ごとの加算なら）開始日〜終了日のあいだ
            on = [_in_range(d, a["start_on"], None)
                  and (not individual or any(_in_range(d, s, e) for s, e in mine[a["id"]])) for d in days]
            if not any(on):
                continue
            if not a["units"]:
                warnings.append(f"「{a['name']}」の単位数が未入力")
                continue
            if a["unit_type"] == "回":
                col = cols.get(a["name"])
                if col is None:
                    warnings.append(f"「{a['name']}」：回数の加算は自動で数えていません（実績記録票に同じ名前の項目を作ると数えます）")
                    continue
                n = sum(1 for d, c, ok in zip(days, day_codes, on)
                        if ok and c is not None and mark_of(col, c, mmap.get((r["id"], d.isoformat(), col["id"]))))
                if n:
                    lines.append((a["name"], a["units"], n, "回"))
                continue
            n = _addon_days(a["name"], day_codes, on)
            if a["unit_type"] == "日" and n:
                lines.append((a["name"], a["units"], n, "日"))
            elif a["unit_type"] == "月" and n:
                lines.append((a["name"], a["units"], 1, "月"))
        subtotal = sum(int(Decimal(str(u)) * n) for _, u, n, _ in lines)
        shogu = shogu_units(subtotal, rate)
        if shogu:
            lines.append((f"福祉・介護職員等処遇改善加算（{rate}%）", None, None, "率"))
        total_units = subtotal + shogu
        yen = units_to_yen(total_units, price)
        burden = yen // 10
        if r["burden_cap"] is not None:
            burden = min(burden, int(r["burden_cap"]))
        elif r["income_class"] in ("生活保護", "低所得"):
            burden = 0
        else:
            warnings.append("利用者負担上限月額が未入力")
        results.append({"r": r, "codes": codes, "billable": billable, "entered": entered, "missing": missing,
                        "lines": lines, "subtotal": subtotal, "shogu": shogu, "total_units": total_units, "yen": yen,
                        "burden": burden, "warnings": warnings})
    return results, price, rate


@bp.route("/benefit")
def benefit():
    first, last = parse_ym(request.args.get("ym"))
    homes = get_db().execute("SELECT * FROM homes ORDER BY name").fetchall()
    home_id = request.args.get("home_id", type=db_int)
    results, price, rate = compute_benefit(first, last, home_id)
    return render_template("billing_benefit.html", results=results, price=price, rate=rate, first=first,
                           ym=first.strftime("%Y-%m"), homes=homes, home_id=home_id,
                           total_yen=sum(x["yen"] for x in results), total_burden=sum(x["burden"] for x in results))


@bp.route("/benefit.xlsx")
def benefit_export():
    first, last = parse_ym(request.args.get("ym"))
    home_id = request.args.get("home_id", type=db_int)
    results, price, rate = compute_benefit(first, last, home_id)
    office = get_setting("office_name")
    wb = Workbook()
    ws = wb.active
    ws.title = "給付費の概算"
    excel.add_table(ws, f"給付費の概算　{first:%Y年%m月}",
                    ["氏名", "受給者証番号", "障害支援区分", "算定日数", "単位数（処遇改善前）", "処遇改善加算", "合計単位", "給付費概算（円）", "利用者負担（概算）", "注意"],
                    [[x["r"]["name"], x["r"]["recipient_no"] or "", x["r"]["support_level"] or "", x["billable"], x["subtotal"],
                      x["shogu"], x["total_units"], x["yen"], x["burden"], "／".join(x["warnings"])] for x in results],
                    subtitle=f"{office}　1単位 {price}円　※概算です。請求は請求ソフトの結果を正としてください。",
                    widths=[14, 14, 10, 8, 14, 12, 10, 14, 14, 40])
    days = month_days(first, last)
    for x in results:
        r = x["r"]
        ws2 = wb.create_sheet(excel.safe_sheet_title(f"実績_{r['name']}"))
        rows = [[f"{d.month}/{d.day}", "月火水木金土日"[d.weekday()], code, dict(CODES).get(code, "")] for d, code in zip(days, x["codes"])]
        end = excel.add_table(ws2, f"サービス提供実績記録（共同生活援助）　{first:%Y年%m月}", ["日付", "曜日", "記号", "内容"], rows,
                              subtitle=f"{office}　利用者：{r['name']}　受給者証番号：{r['recipient_no'] or ''}　支給決定市町村：{r['municipality'] or ''}",
                              widths=[8, 6, 6, 34])
        ws2.cell(row=end + 2, column=1, value=f"算定日数：{x['billable']}日　外泊：{x['codes'].count('外')}日　帰宅：{x['codes'].count('帰')}日　入院：{x['codes'].count('入')}日")
        ws2.cell(row=end + 4, column=1, value="利用者確認欄：")
    return excel.send_workbook(wb, f"給付費概算_{first:%Y%m}")


# ---------------------------------------------------------------- 利用料の請求
def invoice_total(data):
    data["total"] = (_num(data.get("rent")) - _num(data.get("rent_subsidy")) + _num(data.get("food")) + _num(data.get("utility"))
                     + _num(data.get("daily_goods")) + _num(data.get("user_burden")) + _num(data.get("other_amount")))


def normalize_ym(v):
    """「2026-9」「2026/09」を「2026-09」にそろえる。月として読めなければ None"""
    parts = str(v or "").strip().replace("/", "-").replace("年", "-").replace("月", "").split("-")
    try:
        y, m = (int(x) for x in parts if x != "")
    except ValueError:
        return None
    if not (2000 <= y <= 2100 and 1 <= m <= 12):
        return None
    return f"{y:04d}-{m:02d}"


def invoice_compute(data):
    if "ym" in data:
        ym = normalize_ym(data.get("ym"))
        if ym is None:
            raise InputError("「請求月」は 2026-09 のように「年-月」で入力してください。")
        data["ym"] = ym
    invoice_total(data)


COMPUTE["invoices"] = invoice_compute


@bp.app_errorhandler(InputError)
def _input_error(e):
    """保存前に誤りが見つかったら、入力した内容のままフォームに戻す"""
    from . import crud

    flash(str(e), "error")
    args = request.view_args or {}
    key = args.get("key")
    if request.method == "POST" and request.endpoint in ("crud.new", "crud.edit") and key:
        ent = crud.get_entity(key)
        values, _ = crud.parse_form(ent, request.form)
        return render_template("crud_form.html", key=key, ent=ent, values=values, refs=crud._form_context(ent),
                               rid=args.get("rid"), next=request.form.get("_next", ""))
    return redirect(request.referrer or url_for("views.dashboard"))


def calc_fees(r, first, last, billable, burden):
    dim = (last - first).days + 1
    rdays = residence_days(r, first, last)
    ratio = rdays / dim if dim else 0

    def pro(v):
        return int(_num(v) * ratio)

    if (r["food_type"] or "").startswith("日額"):
        food = int(_num(r["food_amount"]) * billable)
    else:
        food = pro(r["food_amount"])
    data = {"rent": pro(r["rent"]), "rent_subsidy": min(pro(r["rent_subsidy"]), pro(r["rent"])), "food": food,
            "utility": pro(r["utility"]), "daily_goods": pro(r["daily_goods"]), "user_burden": burden,
            "other_label": None, "other_amount": None}
    invoice_total(data)
    return data, rdays


def due_date(first):
    y, m = (first.year + 1, 1) if first.month == 12 else (first.year, first.month + 1)
    from .forms import setting_number

    day = setting_number("invoice_due_day", 27)
    return date(y, m, min(max(day, 1), calendar.monthrange(y, m)[1]))


@bp.route("/invoices", methods=["GET", "POST"])
@admin_required
def invoices():
    db = get_db()
    first, last = parse_ym(request.values.get("ym"))
    ym = first.strftime("%Y-%m")
    results, _, _ = compute_benefit(first, last)
    existing = {i["resident_id"]: i for i in db.execute("SELECT * FROM invoices WHERE ym=?", (ym,))}
    preview = []
    for x in results:
        fees, rdays = calc_fees(x["r"], first, last, x["billable"], x["burden"])
        preview.append({"x": x, "fees": fees, "rdays": rdays, "inv": existing.get(x["r"]["id"])})
    if request.method == "POST" and request.form.get("action") == "save":
        # 毎月の金額を表でまとめて入力 → 請求を作る・直す（入金済みは変えない）
        made = updated = 0
        for p in preview:
            rid, inv = p["x"]["r"]["id"], p["inv"]
            if inv and inv["status"] == "入金済":
                continue
            amounts = {}
            for f in MONTHLY_FIELDS:
                raw = request.form.get(f"r{rid}_{f}", "").replace(",", "").strip()
                if f == "other_label":
                    amounts[f] = raw or None
                else:
                    # 読めない・とても大きい値は0にする（±1億円の間におさめる）
                    amounts[f] = int(clamp(safe_float(raw, 0), -100_000_000, 100_000_000)) if raw else 0
            if inv:
                save("invoices", amounts, inv["id"])
                updated += 1
            else:
                save("invoices", dict(amounts, ym=ym, resident_id=rid, status="未請求", issue_date=date.today().isoformat(),
                                      due_date=due_date(first).isoformat(), pay_method=p["x"]["r"]["pay_method"]))
                made += 1
        flash(f"{first:%Y年%m月}分を保存しました（新しく作成 {made}件・更新 {updated}件）。", "ok")
        return redirect(url_for("billing.invoices", ym=ym))
    if request.method == "POST":
        made = 0
        for p in preview:
            if p["inv"]:
                continue  # 作成済みの請求は上書きしない（手で直した内容を守る）
            data = dict(p["fees"], ym=ym, resident_id=p["x"]["r"]["id"], status="未請求", issue_date=date.today().isoformat(),
                        due_date=due_date(first).isoformat(), pay_method=p["x"]["r"]["pay_method"], paid_on=None,
                        paid_amount=None, notes=None)
            save("invoices", data)
            made += 1
        flash(f"{made}件の請求を作りました。" if made else "新しく作る請求はありませんでした（作成済みです）。", "ok")
        return redirect(url_for("billing.invoices", ym=ym))
    total = sum((p["inv"]["total"] if p["inv"] else p["fees"]["total"]) or 0 for p in preview)
    unpaid = db.execute("SELECT i.*, r.name AS rname FROM invoices i JOIN residents r ON r.id=i.resident_id "
                        "WHERE i.status != '入金済' AND i.ym < ? ORDER BY i.ym, r.kana", (ym,)).fetchall()
    prev_first = (first - timedelta(days=1)).replace(day=1)
    prev = {i["resident_id"]: dict(i) for i in db.execute("SELECT * FROM invoices WHERE ym=?", (prev_first.strftime("%Y-%m"),))}
    return render_template("billing_invoices.html", ym=ym, first=first, preview=preview, total=total, unpaid=unpaid,
                           missing=sum(1 for p in preview if not p["inv"]), prev=prev, prev_first=prev_first,
                           FIELDS=MONTHLY_FIELDS)


# 毎月の表で入力する項目（家賃助成は差し引く）
MONTHLY_FIELDS = ["rent", "rent_subsidy", "food", "utility", "daily_goods", "user_burden", "other_label", "other_amount"]


INVOICE_LINES = [("rent", "家賃"), ("rent_subsidy", "家賃助成（差引）"), ("food", "食費"), ("utility", "光熱水費"),
                 ("daily_goods", "日用品費"), ("user_burden", "利用者負担額（障害福祉サービス）")]


@bp.route("/invoice/<int:iid>/print")
@admin_required
def invoice_print(iid):
    inv = get_db().execute("SELECT i.*, r.name AS rname, r.guardian FROM invoices i JOIN residents r ON r.id=i.resident_id "
                           "WHERE i.id=?", (iid,)).fetchone()
    if inv is None:
        abort(404)
    kind = "receipt" if request.args.get("kind") == "receipt" else "invoice"
    lines = [(label, -(inv[k] or 0) if k == "rent_subsidy" else (inv[k] or 0)) for k, label in INVOICE_LINES if inv[k]]
    if inv["other_amount"]:
        lines.append((inv["other_label"] or "その他", inv["other_amount"]))
    first, _ = parse_ym(inv["ym"])
    return render_template("billing_invoice_print.html", inv=inv, lines=lines, kind=kind, first=first,
                           s={k: get_setting(k) for k in ("office_name", "office_no", "office_address", "office_tel", "bank_info")},
                           today=date.today())


@bp.route("/invoices.xlsx")
@admin_required
def invoices_export():
    first, _ = parse_ym(request.args.get("ym"))
    ym = first.strftime("%Y-%m")
    rows = get_db().execute("SELECT i.*, r.name AS rname FROM invoices i JOIN residents r ON r.id=i.resident_id "
                            "WHERE i.ym=? ORDER BY r.kana", (ym,)).fetchall()
    data = [[i["rname"], i["rent"] or 0, -(i["rent_subsidy"] or 0), i["food"] or 0, i["utility"] or 0, i["daily_goods"] or 0,
             i["user_burden"] or 0, i["other_amount"] or 0, i["total"] or 0, i["status"] or "", i["paid_on"] or "",
             i["paid_amount"] or ""] for i in rows]
    data.append(["合計"] + [sum(r[c] for r in data) for c in range(1, 9)] + ["", "", ""])
    return excel.send_table(f"利用料請求一覧_{first:%Y%m}",
                            ["氏名", "家賃", "家賃助成", "食費", "光熱水費", "日用品費", "利用者負担", "その他", "請求額", "状態", "入金日", "入金額"],
                            data, subtitle=f"{get_setting('office_name')}　{first:%Y年%m月}分")


# ---------------------------------------------------------------- 預り金
def deposit_balances():
    rows = get_db().execute(
        "SELECT r.id, r.name, r.status, SUM(CASE WHEN d.kind='入金' THEN d.amount ELSE -d.amount END) AS bal,"
        " MAX(d.date) AS last, SUM(CASE WHEN d.kind='出金' AND (d.receipt IS NULL OR d.receipt=0) THEN 1 ELSE 0 END) AS noreceipt"
        " FROM residents r JOIN deposits d ON d.resident_id=r.id GROUP BY r.id ORDER BY r.kana").fetchall()
    return rows


@bp.route("/deposits")
def deposits():
    return render_template("billing_deposits.html", rows=deposit_balances())


@bp.route("/deposits/<int:rid>.xlsx")
def deposit_ledger(rid):
    db = get_db()
    r = db.execute("SELECT * FROM residents WHERE id=?", (rid,)).fetchone()
    if r is None:
        abort(404)
    bal, data = 0, []
    for d in db.execute("SELECT * FROM deposits WHERE resident_id=? ORDER BY date, id", (rid,)):
        amt = d["amount"] or 0
        bal += amt if d["kind"] == "入金" else -amt
        data.append([d["date"], d["purpose"] or "", amt if d["kind"] == "入金" else "", amt if d["kind"] == "出金" else "",
                     bal, "✓" if d["receipt"] else "", d["staff"] or "", d["checker"] or ""])
    return excel.send_table(f"預り金出納帳_{r['name']}", ["日付", "内容", "入金", "出金", "残高", "レシート", "対応職員", "確認者"], data,
                            subtitle=f"{get_setting('office_name')}　{r['name']} 様")


# ---------------------------------------------------------------- 契約書・同意書がそろっているか
REQUIRED_DOCS = ["利用契約書", "重要事項説明書", "個人情報使用同意書", "個別支援計画への同意", "受給者証の写し", "緊急連絡先届"]


def required_docs():
    """そろえる書類の一覧。「選択肢を変える」で決めた書類の種類（使うもの）にあわせる。
    はじめから入っている種類のうち、全員に必要とは限らないもの（金銭管理の契約・その他など）は除く"""
    from .customize import choice_rows
    from .entities import DOC_TYPE

    optional = set(DOC_TYPE) - set(REQUIRED_DOCS)
    try:
        rows = choice_rows("resident_documents.doc_type")
    except Exception:  # 選択肢の表がまだないときは、はじめの一覧
        rows = []
    types = [r["value"] for r in rows if r["active"] and r["value"] not in optional]
    return types or REQUIRED_DOCS


@bp.route("/documents")
def documents():
    db = get_db()
    residents = db.execute("SELECT * FROM residents WHERE status IS NULL OR status != '退居' ORDER BY kana").fetchall()
    docs = {}
    # 同じ種類が何枚もあるときは、いちばん新しいもの（署名日、なければ登録日の順。同じなら後に登録したもの）
    for d in db.execute("SELECT * FROM resident_documents ORDER BY COALESCE(signed_on, created_at), id"):
        docs[(d["resident_id"], d["doc_type"])] = d
    today = date.today().isoformat()
    return render_template("billing_documents.html", residents=residents, docs=docs, types=required_docs(), today=today)
