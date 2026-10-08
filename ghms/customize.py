"""事業所ごとのカスタマイズ。

- 使う機能・使わない機能（使わない機能はメニュー・ホーム・検索から消え、画面も開けない）
- 選択肢の管理（会議の種類・研修の種類など。追加・使わない・並べ替え。会議はホームに出す日数も）
- 独自の項目（入居者・日誌などに、事業所で必要な項目を追加。一覧・入力・Excel出力に自動で反映）
"""

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from .auth import admin_required, log_event
from .db import get_db, get_setting, now, set_setting
from .entities import ENTITIES, F

bp = Blueprint("customize", __name__, url_prefix="/settings")

# ---------------------------------------------------------------- 使う機能
# (キー, 名前, 説明, その機能の画面のURLの先頭)
FEATURES = [
    ("absences", "入院・帰宅・外泊", "入院や帰省の期間と、そのあいだの連絡のやりとり", ["/absences", "/m/absences"]),
    ("contact_logs", "連絡記録", "家族・病院・相談支援専門員などとの連絡", ["/m/contact_logs"]),
    ("incidents", "ヒヤリハット・事故報告", "", ["/m/incidents"]),
    ("activities", "行事・レクリエーションの記録", "外出・誕生日会・バーベキューなど", ["/m/activities"]),
    ("meetings", "会議・委員会・避難訓練の記録", "虐待防止委員会などの最終実施日をホームに表示", ["/m/meetings"]),
    ("documents", "契約書・同意書の管理", "書類がそろっているかの一覧・更新日のお知らせ", ["/billing/documents", "/m/resident_documents"]),
    ("career", "キャリアパス・研修・人事評価", "処遇改善のキャリアパス要件の根拠にもなります",
     ["/career", "/m/career_grades", "/m/trainings", "/m/training_plans", "/m/evaluations"]),
    ("shift", "勤務表・常勤換算", "", ["/shift", "/m/shift_types"]),
    ("addons", "加算の管理・毎月の要件チェック", "", ["/addons", "/m/addons", "/m/resident_addons"]),
    ("shogu", "処遇改善加算", "加算額の見込みと職員への配分", ["/shogu", "/m/shogu_"]),
    ("billing", "実績と給付費の概算", "国保連に請求する前の確認", ["/billing/attendance", "/billing/benefit", "/m/basic_units", "/docs/record-marks"]),
    ("invoices", "利用料の請求書・入金管理", "家賃・食費などの請求書と領収書", ["/billing/invoice", "/m/invoices"]),
    ("deposits", "預り金の管理", "お小遣いなどの出し入れと出納帳", ["/billing/deposits", "/m/deposits"]),
    ("menus", "献立表", "1週間の献立を入力・印刷", ["/docs/menus"]),
]
FEATURE_BY_KEY = {k: (k, n, d, p) for k, n, d, p in FEATURES}


def features_off():
    return {k for k in (get_setting("features_off", "") or "").split(",") if k in FEATURE_BY_KEY}


def feature_on(key):
    return key not in features_off()


def path_disabled(path):
    off = features_off()
    return any(path.startswith(prefix) for k in off for prefix in FEATURE_BY_KEY[k][3])


def entity_on(key):
    return not path_disabled(f"/m/{key}/")


# ---------------------------------------------------------------- 選択肢の管理
# 選択肢を事業所で変えられる項目（"テーブル.項目" → 画面での呼び名）
CHOICE_FIELDS = {
    "meetings.kind": "会議・委員会・訓練の種類",
    "trainings.kind": "研修の種類",
    "incidents.kind": "ヒヤリハット・事故の区分",
    "activities.kind": "行事・レクリエーションの種類",
    "contact_logs.counterpart": "連絡した相手",
    "contact_logs.method": "連絡の方法",
    "absences.kind": "入院・帰宅・外泊の種類",
    "resident_documents.doc_type": "契約書・同意書の種類",
    "staff.job": "職種",
    "residents.disability_type": "障害種別",
}
# ホームに「最終実施日」を出す会議と、注意を出すまでの日数（はじめの値）
DEFAULT_TRACK = {"虐待防止委員会": 365, "身体拘束適正化委員会": 365, "感染症対策委員会": 365,
                 "業務継続計画(BCP)": 365, "避難訓練": 183}


