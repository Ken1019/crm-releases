"""実地指導（運営指導）に向けた「規定を満たしていないもの」のチェック。

ホームの画面のいちばん上に件数と中身を出し、「実地指導チェック」の画面で全部を見られる。
回数・日数は運営基準や自治体の指導で変わるので、画面で直せるようにしてある（初期値は目安）。
"""

from datetime import date, timedelta

from flask import Blueprint, flash, redirect, render_template, request, url_for

from .auth import admin_required, is_admin, log_event
from .customize import feature_on, tracked_meetings
from .db import get_db, get_setting, set_setting
from .views import parse_date

bp = Blueprint("compliance", __name__, url_prefix="/compliance")

ACTIVE_RES = "status IS NULL OR status != '退居'"
ACTIVE_STAFF = "status IS NULL OR status = '在籍'"

# (キー, 名前, 実地指導で見られること, 使う機能)
CHECKS = [
    ("meetings", "委員会・研修・訓練の開催", "虐待防止・身体拘束適正化・感染症対策の委員会、研修、BCP、避難訓練を決められた間隔で開いているか", "meetings"),
    ("staff_training", "職員ごとの研修の受講", "全職員が虐待防止・身体拘束・感染症・BCPの研修を受けているか（受講記録・会議の出席者）", None),
    ("journal", "業務日誌の書きもれ", "毎日の業務日誌があるか", None),
    ("records", "支援記録の書きもれ", "在居していた日の入居者ごとの支援記録（サービス提供の記録）があるか", None),
    ("plans", "個別支援計画の同意", "計画を本人に説明して同意を得て、交付しているか（同意日の記録）", None),
    ("incidents", "ヒヤリハット・事故の書きもれ", "原因・再発防止策・家族や市町村への報告が書かれているか", "incidents"),
    ("residents", "入居者の基本情報", "受給者証番号・支給決定期間・緊急連絡先がそろっているか", None),
    ("attendance", "実績の入力もれ（請求）", "先月の実績（在居・外泊・入院）が全部の日に入っているか", "billing"),
    ("staff_info", "職員の情報（勤務体制の書類）", "職種・雇用形態・週の勤務時間がそろっているか（勤務形態一覧表・常勤換算）", None),
    ("timecard", "タイムカードの退勤忘れ", "勤務の実績（出勤簿）に抜けがないか", "timecard"),
    ("health", "職員の体調", "37.5℃以上・体調不良で出勤した職員がいないか（感染症対策）", "timecard"),
    ("payroll", "給与の確定", "先月の給与が確定しているか", "payroll"),
]
CHECK_NAMES = {k: n for k, n, _, _ in CHECKS}
DEFAULT_TRAININGS = "虐待防止研修=虐待|365\n身体拘束適正化の研修=身体拘束|365\n感染症の研修=感染症|365\n業務継続計画（BCP）の研修=BCP,業務継続|365"


def checks_off():
    return {k for k in (get_setting("comp_off", "") or "").split(",") if k}


def look_days():
    try:
        return max(1, min(60, int(get_setting("comp_days", "7") or 7)))
    except ValueError:
        return 7


def training_topics():
    out = []
    for line in (get_setting("comp_trainings", DEFAULT_TRAININGS) or "").splitlines():
        if "=" not in line:
            continue
        name, rest = line.split("=", 1)
        kws, _, days = rest.partition("|")
        kws = [k.strip() for k in kws.split(",") if k.strip()]
        if name.strip() and kws:
            out.append((name.strip(), kws, int(days) if days.strip().isdigit() else 365))
    return out


def _norm(s):
    return (s or "").replace(" ", "").replace("　", "")


def _item(level, check, who, msg, url):
    return {"level": level, "check": check, "name": CHECK_NAMES[check], "who": who, "msg": msg, "url": url}


def _days_back(n):
    today = date.today()
    return [today - timedelta(days=i) for i in range(n, 0, -1)]


def _fmt_days(days):
    s = "、".join(f"{d.month}/{d.day}" for d in days[:6])
    return s + (f" ほか{len(days) - 6}日" if len(days) > 6 else "")


