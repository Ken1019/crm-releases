"""入力された数字を安全に読むための道具（画面の入力・設定の値）。

まちがった値・とても大きい値（9999999999999999999 など）が入っても、
画面がエラー（500）にならないようにする。
"""

import math
import unicodedata

# データベース（SQLite）に入れられる整数の範囲より少し小さくしておく
DB_INT_MAX = 2 ** 62
# ふつうの数値の欄（金額・時間など）で受けつける大きさ
NUM_MAX = 1e12

# 数字のうしろについていてもよい単位など（全角は NFKC で半角にしてから外す）
_UNITS = ("時間", "%", "円", "日", "分", "回", "℃", "°C", "度", ",", " ")


def normalize_num(value):
    """「１０．２８」「9.85%」「1,131円」「60日」→ 数字だけの文字（"10.28" など）にする"""
    s = unicodedata.normalize("NFKC", str(value if value is not None else "")).strip()
    for u in _UNITS:
        s = s.replace(u, "")
    return s.replace("　", "").strip()


def parse_number(value, lo=None, hi=None, integer=False):
    """数字として読む。読めない・無限大・範囲の外なら ValueError"""
    s = normalize_num(value)
    if not s:
        raise ValueError("empty")
    n = float(s)
    if not math.isfinite(n):
        raise ValueError("not finite")
    if integer:
        if not n.is_integer():
            raise ValueError("not integer")
        n = int(n)
    if (lo is not None and n < lo) or (hi is not None and n > hi):
        raise ValueError("out of range")
    return n


def safe_int(value, default=None, lo=-DB_INT_MAX, hi=DB_INT_MAX):
    """整数として読む。読めない・範囲の外なら default"""
    try:
        return parse_number(value, lo, hi, integer=True)
    except (TypeError, ValueError, OverflowError):
        return default


def safe_float(value, default=None, lo=-NUM_MAX, hi=NUM_MAX):
    try:
        return parse_number(value, lo, hi)
    except (TypeError, ValueError, OverflowError):
        return default


def clamp(n, lo, hi):
    return max(lo, min(hi, n))


def db_int(value):
    """request.args.get(..., type=db_int) 用。データベースに入らない大きな数は「なし」と同じにする"""
    v = int(str(value).strip())
    if not -DB_INT_MAX < v < DB_INT_MAX:
        raise ValueError("out of range")
    return v


def positive_id(value):
    """番号（id）用。1以上の整数だけ"""
    v = db_int(value)
    if v < 1:
        raise ValueError("not positive")
    return v


def finite_float(value):
    """request.form.get(..., type=finite_float) 用。nan・inf・とても大きい値は「なし」"""
    v = float(str(value).strip())
    if not math.isfinite(v) or abs(v) > NUM_MAX:
        raise ValueError("out of range")
    return v


# ---------------------------------------------------------------- 数字の設定
# 設定のキー: (いちばん小さい値, いちばん大きい値, 整数だけか)
NUM_SETTINGS = {
    # 事業所の設定
    "unit_price": (1, 100, False),
    "cert_alert_days": (0, 3650, True),
    "plan_alert_days": (0, 3650, True),
    "invoice_due_day": (1, 31, True),
    "full_time_hours": (1, 744, False),
    "session_timeout_min": (1, 1440, True),
    "pin_timeout_min": (1, 1440, True),
    "comp_days": (1, 60, True),
    # 給与・タイムカードの設定
    "pay_monthly_hours": (1, 744, False),
    "pay_ot_rate": (0, 200, False),
    "pay_yakin_flat": (0, 10_000_000, False),
    "pay_min_wage": (0, 100_000, False),
    "pay_night_rate": (0, 200, False),
    "pay_break_default": (0, 600, True),
    "pay_max_shift_hours": (1, 48, False),
    "pay_forgot_hours": (1, 48, False),
    "pay_fever": (34, 43, False),
    "pay_late_grace": (0, 600, True),
    "ins_health": (0, 100, False),
    "ins_care": (0, 100, False),
    "ins_kodomo": (0, 100, False),
    "ins_pension": (0, 100, False),
    "ins_emp_ee": (0, 100, False),
    "ins_emp_er": (0, 100, False),
    "ins_child": (0, 100, False),
    "ins_rosai": (0, 100, False),
}


def check_setting(key, raw):
    """画面から入った設定の値を確かめる。(保存する文字, まちがいのときの説明 or None)
    空欄は「初期値を使う」としてそのまま空にする"""
    lo, hi, integer = NUM_SETTINGS[key]
    if not normalize_num(raw):
        return "", None
    try:
        n = parse_number(raw, lo, hi, integer)
    except (TypeError, ValueError, OverflowError):
        kind = "整数" if integer else "数字"
        return None, f"{kind}で、{lo:g}〜{hi:g}の間で入れてください"
    return (str(n) if integer else normalize_num(raw)), None


def setting_number(key, default):
    """保存されている数字の設定を読む。まちがった値が入っていても初期値を使い、範囲の中におさめる"""
    from .db import get_setting

    lo, hi, integer = NUM_SETTINGS.get(key, (-NUM_MAX, NUM_MAX, False))
    try:
        n = parse_number(get_setting(key, str(default)))
    except (TypeError, ValueError, OverflowError):
        n = float(default)
    n = clamp(n, lo, hi)
    return int(n) if integer else float(n)
