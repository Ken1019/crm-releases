"""entities.py の定義から一覧・登録・編集・削除・Excel出力を共通処理で提供する。"""

import math
from datetime import date

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from . import excel
from .auth import is_admin, require_admin
from .customize import allowed_values, entity_on, options_for
from .db import get_db, now
from .entities import ENTITIES
from .forms import DB_INT_MAX, NUM_MAX, clamp, db_int

bp = Blueprint("crud", __name__, url_prefix="/m")

PAGE_SIZE = 100

# 保存前に値を計算するフック {entity_key: fn(data) -> None}
COMPUTE = {}
# 保存後に関連データを更新するフック {entity_key: fn(rid) -> None}
AFTER_SAVE = {}
# 削除後に関連データを片付けるフック {entity_key: fn(rid) -> None}
AFTER_DELETE = {}
# 一覧・選択肢での表示名 {entity_key: fn(row) -> str}
LABELS = {}


def get_entity(key):
    ent = ENTITIES.get(key)
    if ent is None:
        abort(404)
    if ent.get("admin_only"):
        require_admin()
    return ent


def label_of(key, row):
    if row is None:
        return ""
    if key == "shogu_plans":
        return f'{row["fiscal_year"] and int(row["fiscal_year"])}年度 区分{row["category"] or ""}'
    if key == "career_grades":
        return f'{row["name"]}（{row["position"] or ""}）'
    if key in LABELS:
        return LABELS[key](row)
    if key in ("resident_addons", "shogu_allocations"):
        return f"#{row['id']}"
    v = row[ENTITIES[key]["display"]]
    return "" if v is None else str(v)


def ref_options(key):
    ent = ENTITIES[key]
    rows = get_db().execute(f'SELECT * FROM "{key}" ORDER BY {ent["order"]}').fetchall()
    return [(r["id"], label_of(key, r)) for r in rows]


def ref_maps(ent):
    return {f["name"]: dict(ref_options(f["ref"])) for f in ent["fields"] if f["type"] in ("ref", "multiref")}


def multiref_ids(value):
    return [int(x) for x in str(value or "").split(",") if x.strip().isdigit()]


def fmt(field, value, maps):
    if value is None or value == "":
        return ""
    t = field["type"]
    if t == "ref":
        return maps.get(field["name"], {}).get(value, f"(#{value})")
    if t == "multiref":
        m = maps.get(field["name"], {})
        return "、".join(m.get(int(i), f"(#{i})") for i in multiref_ids(value))
    if t == "check":
        return "✓" if value else ""
    if t == "number":
        return f"{value:,.0f}" if float(value).is_integer() and abs(value) >= 1000 else (
            str(int(value)) if float(value).is_integer() else str(value))
    return str(value)


def query_rows(key, ent, args, limit=None, offset=0):
    where, params = [], []
    q = args.get("q", "").strip()
    if q:
        text_cols = [f["name"] for f in ent["fields"] if f["type"] in ("text", "textarea")]
        if text_cols:
            where.append("(" + " OR ".join(f'"{c}" LIKE ?' for c in text_cols) + ")")
            params += [f"%{q}%"] * len(text_cols)
    for f in ent["fields"]:
        v = args.get(f["name"])
        if v not in (None, "") and f["type"] == "multiref":
            where.append(f'"{f["name"]}" LIKE ?')
            params.append(f"%,{int(v) if str(v).isdigit() else 0},%")
        elif v not in (None, "") and f["type"] in ("ref", "select", "number", "check", "date"):
            where.append(f'"{f["name"]}" = ?')
            params.append(v)
    # 期間で絞り込むのは、一覧に表示している最初の日付項目（記録日など）
    date_field = next((f["name"] for f in ent["fields"] if f["type"] == "date" and f.get("list")), None)
    if date_field and args.get("from"):
        where.append(f'"{date_field}" >= ?')
        params.append(args["from"])
    if date_field and args.get("to"):
        where.append(f'"{date_field}" <= ?')
        params.append(args["to"])
    sql = f'FROM "{key}"' + (" WHERE " + " AND ".join(where) if where else "")
    db = get_db()
    total = db.execute("SELECT COUNT(*) " + sql, params).fetchone()[0]
    sql = f'SELECT * {sql} ORDER BY {ent["order"]}'
    if limit:
        sql += f" LIMIT {int(limit)} OFFSET {int(offset)}"
    return db.execute(sql, params).fetchall(), total, date_field


