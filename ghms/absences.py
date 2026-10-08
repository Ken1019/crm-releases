"""入院・帰宅（帰省）・外泊と、そのあいだの連絡記録。

- 期間は月の実績（attendance）に自動で反映する（出発日・戻った日は在居のまま、その間の日だけ）
- 入院中は入居者の状態を「入院中」にし、戻ったら「入居中」に戻す
- 入院中・帰省中の方と、最後に連絡した日をホーム・日誌に表示する
"""

from datetime import date, timedelta

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from . import crud
from .db import get_db, now
from .entities import CONTACT_DIRECTION, CONTACT_METHOD, COUNTERPART
from .views import parse_date

bp = Blueprint("absences", __name__, url_prefix="/absences")

CODE = {"入院": "入", "帰宅（帰省）": "帰", "外泊": "外"}
NO_CONTACT_DAYS = 7  # 入院中にこの日数以上連絡がなければお知らせ


def _label(row):
    r = get_db().execute("SELECT name FROM residents WHERE id=?", (row["resident_id"],)).fetchone()
    period = f'{row["start_date"] or ""}〜{row["end_date"] or ""}'
    return f'{r["name"] if r else ""} {row["kind"] or ""} {period}'


def _compute(data):
    start = parse_date(data.get("start_date"))
    if data.get("end_date"):
        data["status"] = "戻った"
    elif start and start > date.today():
        data["status"] = "予定"
    elif data.get("status") in (None, "", "戻った", "予定"):
        data["status"] = "不在中"  # 出発済みで戻った日が空なら不在中


def sync(rid):
    """実績と入居者の状態をこの入院・帰省に合わせる"""
    db = get_db()
    a = db.execute("SELECT * FROM absences WHERE id=?", (rid,)).fetchone()
    tag = f"absence:{rid}"
    db.execute("DELETE FROM attendance WHERE updated_by=?", (tag,))
    if a is None:
        return
    code = CODE.get(a["kind"])
    start = parse_date(a["start_date"])
    if code and a["auto_attendance"] and start and a["status"] != "予定":
        end = parse_date(a["end_date"])
        last = (end - timedelta(days=1)) if end else date.today()
        d = start + timedelta(days=1)
        while d <= last:
            db.execute("INSERT OR REPLACE INTO attendance (resident_id, date, code, updated_by, updated_at) VALUES (?,?,?,?,?)",
                       (a["resident_id"], d.isoformat(), code, tag, now()))
            d += timedelta(days=1)
    if a["kind"] == "入院":
        res = db.execute("SELECT status FROM residents WHERE id=?", (a["resident_id"],)).fetchone()
        if res and a["status"] == "不在中" and res["status"] in (None, "", "入居中"):
            db.execute("UPDATE residents SET status='入院中' WHERE id=?", (a["resident_id"],))
        elif res and a["status"] == "戻った" and res["status"] == "入院中":
            still = db.execute("SELECT 1 FROM absences WHERE resident_id=? AND kind='入院' AND status='不在中' AND id!=?",
                               (a["resident_id"], rid)).fetchone()
            if not still:
                db.execute("UPDATE residents SET status='入居中' WHERE id=?", (a["resident_id"],))


crud.LABELS["absences"] = _label
crud.COMPUTE["absences"] = _compute
crud.AFTER_SAVE["absences"] = sync
crud.AFTER_DELETE["absences"] = sync


def sync_open():
    """出発日が来た「予定」を不在中にし、不在中のものは今日までの実績を毎回のばす"""
    get_db().execute("UPDATE absences SET status='不在中' WHERE status='予定' AND start_date <= ?", (date.today().isoformat(),))
    for (rid,) in get_db().execute("SELECT id FROM absences WHERE status='不在中'").fetchall():
        sync(rid)
    get_db().commit()


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
    return render_template("absence_detail.html", a=a, logs=logs, visits=visits, COUNTERPART=COUNTERPART,
                           CONTACT_METHOD=CONTACT_METHOD, CONTACT_DIRECTION=CONTACT_DIRECTION,
                           today=date.today().isoformat(), me=g.user["display_name"])
