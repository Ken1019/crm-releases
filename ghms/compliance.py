"""実地指導（運営指導）に向けた「規定を満たしていないもの」のチェック。

ホームの画面のいちばん上に件数と中身を出し、「実地指導チェック」の画面で全部を見られる。
回数・日数は運営基準や自治体の指導で変わるので、画面で直せるようにしてある（初期値は目安）。
"""

import re
import unicodedata
from datetime import date, timedelta

from flask import Blueprint, flash, redirect, render_template, request, url_for

from .auth import admin_required, log_event
from .customize import feature_on, tracked_meetings
from .db import get_db, get_setting, set_setting, system_start
from .forms import db_int
from .views import parse_date

bp = Blueprint("compliance", __name__, url_prefix="/compliance")

ACTIVE_RES = "status IS NULL OR status != '退居'"
ACTIVE_STAFF = "status IS NULL OR status = '在籍'"

# (キー, 名前, 実地指導で見られること, 使う機能（タプルはどれか1つが「使う」ならチェックする）)
CHECKS = [
    ("meetings", "委員会・研修・訓練の開催", "虐待防止・身体拘束適正化・感染症対策の委員会、研修、BCP、避難訓練を決められた間隔で開いているか", "meetings"),
    ("staff_training", "職員ごとの研修の受講", "全職員が虐待防止・身体拘束・感染症・BCPの研修を受けているか（研修受講記録・研修の出席者）",
     ("career", "meetings")),
    ("journal", "業務日誌の書きもれ", "入居者がいた日の業務日誌があるか", None),
    ("records", "支援記録の書きもれ", "在居していた日の入居者ごとの支援記録（サービス提供の記録）があるか", None),
    ("plans", "個別支援計画の同意", "計画を本人に説明して同意を得て、交付しているか（同意日の記録）", None),
    ("incidents", "ヒヤリハット・事故の書きもれ", "原因・再発防止策・家族や市町村への報告が書かれているか", "incidents"),
    ("residents", "入居者の基本情報", "受給者証番号・支給決定期間・緊急連絡先がそろっているか", None),
    ("attendance", "実績の入力もれ（請求）", "先月の実績（在居・外泊・入院）が全部の日に入っているか", "billing"),
    ("staff_info", "職員の情報（勤務体制の書類）", "職種・雇用形態・週の勤務時間がそろっているか（勤務形態一覧表・常勤換算）", None),
    ("timecard", "タイムカードの退勤忘れ", "勤務の実績（出勤簿）に抜けがないか", "timecard"),
    ("shift_match", "勤務表と出勤簿のちがい", "勤務表（勤務形態一覧表）と実際の勤務（出勤簿）が合っているか。人員配置・夜間支援の体制の根拠", "timecard"),
    ("leave5", "有給の年5日の取得", "10日以上付与した職員が、付与から1年以内に5日取っているか（労働基準法39条7項）。有給は勤務表の「有」で記録します", "shift"),
    ("health", "職員の体調", "37.5℃以上・体調不良で出勤した職員がいないか（感染症対策）", "timecard"),
    ("payroll", "給与の確定", "最近3か月の給与が確定しているか", "payroll"),
]
CHECK_NAMES = {k: n for k, n, _, _ in CHECKS}
CHECK_FEATURE = {k: f for k, _, _, f in CHECKS}
DEFAULT_TRAININGS = "虐待防止研修=虐待|365\n身体拘束適正化の研修=身体拘束|365\n感染症の研修=感染症|365\n業務継続計画（BCP）の研修=BCP,業務継続|365"
# 職員が自分では直せないもの（職員の画面では「管理者に伝えること」にまとめる）
STAFF_CANT_FIX = {"timecard", "shift_match", "leave5", "staff_training"}
STD_MISSING = "標準報酬月額が未入力です。毎月の総支給から仮に決めています。資格取得のときの届出の額を入れてください"
LEAVE5_EXPIRED = "期限が過ぎています（次の付与で守れるようにしましょう）"


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


def _nfkc(s):
    """全角の英数字・記号を半角にそろえる（ＢＣＰ → BCP）"""
    return unicodedata.normalize("NFKC", s or "")


def _norm(s):
    return re.sub(r"\s+", "", _nfkc(s))