def parse_form(ent, form):
    data, errors = {}, []
    for f in ent["fields"]:
        if f["type"] == "hidden":
            continue
        raw = form.get(f["name"], "")
        raw = raw.strip() if isinstance(raw, str) else raw
        if f["type"] == "check":
            data[f["name"]] = 1 if raw else 0
            continue
        if f["type"] == "multiref":
            ids = sorted({int(x) for x in form.getlist(f["name"]) if str(x).isdigit()}) if hasattr(form, "getlist") else []
            data[f["name"]] = "," + ",".join(map(str, ids)) + "," if ids else None
            if f.get("required") and not ids:
                errors.append(f'「{f["label"]}」をえらんでください。')
            continue
        if raw == "":
            if f.get("required"):
                errors.append(f'「{f["label"]}」は必須です。')
            data[f["name"]] = None
            continue
        if f["type"] in ("number", "ref"):
            try:
                n = float(raw.replace(",", ""))
                if not math.isfinite(n):
                    raise ValueError("not finite")
            except ValueError:
                errors.append(f'「{f["label"]}」は数値で入力してください。')
                data[f["name"]] = raw
                continue
            if f["type"] == "ref" and not (n.is_integer() and 0 < n < DB_INT_MAX):
                errors.append(f'「{f["label"]}」の値が正しくありません。')
                data[f["name"]] = None
            elif f["type"] == "number" and abs(n) > NUM_MAX:
                errors.append(f'「{f["label"]}」の数が大きすぎます。')
                data[f["name"]] = raw
            else:
                data[f["name"]] = int(n) if f["type"] == "ref" or n.is_integer() else n
        elif f["type"] == "select" and (f.get("options") or f.get("choices")) and raw not in allowed_values(f):
            errors.append(f'「{f["label"]}」の値が不正です。')
        else:
            data[f["name"]] = raw
    return data, errors


def safe_next(url):
    """リダイレクト先は同一サイト内のパスに限定する"""
    from .auth import is_safe_path

    return url if is_safe_path(url) else None


# 開いただけでも記録を残す（個人情報・給与など）
VIEW_LOGGED = {"residents", "support_plans", "support_records", "contact_logs", "absences", "incidents",
               "resident_documents", "deposits", "invoices", "staff", "evaluations", "shogu_allocations"}


def audit(action, key, rid):
    from .auth import log_event

    log_event(action, key, rid)


def save(key, data, rid=None):
    if key in COMPUTE:
        COMPUTE[key](data)
    db = get_db()
    cols = list(data)
    if rid is None:
        cur = db.execute(
            f'INSERT INTO "{key}" ({", ".join(chr(34) + c + chr(34) for c in cols)}, created_at, updated_at, updated_by)'
            f' VALUES ({", ".join("?" for _ in cols)}, ?, ?, ?)',
            [data[c] for c in cols] + [now(), now(), g.user["username"]],
        )
        rid = cur.lastrowid
        audit("create", key, rid)
    else:
        db.execute(
            f'UPDATE "{key}" SET {", ".join(chr(34) + c + chr(34) + "=?" for c in cols)}, updated_at=?, updated_by=? WHERE id=?',
            [data[c] for c in cols] + [now(), g.user["username"], rid],
        )
        audit("update", key, rid)
    db.commit()
    if key in AFTER_SAVE:
        AFTER_SAVE[key](rid)
        db.commit()
    return rid


# 一覧の表（ENTITIES）ではない記録からの参照。職員を消すと、タイムカードや給与明細が次の職員に引き継がれてしまうため
EXTRA_REFS = {
    "staff": [("timecards", "staff_id", "タイムカード"), ("payslips", "staff_id", "給与明細"), ("leave_grants", "staff_id", "有給休暇"),
              ("shifts", "staff_id", "勤務表"), ("health_checks", "staff_id", "体温の記録"), ("users", "staff_id", "ログインする人")],
    "residents": [("attendance", "resident_id", "実績"), ("record_marks", "resident_id", "実績記録票")],
}


def references_to(key, rid):
    """他テーブルから参照されている件数（削除可否の判定用）"""
    db, found = get_db(), []
    for table, col, label in EXTRA_REFS.get(key, []):
        n = db.execute(f'SELECT COUNT(*) FROM "{table}" WHERE "{col}"=?', (rid,)).fetchone()[0]
        if n:
            found.append(f"{label} {n}件")
    for k, ent in ENTITIES.items():
        for f in ent["fields"]:
            if f["type"] in ("ref", "multiref") and f["ref"] == key:
                cond = f'"{f["name"]}" LIKE ?' if f["type"] == "multiref" else f'"{f["name"]}"=?'
                arg = f"%,{rid},%" if f["type"] == "multiref" else rid
                n = db.execute(f'SELECT COUNT(*) FROM "{k}" WHERE {cond}', (arg,)).fetchone()[0]
                if n:
                    found.append(f'{ent["title"]} {n}件')
    return found


@bp.route("/<key>/")
def index(key):
    ent = get_entity(key)
    page = clamp(request.args.get("page", 1, type=db_int) or 1, 1, 10 ** 9)
    rows, total, date_field = query_rows(key, ent, request.args, PAGE_SIZE, (page - 1) * PAGE_SIZE)
    maps = ref_maps(ent)
    cols = [f for f in ent["fields"] if f.get("list")]
    filters = [f for f in ent["fields"] if f["type"] in ("ref", "select", "multiref") and f.get("list")]
    options = {f["name"]: (ref_options(f["ref"]) if f["type"] in ("ref", "multiref") else [(o, o) for o in options_for(f, request.args.get(f["name"]))])
               for f in filters}
    return render_template(
        "crud_list.html", key=key, ent=ent, rows=rows, cols=cols, maps=maps, fmt=fmt, total=total,
        page=page, pages=(total + PAGE_SIZE - 1) // PAGE_SIZE, filters=filters, options=options,
        date_field=date_field, args=request.args,
    )