def _field(fkey):
    ent, name = fkey.split(".")
    return next(f for f in ENTITIES[ent]["fields"] if f["name"] == name)


def seed_choices(con):
    """コードに書いたはじめの選択肢をDBに入れる（すでにあるものはそのまま）"""
    for fkey in CHOICE_FIELDS:
        f = _field(fkey)
        f["choices"] = fkey
        for i, v in enumerate(f.get("options") or []):
            track = DEFAULT_TRACK.get(v) if fkey == "meetings.kind" else None
            con.execute("INSERT OR IGNORE INTO choice_options (field, value, sort, active, track_days) VALUES (?,?,?,1,?)",
                        (fkey, v, (i + 1) * 10, track))


def choice_rows(fkey):
    return get_db().execute("SELECT * FROM choice_options WHERE field=? ORDER BY sort, value", (fkey,)).fetchall()


def options_for(f, current=None):
    """入力・絞り込みに出す選択肢。使わない設定にしたものは出さない（ただし今の値は残す）"""
    if not f.get("choices"):
        opts = list(f.get("options") or [])
    else:
        opts = [r["value"] for r in choice_rows(f["choices"]) if r["active"]]
    if current not in (None, "") and current not in opts:
        opts.append(current)
    return opts


def allowed_values(f):
    if f.get("choices"):
        return {r["value"] for r in choice_rows(f["choices"])}
    return set(f.get("options") or [])


def tracked_meetings():
    return [(r["value"], r["track_days"]) for r in choice_rows("meetings.kind") if r["active"] and r["track_days"]]


# ---------------------------------------------------------------- 独自の項目
CUSTOM_ENTITIES = ["residents", "support_plans", "support_records", "daily_logs", "contact_logs", "absences",
                   "incidents", "meetings", "staff", "trainings", "evaluations", "homes"]
CUSTOM_TYPES = {"text": "文字（1行）", "textarea": "文章（複数行）", "date": "日付", "number": "数字",
                "select": "えらぶ（選択肢）", "check": "チェック（はい／いいえ）"}


def apply_custom_fields(con):
    """DBに登録した独自の項目を ENTITIES に反映し、列がなければ追加する"""
    rows = con.execute("SELECT id, entity, label, type, options, list_show, active FROM custom_fields"
                       " ORDER BY entity, sort, id").fetchall()
    for ent in ENTITIES.values():
        ent["fields"] = [f for f in ent["fields"] if not f.get("custom")]
    first = set()
    for cid, key, label, ftype, options, list_show, active in rows:
        if key not in ENTITIES:
            continue
        col = f"cf_{cid}"
        existing = {c[1] for c in con.execute(f'PRAGMA table_info("{key}")')}
        if col not in existing:
            sqltype = {"number": "REAL", "check": "INTEGER"}.get(ftype, "TEXT")
            con.execute(f'ALTER TABLE "{key}" ADD COLUMN "{col}" {sqltype}')
        if not active:
            continue
        f = F(col, label, ftype, custom=True, list=bool(list_show))
        if ftype == "select":
            f["options"] = [o.strip() for o in (options or "").splitlines() if o.strip()]
        if key not in first:
            f["section"] = "独自の項目"
            first.add(key)
        ENTITIES[key]["fields"].append(f)
    con.commit()


# ---------------------------------------------------------------- 画面
def install(app):
    @app.before_request
    def block_disabled_features():
        if g.get("user") and path_disabled(request.path):
            flash("この機能は「使わない」設定になっています。管理者が「設定」→「使う機能をえらぶ」で変更できます。", "error")
            return redirect(url_for("views.dashboard"))
        return None

    @app.context_processor
    def helpers():
        return {"feature_on": feature_on if g.get("user") else (lambda k: True), "opts": options_for,
                "entity_on": entity_on if g.get("user") else (lambda k: True)}


@bp.route("/features", methods=["GET", "POST"])
@admin_required
def features():
    if request.method == "POST":
        on = set(request.form.getlist("on"))
        off = [k for k, *_ in FEATURES if k not in on]
        set_setting("features_off", ",".join(off))
        log_event("settings", detail="使う機能: " + "、".join(FEATURE_BY_KEY[k][1] for k in on) if on else "使う機能: なし")
        get_db().commit()
        flash("使う機能を保存しました。メニューとホームに反映しました。", "ok")
        return redirect(url_for("customize.features"))
    return render_template("custom_features.html", features=FEATURES, off=features_off())