_SEP = re.compile(r"[、,，/／・;；\n]+")
_HONORIFIC = re.compile(r"(さん|様|さま|氏)$")
EVERYONE = {"全員", "全職員", "職員全員"}


def attendee_names(text):
    """出席者の欄を名前に分ける。「、 , ， / ・」と改行で区切り、空白で区切った形も入れる。
    空白をのぞいた名前が職員の名前とぴったり同じときだけ出席とみなす（「田中」と「田中太郎」は別の人）"""
    out = set()
    for part in _SEP.split(_nfkc(text)):
        part = part.strip()
        if not part:
            continue
        for x in [part] + part.split():
            x = _HONORIFIC.sub("", _norm(x))
            if x:
                out.add(x)
    return out


def is_training_meeting(kind, title):
    """研修として数える会議：種別か議題に「研修」が入っているもの（委員会だけの回は研修に数えない）"""
    return "研修" in _nfkc(kind) or "研修" in _nfkc(title)


def _topic_hit(kws, *texts):
    t = "".join(_nfkc(x) for x in texts).upper()
    return any(_nfkc(k).upper() in t for k in kws)


def _item(level, check, who, msg, url, done=False):
    return {"level": level, "check": check, "name": CHECK_NAMES[check], "who": who, "msg": msg, "url": url, "done": done}


def _fmt_few(days, k=3):
    """件数と、はじめの何日か（例「12日（9/1、9/2、9/3 ほか）」）"""
    s = "、".join(f"{d.month}/{d.day}" for d in days[:k])
    return f"{len(days)}日（{s}{' ほか' if len(days) > k else ''}）"


def check_days(today=None):
    """書きもれを見る日：先月の1日（設定の日数のほうが長ければそちら）〜きのう。使い始めた日より前は見ない。
    先月の書きもれは、直すまで出つづける"""
    today = today or date.today()
    lo = min(_month_first(today, 1), today - timedelta(days=look_days()))
    ss = system_start()
    if ss and ss > lo:
        lo = ss
    return [lo + timedelta(days=i) for i in range((today - lo).days)]


def before_start(last):
    """その月（の末日 last）がまるごと使い始めた日より前か"""
    ss = system_start()
    return bool(ss and last < ss)


def _fmt_days(days):
    s = "、".join(f"{d.month}/{d.day}" for d in days[:6])
    return s + (f" ほか{len(days) - 6}日" if len(days) > 6 else "")


def _month_first(d, back):
    """d の月から back か月前の1日"""
    y, m = divmod(d.year * 12 + d.month - 1 - back, 12)
    return date(y, m + 1, 1)


def unconfirmed_payroll(months=3):
    """最近 months か月で、打刻があるのに給与が確定していない月。[(1日, 月末, 確定していない人数, 打刻した人数)]"""
    db = get_db()
    today = date.today()
    out = []
    for back in range(1, months + 1):
        first = _month_first(today, back)
        last = _month_first(today, back - 1) - timedelta(days=1)
        ym = first.strftime("%Y-%m")
        if before_start(last):
            continue  # 使い始める前の月は見ない
        worked = {r[0] for r in db.execute("SELECT DISTINCT staff_id FROM timecards WHERE date BETWEEN ? AND ?",
                                           (first.isoformat(), last.isoformat()))}
        if not worked:
            continue
        done = {r[0] for r in db.execute("SELECT staff_id FROM payslips WHERE ym=? AND status='確定'", (ym,))}
        out.append((first, last, len(worked - done), len(worked)))
    return out


RES_FOR_JOURNAL = "status IS NULL OR status != '退居' OR move_out IS NOT NULL"


def lived_there(r, day):
    """その日に住居で暮らしていたか（入居日〜退居日。退居にしたのに退居日がない人は数えない）"""
    from .billing import in_residence

    if r["status"] == "退居" and not r["move_out"]:
        return False
    return in_residence(r, day)