def _form_context(ent):
    return {f["name"]: ref_options(f["ref"]) for f in ent["fields"] if f["type"] in ("ref", "multiref")}


@bp.route("/<key>/new", methods=["GET", "POST"])
def new(key):
    ent = get_entity(key)
    if request.method == "POST":
        data, errors = parse_form(ent, request.form)
        if not errors:
            rid = save(key, data)
            flash("登録しました。", "ok")
            return redirect(safe_next(request.form.get("_next")) or url_for("crud.view", key=key, rid=rid))
        for e in errors:
            flash(e, "error")
        values = data
    else:
        values = {}
        for f in ent["fields"]:
            d = f.get("default")
            values[f["name"]] = date.today().isoformat() if d == "today" else d
        fields = {f["name"]: f for f in ent["fields"]}
        for k, v in request.args.items():  # ?resident_id=3 のような初期値
            values[k] = f",{v}," if k in fields and fields[k]["type"] == "multiref" and v.isdigit() else v
    return render_template("crud_form.html", key=key, ent=ent, values=values, refs=_form_context(ent), rid=None,
                           next=request.args.get("next", ""))


@bp.route("/<key>/<int:rid>")
def view(key, rid):
    ent = get_entity(key)
    row = get_db().execute(f'SELECT * FROM "{key}" WHERE id=?', (rid,)).fetchone()
    if row is None:
        abort(404)
    related = []
    for k, e in ENTITIES.items():
        if (e.get("admin_only") and not is_admin()) or not entity_on(k):
            continue
        for f in e["fields"]:
            if f["type"] in ("ref", "multiref") and f["ref"] == key:
                related.append((k, e, f["name"]))
    if key in VIEW_LOGGED:
        audit("view", key, rid)
        get_db().commit()
    summary = resident_summary(rid) if key == "residents" else None
    return render_template("crud_view.html", key=key, ent=ent, row=row, maps=ref_maps(ent), fmt=fmt, related=related,
                           summary=summary)


def resident_summary(rid):
    """入居者の詳細画面に出す「最近のようす」"""
    db = get_db()
    return {
        "away": db.execute("SELECT * FROM absences WHERE resident_id=? AND status='不在中' ORDER BY start_date DESC LIMIT 1",
                           (rid,)).fetchone(),
        "records": db.execute("SELECT * FROM support_records WHERE resident_id=? ORDER BY date DESC, id DESC LIMIT 5",
                              (rid,)).fetchall(),
        "contacts": db.execute("SELECT * FROM contact_logs WHERE resident_id=? ORDER BY date DESC, id DESC LIMIT 5",
                               (rid,)).fetchall(),
        "activities": db.execute("SELECT * FROM activities WHERE participants LIKE ? ORDER BY date DESC, id DESC LIMIT 5",
                                 (f"%,{rid},%",)).fetchall(),
        "plan": db.execute("SELECT * FROM support_plans WHERE resident_id=? AND (status IS NULL OR status != '終了') "
                           "ORDER BY period_end DESC LIMIT 1", (rid,)).fetchone(),
    }


@bp.route("/<key>/<int:rid>/edit", methods=["GET", "POST"])
def edit(key, rid):
    ent = get_entity(key)
    row = get_db().execute(f'SELECT * FROM "{key}" WHERE id=?', (rid,)).fetchone()
    if row is None:
        abort(404)
    if request.method == "POST":
        data, errors = parse_form(ent, request.form)
        if not errors:
            save(key, data, rid)
            flash("更新しました。", "ok")
            return redirect(safe_next(request.form.get("_next")) or url_for("crud.view", key=key, rid=rid))
        for e in errors:
            flash(e, "error")
        values = data
    else:
        values = dict(row)
    return render_template("crud_form.html", key=key, ent=ent, values=values, refs=_form_context(ent), rid=rid,
                           next=request.args.get("next", ""))


@bp.route("/<key>/<int:rid>/delete", methods=["POST"])
def delete(key, rid):
    get_entity(key)
    refs = references_to(key, rid)
    if refs:
        flash("関連データがあるため削除できません（" + "、".join(refs) + "）。状態を『退居』『退職』等に変更してください。", "error")
        return redirect(url_for("crud.view", key=key, rid=rid))
    db = get_db()
    db.execute(f'DELETE FROM "{key}" WHERE id=?', (rid,))
    audit("delete", key, rid)
    db.commit()
    if key in AFTER_DELETE:
        AFTER_DELETE[key](rid)
        db.commit()
    flash("削除しました。", "ok")
    return redirect(url_for("crud.index", key=key))


@bp.route("/<key>/export.xlsx")
def export(key):
    ent = get_entity(key)
    rows, _, _ = query_rows(key, ent, request.args)
    maps = ref_maps(ent)
    headers = [f["label"] for f in ent["fields"] if f["type"] != "hidden"]
    fields = [f for f in ent["fields"] if f["type"] != "hidden"]
    data = [[excel.cell_value(f, r[f["name"]], maps) for f in fields] for r in rows]
    return excel.send_table(ent["title"], headers, data)