@bp.route("/choices", methods=["GET", "POST"])
@admin_required
def choices():
    db = get_db()
    fkey = request.values.get("field") or next(iter(CHOICE_FIELDS))
    if fkey not in CHOICE_FIELDS:
        abort(404)
    if request.method == "POST":
        for r in choice_rows(fkey):
            i = r["value"]
            active = 1 if request.form.get(f"active::{i}") else 0
            try:
                sort = int(request.form.get(f"sort::{i}") or r["sort"])
            except ValueError:
                sort = r["sort"]
            track = request.form.get(f"track::{i}", "").strip()
            track = int(track) if track.isdigit() and int(track) > 0 else None
            db.execute("UPDATE choice_options SET active=?, sort=?, track_days=? WHERE field=? AND value=?",
                       (active, sort, track if fkey == "meetings.kind" else None, fkey, i))
        new = request.form.get("new", "").strip()
        if new:
            if len(new) > 40:
                flash("選択肢は40文字以内にしてください。", "error")
            else:
                mx = db.execute("SELECT COALESCE(MAX(sort), 0) FROM choice_options WHERE field=?", (fkey,)).fetchone()[0]
                track = request.form.get("new_track", "").strip()
                db.execute("INSERT OR IGNORE INTO choice_options (field, value, sort, active, track_days) VALUES (?,?,?,1,?)",
                           (fkey, new, mx + 10, int(track) if fkey == "meetings.kind" and track.isdigit() else None))
                flash(f"「{new}」を追加しました。", "ok")
        log_event("settings", detail=f"選択肢: {CHOICE_FIELDS[fkey]}")
        db.commit()
        if not new:
            flash("保存しました。", "ok")
        return redirect(url_for("customize.choices", field=fkey))
    counts = {}
    ent, name = fkey.split(".")
    for v, n in db.execute(f'SELECT "{name}", COUNT(*) FROM "{ent}" GROUP BY "{name}"'):
        counts[v] = n
    return render_template("custom_choices.html", fields=CHOICE_FIELDS, fkey=fkey, rows=choice_rows(fkey), counts=counts)


@bp.route("/fields", methods=["GET", "POST"])
@admin_required
def fields():
    db = get_db()
    ent = request.values.get("entity") or CUSTOM_ENTITIES[0]
    if ent not in CUSTOM_ENTITIES:
        abort(404)
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            label = request.form.get("label", "").strip()
            ftype = request.form.get("type", "text")
            if not label or ftype not in CUSTOM_TYPES:
                flash("項目の名前と種類を入れてください。", "error")
            elif ftype == "select" and not request.form.get("options", "").strip():
                flash("「えらぶ」の項目には、選択肢を1行に1つずつ入れてください。", "error")
            else:
                mx = db.execute("SELECT COALESCE(MAX(sort), 0) FROM custom_fields WHERE entity=?", (ent,)).fetchone()[0]
                db.execute("INSERT INTO custom_fields (entity, label, type, options, list_show, sort, active, created_at)"
                           " VALUES (?,?,?,?,?,?,1,?)",
                           (ent, label[:40], ftype, request.form.get("options", ""), 1 if request.form.get("list_show") else 0,
                            mx + 10, now()))
                flash(f"「{label}」を追加しました。{ENTITIES[ent]['title']}の入力画面のいちばん下に出ます。", "ok")
        elif action == "update":
            cid = request.form.get("id", type=int)
            db.execute("UPDATE custom_fields SET label=?, options=?, list_show=?, active=? WHERE id=? AND entity=?",
                       (request.form.get("label", "").strip()[:40] or "（名前なし）", request.form.get("options", ""),
                        1 if request.form.get("list_show") else 0, 1 if request.form.get("active") else 0, cid, ent))
            flash("保存しました。", "ok")
        log_event("settings", detail=f"独自の項目: {ENTITIES[ent]['title']}")
        db.commit()
        apply_custom_fields(db)
        return redirect(url_for("customize.fields", entity=ent))
    rows = db.execute("SELECT * FROM custom_fields WHERE entity=? ORDER BY sort, id", (ent,)).fetchall()
    return render_template("custom_fields.html", ent_key=ent, entities=CUSTOM_ENTITIES, rows=rows, types=CUSTOM_TYPES)