def run_checks(staff_id=None):
    """規定を満たしていないものの一覧。staff_id を渡すと、その職員に関係するものだけ"""
    db = get_db()
    today = date.today()
    off = checks_off()
    items = []

    def on(key):
        feat = next(f for k, _, _, f in CHECKS if k == key)
        return key not in off and (feat is None or feature_on(feat))

    staff_rows = db.execute(f"SELECT * FROM staff WHERE {ACTIVE_STAFF} ORDER BY kana, name").fetchall()
    if staff_id:
        staff_rows = [s for s in staff_rows if s["id"] == staff_id]

    if on("meetings") and not staff_id:
        for kind, limit in tracked_meetings():
            last = db.execute("SELECT MAX(date) FROM meetings WHERE kind=?", (kind,)).fetchone()[0]
            if not last:
                items.append(_item("ng", "meetings", kind, f"まだ記録がありません（{limit}日に1回が目安）",
                                   url_for("crud.new", key="meetings", kind=kind)))
            elif parse_date(last, today) < today - timedelta(days=limit):
                items.append(_item("ng", "meetings", kind, f"最後は {last}。{limit}日以上あいています",
                                   url_for("crud.new", key="meetings", kind=kind)))

    if on("staff_training"):
        for topic, kws, days in training_topics():
            since = (today - timedelta(days=days)).isoformat()
            like = " OR ".join("title LIKE ?" for _ in kws)
            args = [f"%{k}%" for k in kws]
            trained = {r[0] for r in db.execute(f"SELECT staff_id FROM trainings WHERE date >= ? AND ({like})", [since] + args)}
            m_like = " OR ".join("kind LIKE ? OR title LIKE ?" for _ in kws)
            m_args = [a for k in kws for a in (f"%{k}%", f"%{k}%")]
            attendees = [_norm(r[0]) for r in db.execute(f"SELECT attendees FROM meetings WHERE date >= ? AND ({m_like})",
                                                         [since] + m_args)]
            everyone = any(a and ("全員" in a or "全職員" in a) for a in attendees)
            for s in staff_rows:
                if s["id"] in trained or everyone or (_norm(s["name"]) and any(_norm(s["name"]) in a for a in attendees)):
                    continue
                items.append(_item("warn", "staff_training", s["name"], f"「{topic}」の受講が{days}日以内に確認できません",
                                   url_for("crud.new", key="trainings", staff_id=s["id"], title=topic) if not staff_id
                                   else url_for("crud.index", key="meetings")))

    # 日誌・支援記録は職員みんなで書くので、職員の画面にも出す
    n = look_days()
    homes = db.execute("SELECT * FROM homes ORDER BY name").fetchall()
    if on("journal"):
        for h in homes:
            done = {r[0] for r in db.execute("SELECT date FROM daily_logs WHERE home_id=? AND date >= ?",
                                             (h["id"], (today - timedelta(days=n)).isoformat()))}
            miss = [d for d in _days_back(n) if d.isoformat() not in done]
            if miss:
                items.append(_item("warn", "journal", h["name"], f"業務日誌がない日：{_fmt_days(miss)}",
                                   url_for("views.journal", home_id=h["id"], date=miss[0].isoformat())))
    if on("records"):
        since = (today - timedelta(days=n)).isoformat()
        have = {(r[0], r[1]) for r in db.execute("SELECT resident_id, date FROM support_records WHERE date >= ?", (since,))}
        codes = {(r[0], r[1]): r[2] for r in db.execute("SELECT resident_id, date, code FROM attendance WHERE date >= ?", (since,))}
        for r in db.execute(f"SELECT * FROM residents WHERE {ACTIVE_RES} ORDER BY kana"):
            mi, mo = parse_date(r["move_in"]), parse_date(r["move_out"])
            miss = [d for d in _days_back(n) if (mi is None or mi <= d) and (mo is None or d <= mo)
                    and codes.get((r["id"], d.isoformat()), "○") in ("○", "日") and (r["id"], d.isoformat()) not in have]
            if miss:
                items.append(_item("warn", "records", r["name"], f"支援記録がない日：{_fmt_days(miss)}",
                                   url_for("views.journal", home_id=r["home_id"], date=miss[0].isoformat())))

    if not staff_id:
        if on("plans"):
            for p in db.execute("SELECT p.*, r.name AS rname FROM support_plans p JOIN residents r ON r.id=p.resident_id "
                                "WHERE (r.status IS NULL OR r.status != '退居') AND (p.status IS NULL OR p.status != '終了')"):
                if p["status"] != "作成中" and not p["consent_date"]:
                    items.append(_item("ng", "plans", p["rname"], "個別支援計画の本人同意日が記録されていません",
                                       url_for("crud.edit", key="support_plans", rid=p["id"])))
                elif p["status"] == "作成中" and parse_date(p["created_on"], today) < today - timedelta(days=30):
                    items.append(_item("warn", "plans", p["rname"], f"個別支援計画が「作成中」のまま30日以上（作成日 {p['created_on']}）",
                                       url_for("crud.edit", key="support_plans", rid=p["id"])))
        if on("incidents"):
            for i in db.execute("SELECT i.*, r.name AS rname FROM incidents i LEFT JOIN residents r ON r.id=i.resident_id "
                                "WHERE i.date >= ? ORDER BY i.date", ((today - timedelta(days=180)).isoformat(),)):
                lack = [label for f, label in (("cause", "原因"), ("prevention", "再発防止策")) if not (i[f] or "").strip()]
                if i["kind"] == "事故" and not (i["family_report"] or "").strip():
                    lack.append("家族・市町村への報告")
                if lack:
                    items.append(_item("warn", "incidents", f"{i['date']} {i['rname'] or ''}".strip(), "未記入：" + "・".join(lack),
                                       url_for("crud.edit", key="incidents", rid=i["id"])))
        if on("residents"):
            for r in db.execute(f"SELECT * FROM residents WHERE {ACTIVE_RES} ORDER BY kana"):
                lack = [label for f, label in (("recipient_no", "受給者証番号"), ("cert_end", "支給決定期間"),
                                               ("emergency_tel", "緊急連絡先")) if not (str(r[f] or "")).strip()]
                if lack:
                    items.append(_item("warn", "residents", r["name"], "未入力：" + "・".join(lack),
                                       url_for("crud.edit", key="residents", rid=r["id"])))
        if on("attendance"):
            from .billing import in_residence, month_days, residents_in_month

            last = today.replace(day=1) - timedelta(days=1)
            first = last.replace(day=1)
            codes = {(r[0], r[1]) for r in db.execute("SELECT resident_id, date FROM attendance WHERE date BETWEEN ? AND ?",
                                                      (first.isoformat(), last.isoformat()))}
            for r in residents_in_month(first, last):
                miss = [d for d in month_days(first, last) if in_residence(r, d) and (r["id"], d.isoformat()) not in codes]
                if miss:
                    items.append(_item("ng" if today.day <= 10 else "warn", "attendance", r["name"],
                                       f"{first:%Y年%m月}の実績が {len(miss)}日 入っていません（請求は毎月10日まで）",
                                       url_for("billing.attendance", ym=first.strftime("%Y-%m"), home_id=r["home_id"])))
        if on("staff_info"):
            for s in staff_rows:
                lack = [label for f, label in (("job", "職種"), ("employment", "雇用形態"), ("weekly_hours", "週の勤務時間"))
                        if not (str(s[f] or "")).strip()]
                if lack:
                    items.append(_item("warn", "staff_info", s["name"], "未入力：" + "・".join(lack),
                                       url_for("crud.edit", key="staff", rid=s["id"])))

    if on("timecard"):
        from .work import forgotten_cards

        names = {s["id"]: s["name"] for s in db.execute("SELECT id, name FROM staff")}
        for c in forgotten_cards(staff_id):
            items.append(_item("warn", "timecard", names.get(c["staff_id"], "?"), f"{c['date']} {c['clock_in']}〜 の退勤の打刻がありません",
                               url_for("work.timecards", ym=c["date"][:7], staff_id=c["staff_id"])))
    if on("health") and not staff_id:
        from .work import is_unwell

        names = {s["id"]: s["name"] for s in db.execute("SELECT id, name FROM staff")}
        for h in db.execute("SELECT * FROM health_checks WHERE date=? ORDER BY time", (today.isoformat(),)):
            if is_unwell(h):
                items.append(_item("ng", "health", names.get(h["staff_id"], "?"),
                                   f"{h['time']} {h['temp'] or ''}℃ {h['symptoms'] or ''}".strip(), url_for("work.health")))
    if on("payroll") and not staff_id and today.day >= 5:
        last = today.replace(day=1) - timedelta(days=1)
        ym = last.strftime("%Y-%m")
        worked = {r[0] for r in db.execute("SELECT DISTINCT staff_id FROM timecards WHERE substr(date,1,7)=?", (ym,))}
        done = {r[0] for r in db.execute("SELECT staff_id FROM payslips WHERE ym=? AND status='確定'", (ym,))}
        if worked - done:
            items.append(_item("warn", "payroll", f"{last:%Y年%m月}分", f"給与が確定していない職員が {len(worked - done)}人います",
                               url_for("payroll.index", ym=ym)))
    order = {k: i for i, (k, _, _, _) in enumerate(CHECKS)}
    items.sort(key=lambda x: (x["level"] != "ng", order[x["check"]]))
    return items


def grouped(items):
    out = []
    for k, name, why, _ in CHECKS:
        xs = [x for x in items if x["check"] == k]
        if xs:
            out.append({"key": k, "name": name, "why": why, "items": xs, "ng": sum(1 for x in xs if x["level"] == "ng")})
    return out


@bp.route("/", methods=["GET", "POST"])
@admin_required
def index():
    if request.method == "POST":
        set_setting("comp_off", ",".join(k for k, _, _, _ in CHECKS if not request.form.get(f"on::{k}")))
        set_setting("comp_days", str(max(1, min(60, request.form.get("comp_days", type=int) or 7))))
        set_setting("comp_trainings", (request.form.get("comp_trainings") or "").strip() or DEFAULT_TRAININGS)
        log_event("settings", "compliance", None, "実地指導チェックの設定")
        get_db().commit()
        flash("チェックの設定を保存しました。", "ok")
        return redirect(url_for("compliance.index"))
    items = run_checks()
    return render_template("compliance.html", groups=grouped(items), total=len(items), CHECKS=CHECKS, off=checks_off(),
                           days=look_days(), trainings=get_setting("comp_trainings", DEFAULT_TRAININGS))
