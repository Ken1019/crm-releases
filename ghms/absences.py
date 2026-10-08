"""入院・帰宅（帰省）・外泊と、そのあいだの連絡記録。

- 期間は月の実績（attendance）に自動で反映する（出発日・戻った日は在居のまま、その間の日だけ）
- 手で入れた実績（入院・帰省の登録から入ったもの以外）は上書きしない。削除・修正したら入れ直す
- 入院中は入居者の状態を「入院中」にし、戻ったら「入居中」に戻す（戻った日が先の日付なら、その日が来るまで不在中）
- 入院中・帰省中の方と、最後に連絡した日をホーム・日誌に表示する
"""

from datetime import date, timedelta

from flask import Blueprint, abort, flash, g, has_request_context, redirect, render_template, request, url_for

from . import crud
from .db import get_db, now
from .views import parse_date

bp = Blueprint("absences", __name__, url_prefix="/absences")

CODE = {"入院": "入", "帰宅（帰省）": "帰", "外泊": "外"}
NO_CONTACT_DAYS = 7  # 入院中にこの日数以上連絡がなければお知らせ


def _label(row):
    r = get_db().execute("SELECT name FROM residents WHERE id=?", (row["resident_id"],)).fetchone()
    period = f'{row["start_date"] or ""}〜{row["end_date"] or ""}'
    return f'{r["name"] if r else ""} {row["kind"] or ""} {period}'


def _compute(data):
    from .billing import InputError

    today = date.today()
    start, end = parse_date(data.get("start_date")), parse_date(data.get("end_date"))
    if start and end and end < start:
        raise InputError("「戻った日」が「出発した日」より前になっています。日付を確かめてください。")
    if end and end <= today:
        data["status"] = "戻った"
    elif start and start > today:
        data["status"] = "予定"
    elif data.get("status") in (None, "", "戻った", "予定"):
        # 出発済みで、戻った日が空（または先の日付＝戻る予定）なら不在中。その日が来たら自動で「戻った」になる
        data["status"] = "不在中"


def _period(a):
    """実績に入れる日（出発日・戻った日は在居のまま、その間の日だけ）。戻った日が空なら今日まで"""
    start = parse_date(a["start_date"])
    if not start:
        return None, None
    end = parse_date(a["end_date"])
    return start + timedelta(days=1), (end - timedelta(days=1)) if end else date.today()


def _fill(a):
    """この入院・帰省の日を実績に入れる。手で入れた日（入院・帰省の登録から入ったもの以外）は上書きしない"""
    code = CODE.get(a["kind"])
    if not (code and a["auto_attendance"] and a["status"] != "予定"):
        return
    d, last = _period(a)
    if d is None:
        return
    db, tag = get_db(), f"absence:{a['id']}"
    while d <= last:
        db.execute("INSERT INTO attendance (resident_id, date, code, updated_by, updated_at) VALUES (?,?,?,?,?)"
                   " ON CONFLICT(resident_id, date) DO UPDATE SET code=excluded.code, updated_by=excluded.updated_by,"
                   " updated_at=excluded.updated_at WHERE attendance.updated_by LIKE 'absence:%'",
                   (a["resident_id"], d.isoformat(), code, tag, now()))
        d += timedelta(days=1)


def resync_resident(resident_id):
    """この入居者の入院・帰省をすべて実績に入れ直し、入居者の状態（入院中など）を合わせる。
    期間が重なるときは、出発日があとのものを優先する"""
    db = get_db()
    db.execute("DELETE FROM attendance WHERE resident_id=? AND updated_by LIKE 'absence:%'", (resident_id,))
    for a in db.execute("SELECT * FROM absences WHERE resident_id=? ORDER BY start_date, id", (resident_id,)).fetchall():
        _fill(a)
    update_status(resident_id)


def update_status(resident_id):
    """入院中（戻った日が来ていない入院）があれば「入院中」、なければ「入居中」に戻す"""
    db = get_db()
    res = db.execute("SELECT status FROM residents WHERE id=?", (resident_id,)).fetchone()
    if res is None:
        return
    away = db.execute("SELECT 1 FROM absences WHERE resident_id=? AND kind='入院' AND status='不在中'", (resident_id,)).fetchone()
    if away and res["status"] in (None, "", "入居中"):
        db.execute("UPDATE residents SET status='入院中' WHERE id=?", (resident_id,))
    elif not away and res["status"] == "入院中":
        db.execute("UPDATE residents SET status='入居中' WHERE id=?", (resident_id,))


def _overlaps(a):
    end = a["end_date"] or "9999-12-31"
    return get_db().execute(
        "SELECT * FROM absences WHERE resident_id=? AND id!=? AND start_date < ? AND COALESCE(NULLIF(end_date, ''), '9999-12-31') > ?"
        " ORDER BY start_date", (a["resident_id"], a["id"], end, a["start_date"])).fetchall()


