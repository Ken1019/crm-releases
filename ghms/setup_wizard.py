"""はじめての設定（インストール後に最初に開く画面）。

事業所の情報・サービスの類型・住居・使う機能・管理者を一度に入力してもらい、
それに合わせてシステムを整える（住居の登録、使わない機能を隠す、類型に合わない加算を外す など）。
"""

from .customize import FEATURES
from .db import now
from .entities import HOME_TYPE

MAX_HOMES = 6

# 類型ごとに、関係のない初期の加算（ほかの類型だけの加算）
ADDON_ONLY_FOR = {
    "夜勤職員加配加算": {"日中サービス支援型"},
}

# はじめから「使う」にしておく機能（ほかは画面でえらぶ）
DEFAULT_ON = {k for k, *_ in FEATURES}


def prefill(office):
    """インストーラーで入力した事業所名・番号を最初の値にする"""
    return {"office_name": office.get("name", ""), "office_no": office.get("no", ""), "unit_price": "10.00",
            "home_types": [HOME_TYPE[0]], "features": DEFAULT_ON}


def validate(form):
    errors = []
    if not form.get("office_name", "").strip():
        errors.append("事業所名を入れてください。")
    try:
        price = float(form.get("unit_price", "10") or 10)
        if not 9 <= price <= 13:
            errors.append("1単位の単価は 9〜13 円の間で入れてください（地域区分で決まります）。")
    except ValueError:
        errors.append("1単位の単価は数字で入れてください（例：10.00）。")
    if not form.getlist("home_types"):
        errors.append("サービスの類型を1つ以上えらんでください。")
    return errors


def apply(db, form, set_setting):
    """入力に合わせてシステムを整え、やったことの一覧を返す"""
    done = []
    ts = now()
    for key in ("office_name", "office_no", "office_address", "office_tel"):
        set_setting(key, form.get(key, "").strip())
    set_setting("unit_price", f"{float(form.get('unit_price') or 10):.2f}")
    done.append("事業所の情報と1単位の単価を設定しました")

    types = [t for t in form.getlist("home_types") if t in HOME_TYPE]
    homes = 0
    for i in range(1, MAX_HOMES + 1):
        name = form.get(f"home_name_{i}", "").strip()
        if not name:
            continue
        htype = form.get(f"home_type_{i}") if form.get(f"home_type_{i}") in types else types[0]
        cap = form.get(f"home_cap_{i}", "").strip()
        db.execute("INSERT INTO homes (name, home_type, capacity, created_at, updated_at, updated_by) VALUES (?,?,?,?,?,?)",
                   (name, htype, int(cap) if cap.isdigit() else None, ts, ts, "初期設定"))
        homes += 1
    if homes:
        done.append(f"住居を{homes}か所登録しました")

    on = set(form.getlist("features"))
    off = [k for k, *_ in FEATURES if k not in on]
    set_setting("features_off", ",".join(off))
    if off:
        names = {k: n for k, n, *_ in FEATURES}
        done.append("使わない機能をメニューから隠しました（" + "、".join(names[k] for k in off) + "）")

    removed = []
    for addon, only in ADDON_ONLY_FOR.items():
        if not (only & set(types)):
            cur = db.execute("DELETE FROM addons WHERE name=? AND id NOT IN (SELECT addon_id FROM resident_addons)", (addon,))
            if cur.rowcount:
                removed.append(addon)
    if removed:
        done.append("類型に関係のない加算を一覧から外しました（" + "、".join(removed) + "）")
    set_setting("home_types", ",".join(types))
    return done