def run_checks(staff_id=None, include_done=False):
    """規定を満たしていないものの一覧。staff_id を渡すと、その職員に関係するものだけ。
    include_done=True のときは、もう対応が済んだもの（体調不良の人が退勤した など）も done=True で返す"""
    db = get_db()
    today = date.today()
    off = checks_off()
    items = []

    def on(key):
        feat = CHECK_FEATURE[key]
        if key in off:
            return False
        if feat is None:
            return True
        return any(feature_on(f) for f in (feat if isinstance(feat, tuple) else (feat,)))

    staff_rows = db.execute(f"SELECT * FROM staff WHERE {ACTIVE_STAFF} ORDER BY kana, name").fetchall()
    if staff_id:
        staff_rows = [s for s in staff_rows if s["id"] == staff_id]

    if on("meetings") and not staff_id:
        lasts = dict(db.execute("SELECT kind, MAX(date) FROM meetings GROUP BY kind").fetchall())
        for kind, limit in tracked_meetings():
            last = lasts.get(kind)
            if not last:
                items.append(_item("ng", "meetings", kind, f"まだ記録がありません（{limit}日に1回が目安）",
                                   url_for("crud.new", key="meetings", kind=kind)))
            elif parse_date(last, today) < today - timedelta(days=limit):
                items.append(_item("ng", "meetings", kind, f"最後は {last}。{limit}日以上あいています",
                                   url_for("crud.new", key="meetings", kind=kind)))

    if on("staff_training") and staff_rows:
        topics = training_topics()
        oldest = (today - timedelta(days=max([d for _, _, d in topics] or [365]))).isoformat()
        # 研修受講記録（研修名で見る）と、研修の会議（種別か議題に「研修」）の出席者
        trs = db.execute("SELECT staff_id, date, title FROM trainings WHERE date >= ?", (oldest,)).fetchall()
        mts = [(m["date"], m["kind"], m["title"], attendee_names(m["attendees"]))
               for m in db.execute("SELECT date, kind, title, attendees FROM meetings WHERE date >= ?", (oldest,))
               if is_training_meeting(m["kind"], m["title"])]
        career, meetings_on = feature_on("career"), feature_on("meetings")
        for topic, kws, days in topics:
            since = (today - timedelta(days=days)).isoformat()
            trained = {t["staff_id"] for t in trs if t["date"] >= since and _topic_hit(kws, t["title"])}
            names = set()
            for d, kind, title, who in mts:
                if d >= since and _topic_hit(kws, kind, title):
                    names |= who
            everyone = bool(names & EVERYONE)
            for s in staff_rows:
                if s["id"] in trained or everyone or _HONORIFIC.sub("", _norm(s["name"])) in names:
                    continue
                if staff_id:
                    url = url_for("crud.index", key="meetings") if meetings_on else url_for("views.dashboard") + "#today"
                elif career:
                    url = url_for("crud.new", key="trainings", staff_id=s["id"], title=topic)
                else:
                    url = url_for("crud.new", key="meetings", title=topic)
                items.append(_item("warn", "staff_training", s["name"], f"「{topic}」の受講が{days}日以内に確認できません", url))

    # 日誌・支援記録は職員みんなで書くので、職員の画面にも出す
    homes = db.execute("SELECT * FROM homes ORDER BY name").fetchall()
    days = check_days(today)
    if on("journal") or on("records"):
        res_rows = db.execute(f"SELECT * FROM residents WHERE {RES_FOR_JOURNAL} ORDER BY kana").fetchall()
        if staff_id:
            # 職員は、自分の「主な勤務住居」が入っていれば、その住居の分だけ
            me = db.execute("SELECT home_id FROM staff WHERE id=?", (staff_id,)).fetchone()
            if me and me["home_id"] and any(h["id"] == me["home_id"] for h in homes):
                homes = [h for h in homes if h["id"] == me["home_id"]]
                res_rows = [r for r in res_rows if r["home_id"] == me["home_id"]]
    if on("journal") and days:
        since = days[0].isoformat()
        done = {(r[0], r[1]) for r in db.execute("SELECT home_id, date FROM daily_logs WHERE date >= ?", (since,))}
        for h in homes:
            start = parse_date((h["created_at"] or "")[:10])
            hres = [r for r in res_rows if r["home_id"] == h["id"]]
            # 住居を登録する前の日・入居者がいなかった日は書かなくてよい
            miss = [d for d in days if (start is None or d >= start) and any(lived_there(r, d) for r in hres)
                    and (h["id"], d.isoformat()) not in done]
            if miss:
                items.append(_item("warn", "journal", h["name"], f"業務日誌がない日：{_fmt_few(miss)}",
                                   url_for("views.journal", home_id=h["id"], date=miss[0].isoformat())))
    if on("records") and days:
        since = days[0].isoformat()
        have = {(r[0], r[1]) for r in db.execute("SELECT resident_id, date FROM support_records WHERE date >= ?", (since,))}
        codes = {(r[0], r[1]): r[2] for r in db.execute("SELECT resident_id, date, code FROM attendance WHERE date >= ?", (since,))}
        for r in res_rows:
            if r["status"] == "退居":
                continue
            mi, mo = parse_date(r["move_in"]), parse_date(r["move_out"])
            miss = [d for d in days if (mi is None or mi <= d) and (mo is None or d <= mo)
                    and codes.get((r["id"], d.isoformat()), "○") in ("○", "日") and (r["id"], d.isoformat()) not in have]
            if miss:
                items.append(_item("warn", "records", r["name"], f"支援記録がない日：{_fmt_few(miss)}",
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
            for r in ([] if before_start(last) else residents_in_month(first, last)):
                miss = [d for d in month_days(first, last) if in_residence(r, d) and (r["id"], d.isoformat()) not in codes]
                if miss:
                    # 請求は毎月10日まで。10日を過ぎたら急ぎ
                    items.append(_item("ng" if today.day > 10 else "warn", "attendance", r["name"],
                                       f"{first:%Y年%m月}の実績が {len(miss)}日 入っていません（請求は毎月10日まで）",
                                       url_for("billing.attendance", ym=first.strftime("%Y-%m"), home_id=r["home_id"])))
        if on("staff_info"):
            for s in staff_rows:
                lack = [label for f, label in (("job", "職種"), ("employment", "雇用形態"), ("weekly_hours", "週の勤務時間"))
                        if not (str(s[f] or "")).strip()]
                if lack:
                    items.append(_item("warn", "staff_info", s["name"], "未入力：" + "・".join(lack),
                                       url_for("crud.edit", key="staff", rid=s["id"])))
                if feature_on("payroll") and s["social_insurance"] and not s["std_monthly"]:
                    items.append(_item("warn", "staff_info", s["name"], STD_MISSING,
                                       url_for("crud.edit", key="staff", rid=s["id"])))

    names = None
    if on("timecard"):
        from .work import forgotten_cards

        names = {s["id"]: s["name"] for s in db.execute("SELECT id, name FROM staff")}
        for c in forgotten_cards(staff_id):
            items.append(_item("warn", "timecard", names.get(c["staff_id"], "?"), f"{c['date']} {c['clock_in']}〜 の退勤の打刻がありません",
                               url_for("work.timecards", ym=c["date"][:7], staff_id=c["staff_id"]) if not staff_id
                               else url_for("work.clock")))
    if on("shift_match") and feature_on("shift"):
        from .work import shift_differences

        first = (today.replace(day=1) - timedelta(days=1)).replace(day=1) if today.day <= 7 else today.replace(day=1)
        # 勤務表か打刻がある人だけ見る（両方ない人はちがいが出ない）
        active = {r[0] for r in db.execute("SELECT staff_id FROM shifts WHERE date BETWEEN ? AND ? UNION "
                                           "SELECT staff_id FROM timecards WHERE date BETWEEN ? AND ?",
                                           (first.isoformat(), today.isoformat()) * 2)}
        for s in staff_rows:
            if s["id"] not in active:
                continue
            found = {}
            m = first
            while m <= today:
                nxt = (m.replace(day=28) + timedelta(days=4)).replace(day=1)
                for dd, diffs in shift_differences(s["id"], m, nxt - timedelta(days=1)).items():
                    for kind, _ in diffs:
                        found.setdefault(kind, []).append(parse_date(dd))
                m = nxt
            if found:
                msg = "・".join(f"{k} {len(v)}日（{_fmt_days(v)}）" for k, v in found.items())
                latest = max(d for v in found.values() for d in v)
                items.append(_item("warn", "shift_match", s["name"], msg,
                                   url_for("work.timecards", ym=latest.strftime("%Y-%m"), staff_id=s["id"]) if not staff_id
                                   else url_for("work.my_shift", ym=latest.strftime("%Y-%m"))))
    if on("leave5"):
        from .leave import LEAVE_CODE, add_months, ensure_grants

        # 付与（まだ作っていない付与があれば作る）と、勤務表の「有」の日を、全員分まとめて読む
        made = 0
        for s in staff_rows:
            if s["hire_date"] and parse_date(s["hire_date"], today) <= today - timedelta(days=180):  # 入職から6か月までは付与がない
                made += ensure_grants(s)
        if made:
            db.commit()
        grants, taken = {}, {}
        for r in db.execute("SELECT staff_id, grant_date FROM leave_grants WHERE days >= 10 AND grant_date >= ? ORDER BY grant_date",
                            ((today - timedelta(days=500)).isoformat(),)):
            grants.setdefault(r[0], []).append(parse_date(r[1]))
        for r in db.execute("SELECT staff_id, date FROM shifts WHERE code=? AND date >= ?",
                            (LEAVE_CODE, (today - timedelta(days=500)).isoformat())):
            taken.setdefault(r[0], []).append(r[1])
        for s in staff_rows:
            for gd in grants.get(s["id"], []):
                if not gd or gd > today:
                    continue
                end = add_months(gd, 12)
                left = (end - today).days
                took = sum(1 for d in taken.get(s["id"], []) if gd.isoformat() <= d < end.isoformat())
                if took >= 5 or not -60 <= left <= 120:  # 何年も前の付与は出さない
                    continue
                if before_start(end):
                    continue  # 期限が使い始める前に過ぎていたもの
                url = url_for("leave.staff", sid=s["id"]) if not staff_id else url_for("work.my_shift")
                base = f"{gd}付与の有給：{end}までに5日のうち {took}日"
                if left < 0:
                    # 過ぎてしまったものは直せない。60日だけ「今月」に出して、次の付与で守れるように
                    items.append(_item("warn", "leave5", s["name"], f"{base}。{LEAVE5_EXPIRED}", url))
                else:
                    items.append(_item("ng" if left <= 30 else "warn", "leave5", s["name"],
                                       f"{base}（{'今日まで' if left == 0 else f'あと{left}日'}）", url))
    if on("health") and not staff_id:
        from .work import is_unwell

        names = names or {s["id"]: s["name"] for s in db.execute("SELECT id, name FROM staff")}
        cards = {}
        for c in db.execute("SELECT staff_id, clock_out FROM timecards WHERE date=?", (today.isoformat(),)):
            cards.setdefault(c["staff_id"], []).append(c["clock_out"])
        for h in db.execute("SELECT * FROM health_checks WHERE date=? ORDER BY time", (today.isoformat(),)):
            if is_unwell(h):
                outs = cards.get(h["staff_id"], [])
                left_work = bool(outs) and all(outs)  # 退勤した（今日はもう対応しなくてよい）
                if left_work and not include_done:
                    continue
                items.append(_item("ng", "health", names.get(h["staff_id"], "?"),
                                   f"{h['time']} {h['temp'] or ''}℃ {h['symptoms'] or ''}".strip() + ("（退勤しました）" if left_work else ""),
                                   url_for("work.health"), done=left_work))
    if on("payroll") and not staff_id:
        for first, last, n_left, n in unconfirmed_payroll():
            if n_left and (first < _month_first(today, 1) or today.day >= 5):
                items.append(_item("warn" if first == _month_first(today, 1) else "ng", "payroll", f"{first:%Y年%m月}分",
                                   f"給与が確定していない職員が {n_left}人います", url_for("payroll.index", ym=first.strftime("%Y-%m"))))
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
        set_setting("comp_days", str(max(1, min(60, request.form.get("comp_days", type=db_int) or 7))))
        set_setting("comp_trainings", (request.form.get("comp_trainings") or "").strip() or DEFAULT_TRAININGS)
        log_event("settings", "compliance", None, "実地指導チェックの設定")
        get_db().commit()
        flash("チェックの設定を保存しました。", "ok")
        return redirect(url_for("compliance.index"))
    items = run_checks()
    return render_template("compliance.html", groups=grouped(items), total=len(items), CHECKS=CHECKS, off=checks_off(),
                           days=look_days(), trainings=get_setting("comp_trainings", DEFAULT_TRAININGS))