def sync(rid):
    """登録・修正のあと：実績と入居者の状態をこの入院・帰省（と、同じ方のほかの入院・帰省）に合わせる"""
    a = get_db().execute("SELECT * FROM absences WHERE id=?", (rid,)).fetchone()
    if a is None:
        return
    resync_resident(a["resident_id"])
    others = _overlaps(a) if a["start_date"] else []
    if others and has_request_context():
        flash("ほかの入院・帰宅・外泊と期間が重なっています（" + "、".join(
            f'{o["kind"]} {o["start_date"]}〜{o["end_date"] or ""}' for o in others)
            + "）。実績は出発日があとのものを優先して入れました。まちがいがないか確かめてください。", "warn")


def after_delete(rid):
    """削除のあと：この登録から入った実績を消し、同じ方のほかの入院・帰省を入れ直す"""
    db = get_db()
    tag = f"absence:{rid}"
    for (resident_id,) in db.execute("SELECT DISTINCT resident_id FROM attendance WHERE updated_by=?", (tag,)).fetchall():
        resync_resident(resident_id)
    db.execute("DELETE FROM attendance WHERE updated_by=?", (tag,))
    # 入院中のまま消したときに備えて、入院中の方の状態を見直す
    for (resident_id,) in db.execute("SELECT id FROM residents WHERE status='入院中'").fetchall():
        update_status(resident_id)


crud.LABELS["absences"] = _label
crud.COMPUTE["absences"] = _compute
crud.AFTER_SAVE["absences"] = sync
crud.AFTER_DELETE["absences"] = after_delete


def sync_open():
    """出発日が来た「予定」を不在中に、戻った日が来た「不在中」を戻ったにし、不在中のものは今日までの実績を毎回のばす"""
    db = get_db()
    today = date.today().isoformat()
    db.execute("UPDATE absences SET status='不在中' WHERE status='予定' AND start_date <= ? AND"
               " (end_date IS NULL OR end_date = '' OR end_date > ?)", (today, today))
    done = [r[0] for r in db.execute("SELECT DISTINCT resident_id FROM absences WHERE status IN ('予定', '不在中')"
                                     " AND end_date IS NOT NULL AND end_date != '' AND end_date <= ?", (today,))]
    db.execute("UPDATE absences SET status='戻った' WHERE status IN ('予定', '不在中')"
               " AND end_date IS NOT NULL AND end_date != '' AND end_date <= ?", (today,))
    away = [r[0] for r in db.execute("SELECT DISTINCT resident_id FROM absences WHERE status='不在中'")]
    for resident_id in dict.fromkeys(done + away):
        resync_resident(resident_id)
    db.commit()


def current_absences(home_id=None):
    """不在中の入院・帰省と、最後の連絡日"""
    sql = ("SELECT a.*, r.name AS rname, r.home_id, (SELECT MAX(date) FROM contact_logs c WHERE c.absence_id=a.id) AS last_contact"
           " FROM absences a JOIN residents r ON r.id=a.resident_id WHERE a.status='不在中'")
    params = []
    if home_id:
        sql += " AND r.home_id=?"
        params.append(home_id)
    rows = get_db().execute(sql + " ORDER BY a.start_date", params).fetchall()
    today = date.today()
    out = []
    for a in rows:
        last = parse_date(a["last_contact"]) or parse_date(a["start_date"])
        days = (today - last).days if last else 0
        out.append({"a": a, "days": days, "need_contact": a["kind"] == "入院" and days >= NO_CONTACT_DAYS})
    return out


@bp.route("/")
def index():
    db = get_db()
    recent = db.execute("SELECT a.*, r.name AS rname FROM absences a JOIN residents r ON r.id=a.resident_id "
                        "WHERE a.status != '不在中' ORDER BY a.start_date DESC LIMIT 30").fetchall()
    return render_template("absences.html", current=current_absences(), recent=recent)


@bp.route("/<int:aid>", methods=["GET", "POST"])
def detail(aid):
    db = get_db()
    a = db.execute("SELECT a.*, r.name AS rname FROM absences a JOIN residents r ON r.id=a.resident_id WHERE a.id=?",
                   (aid,)).fetchone()
    if a is None:
        abort(404)
    ent = crud.get_entity("contact_logs")
    if request.method == "POST":
        form = request.form.to_dict()
        form.update(resident_id=str(a["resident_id"]), absence_id=str(aid))
        data, errors = crud.parse_form(ent, form)
        if errors:
            for e in errors:
                flash(e, "error")
        else:
            crud.save("contact_logs", data)
            flash("連絡を記録しました。", "ok")
            return redirect(url_for("absences.detail", aid=aid))
    if request.method == "GET":
        crud.audit("view", "absences", aid)
        db.commit()
    logs = db.execute("SELECT * FROM contact_logs WHERE absence_id=? ORDER BY date DESC, time DESC, id DESC", (aid,)).fetchall()
    month = date.today().strftime("%Y-%m")
    visits = sum(1 for c in logs if c["method"] == "面会・訪問" and (c["date"] or "").startswith(month))
    fields = {f["name"]: f for f in ent["fields"]}
    return render_template("absence_detail.html", a=a, logs=logs, visits=visits, COUNTERPART=fields["counterpart"],
                           CONTACT_METHOD=fields["method"], CONTACT_DIRECTION=fields["direction"],
                           today=date.today().isoformat(), me=g.user["display_name"])
