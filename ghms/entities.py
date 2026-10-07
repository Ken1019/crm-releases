"""画面・テーブル定義。

ここに項目を追加すると、DB列・一覧・入力フォーム・Excel出力に自動で反映されます。
（既存DBには起動時に不足列が ALTER TABLE で追加されます）

項目の type:
    text / textarea / date / time / number / select / ref / check
"""

GENDER = ["男", "女", "その他"]
SUPPORT_LEVEL = ["非該当", "区分1", "区分2", "区分3", "区分4", "区分5", "区分6"]
DISABILITY = ["知的障害", "精神障害", "身体障害", "発達障害", "難病等", "重複"]
HOME_TYPE = ["介護サービス包括型", "日中サービス支援型", "外部サービス利用型"]
EMPLOYMENT = ["常勤", "非常勤", "パート"]
JOB = ["管理者", "サービス管理責任者", "世話人", "生活支援員", "夜間支援従事者", "看護職員", "事務", "その他"]
STATUS_RES = ["入居中", "入院中", "退居"]
STATUS_STAFF = ["在籍", "休職", "退職"]
MEAL = ["全量", "8割", "半量", "少量", "欠食", "-"]
MED = ["済", "未", "該当なし"]
MOOD = ["良好", "普通", "不安定", "体調不良"]
TIME_SLOT = ["朝", "日中", "夕", "夜間", "終日"]
UNIT_TYPE = ["日", "月", "回"]
ADDON_KIND = ["体制加算（事業所全体）", "個別加算（利用者ごと）"]
MEETING_KIND = [
    "虐待防止委員会", "身体拘束適正化委員会", "感染症対策委員会", "業務継続計画(BCP)",
    "職員会議", "ケース会議", "地域連携推進会議", "その他",
]
TRAINING_KIND = ["内部研修", "外部研修", "OJT", "資格取得"]
INCIDENT_KIND = ["ヒヤリハット", "事故", "苦情", "その他"]
SHOGU_CATEGORY = ["Ⅰ", "Ⅱ", "Ⅲ", "Ⅳ"]
PAY_METHOD = ["基本給", "手当（毎月）", "賞与・一時金"]
PLAN_STATUS = ["作成中", "同意済", "モニタリング済", "終了"]
TRAINING_PLAN_STATUS = ["予定", "実施済", "中止"]


def F(name, label, type="text", **kw):
    d = {"name": name, "label": label, "type": type, "list": True, "required": False}
    d.update(kw)
    return d


# key: URLやテーブル名に使う識別子
ENTITIES = {
    # ---------------- 基本情報 ----------------
    "homes": {
        "guide": "グループホームの建物（ユニット）ごとに1件ずつ登録します。最初にここを登録してください。",
        "icon": "🏠",
        "title": "住居（ユニット）",
        "group": "基本情報",
        "display": "name",
        "order": "name",
        "fields": [
            F("name", "住居名", required=True),
            F("home_type", "類型", "select", options=HOME_TYPE),
            F("capacity", "定員", "number"),
            F("address", "所在地"),
            F("tel", "電話番号"),
            F("notes", "備考", "textarea", list=False),
        ],
    },
    "residents": {
        "guide": "入居者の名前をクリックすると、くわしい情報を見たり直したりできます。退居した方は「状態」を「退居」にすると一覧の下に移ります。",
        "icon": "👤",
        "title": "入居者",
        "group": "入居者",
        "display": "name",
        "order": "status, kana",
        "fields": [
            F("name", "氏名", required=True),
            F("kana", "ふりがな"),
            F("status", "状態", "select", options=STATUS_RES, default="入居中"),
            F("home_id", "住居", "ref", ref="homes"),
            F("room", "居室"),
            F("gender", "性別", "select", options=GENDER, list=False),
            F("birthdate", "生年月日", "date", list=False),
            F("disability_type", "障害種別", "select", options=DISABILITY),
            F("support_level", "障害支援区分", "select", options=SUPPORT_LEVEL),
            F("recipient_no", "受給者証番号", list=False),
            F("municipality", "支給決定市町村", list=False),
            F("cert_start", "支給決定期間（開始）", "date", list=False),
            F("cert_end", "支給決定期間（終了）", "date"),
            F("level_end", "障害支援区分有効期限", "date", list=False),
            F("move_in", "入居日", "date", list=False),
            F("move_out", "退居日", "date", list=False),
            F("day_activity", "日中活動先", list=False),
            F("doctor", "主治医・医療機関", list=False),
            F("medication", "服薬情報", "textarea", list=False),
            F("allergy", "アレルギー・禁忌", "textarea", list=False),
            F("emergency_name", "緊急連絡先（氏名・続柄）", list=False),
            F("emergency_tel", "緊急連絡先（電話）", list=False),
            F("guardian", "成年後見人等", list=False),
            F("notes", "特記事項", "textarea", list=False),
        ],
    },
    "support_plans": {
        "guide": "計画期間の終わりや次のモニタリング日を入れておくと、ホーム画面でお知らせします。",
        "icon": "📋",
        "title": "個別支援計画",
        "group": "入居者",
        "display": "period_start",
        "order": "period_end DESC",
        "fields": [
            F("resident_id", "入居者", "ref", ref="residents", required=True),
            F("status", "状態", "select", options=PLAN_STATUS, default="作成中"),
            F("created_on", "作成日", "date"),
            F("period_start", "計画期間（開始）", "date"),
            F("period_end", "計画期間（終了）", "date"),
            F("sabikan", "作成者（サビ管）"),
            F("wish", "本人・家族の意向", "textarea", list=False),
            F("long_goal", "長期目標", "textarea", list=False),
            F("short_goal", "短期目標", "textarea", list=False),
            F("support", "支援内容", "textarea", list=False),
            F("consent_date", "本人同意日", "date", list=False),
            F("next_monitoring", "次回モニタリング予定日", "date"),
            F("monitoring", "モニタリング記録", "textarea", list=False),
        ],
    },
    "support_records": {
        "guide": "ふだんは「今日の日誌を書く」画面からまとめて入力すると便利です。",
        "icon": "✍️",
        "title": "支援記録（個別）",
        "group": "日誌・記録",
        "display": "date",
        "order": "date DESC, id DESC",
        "fields": [
            F("date", "日付", "date", required=True, default="today"),
            F("resident_id", "入居者", "ref", ref="residents", required=True),
            F("time_slot", "時間帯", "select", options=TIME_SLOT),
            F("temperature", "体温", "number", step="0.1"),
            F("meal", "食事", "select", options=MEAL),
            F("medication", "服薬", "select", options=MED),
            F("mood", "様子", "select", options=MOOD),
            F("content", "記録内容", "textarea"),
            F("staff", "記録者"),
        ],
    },
    "daily_logs": {
        "guide": "ふだんは「今日の日誌を書く」画面から入力すると便利です。",
        "icon": "📔",
        "title": "業務日誌",
        "group": "日誌・記録",
        "display": "date",
        "order": "date DESC",
        "fields": [
            F("date", "日付", "date", required=True, default="today"),
            F("home_id", "住居", "ref", ref="homes"),
            F("day_staff", "日中勤務者"),
            F("night_staff", "夜間勤務者"),
            F("residents_count", "在籍者数", "number", list=False),
            F("absent", "外泊・入院者", list=False),
            F("summary", "全体の様子", "textarea"),
            F("events", "行事・来訪・通院", "textarea", list=False),
            F("handover", "申し送り", "textarea", list=False),
            F("checker", "確認者（管理者）"),
        ],
    },
    "incidents": {
        "guide": "ヒヤッとしたこと、ハッとしたことは小さなことでも記録しましょう。再発防止につながります。",
        "icon": "⚠️",
        "title": "ヒヤリハット・事故報告",
        "group": "日誌・記録",
        "display": "date",
        "order": "date DESC",
        "fields": [
            F("date", "発生日", "date", required=True, default="today"),
            F("time", "時刻", "time", list=False),
            F("kind", "区分", "select", options=INCIDENT_KIND),
            F("resident_id", "対象入居者", "ref", ref="residents"),
            F("home_id", "住居", "ref", ref="homes"),
            F("place", "場所", list=False),
            F("content", "発生状況", "textarea"),
            F("response", "対応", "textarea", list=False),
            F("cause", "原因分析", "textarea", list=False),
            F("prevention", "再発防止策", "textarea", list=False),
            F("family_report", "家族・市町村への報告", list=False),
            F("reporter", "報告者"),
        ],
    },
    "meetings": {
        "guide": "虐待防止委員会・身体拘束適正化委員会などの記録を残すと、ホーム画面に最終実施日が出ます。",
        "icon": "🗣️",
        "title": "会議・委員会記録",
        "group": "日誌・記録",
        "display": "date",
        "order": "date DESC",
        "fields": [
            F("date", "開催日", "date", required=True, default="today"),
            F("kind", "種別", "select", options=MEETING_KIND),
            F("title", "議題"),
            F("attendees", "出席者", list=False),
            F("content", "内容・決定事項", "textarea", list=False),
            F("recorder", "記録者"),
        ],
    },
    # ---------------- 職員・キャリアパス ----------------
    "career_grades": {
        "icon": "🪜",
        "title": "キャリアパス等級",
        "group": "職員・キャリアパス",
        "display": "name",
        "order": "level",
        "admin_only": True,
        "fields": [
            F("level", "等級", "number", required=True),
            F("name", "等級名称", required=True),
            F("position", "職位"),
            F("duties", "職責・職務内容", "textarea", list=False),
            F("requirements", "任用要件（経験年数・資格等）", "textarea"),
            F("trainings", "必須研修", "textarea", list=False),
            F("salary_min", "基本給 下限（円）", "number"),
            F("salary_max", "基本給 上限（円）", "number"),
            F("allowance", "役職手当（円）", "number", list=False),
            F("raise_rule", "昇給・昇格の基準", "textarea", list=False),
        ],
    },
    "staff": {
        "guide": "職員の情報です。管理者だけが見られます。",
        "icon": "🧑‍💼",
        "title": "職員",
        "group": "職員・キャリアパス",
        "display": "name",
        "order": "status, kana",
        "admin_only": True,
        "fields": [
            F("name", "氏名", required=True),
            F("kana", "ふりがな"),
            F("status", "在籍状況", "select", options=STATUS_STAFF, default="在籍"),
            F("job", "職種", "select", options=JOB),
            F("employment", "雇用形態", "select", options=EMPLOYMENT),
            F("home_id", "主な勤務住居", "ref", ref="homes", list=False),
            F("grade_id", "等級", "ref", ref="career_grades"),
            F("hire_date", "入職日", "date"),
            F("experience_years", "業界経験年数（入職前）", "number", list=False),
            F("qualifications", "保有資格", "textarea"),
            F("base_salary", "基本給（月額・円）", "number", list=False),
            F("hourly_wage", "時給（円）", "number", list=False),
            F("tel", "電話番号", list=False),
            F("notes", "備考", "textarea", list=False),
        ],
    },
    "trainings": {
        "guide": "研修を受けたら記録します。処遇改善のキャリアパス要件の根拠資料になります。",
        "icon": "🎓",
        "title": "研修受講記録",
        "group": "職員・キャリアパス",
        "display": "title",
        "order": "date DESC",
        "admin_only": True,
        "fields": [
            F("date", "受講日", "date", required=True, default="today"),
            F("staff_id", "職員", "ref", ref="staff", required=True),
            F("kind", "種別", "select", options=TRAINING_KIND),
            F("title", "研修名", required=True),
            F("hours", "時間", "number", step="0.5"),
            F("organizer", "主催", list=False),
            F("report", "受講報告・学び", "textarea", list=False),
        ],
    },
    "training_plans": {
        "icon": "🗓️",
        "title": "研修計画（年間）",
        "group": "職員・キャリアパス",
        "display": "title",
        "order": "fiscal_year DESC, month",
        "admin_only": True,
        "fields": [
            F("fiscal_year", "年度", "number", required=True),
            F("month", "実施月", "number"),
            F("title", "研修名", required=True),
            F("target", "対象（等級・職種）"),
            F("goal", "ねらい", "textarea", list=False),
            F("status", "状況", "select", options=TRAINING_PLAN_STATUS, default="予定"),
        ],
    },
    "evaluations": {
        "icon": "💬",
        "title": "人事評価・面談",
        "group": "職員・キャリアパス",
        "display": "period",
        "order": "date DESC",
        "admin_only": True,
        "fields": [
            F("date", "実施日", "date", required=True, default="today"),
            F("staff_id", "職員", "ref", ref="staff", required=True),
            F("period", "評価期間"),
            F("evaluator", "評価者"),
            F("score", "総合評価", "select", options=["S", "A", "B", "C", "D"]),
            F("goals", "目標", "textarea", list=False),
            F("self_review", "自己評価", "textarea", list=False),
            F("comment", "評価者コメント", "textarea", list=False),
            F("recommend_grade_id", "推薦等級", "ref", ref="career_grades"),
        ],
    },
    # ---------------- 加算・処遇改善 ----------------
    "addons": {
        "guide": "算定している加算は「算定中」にチェックを入れ、単位数を最新の報酬告示で確認して入力してください。",
        "icon": "➕",
        "title": "加算マスタ・算定状況",
        "group": "加算・処遇改善",
        "display": "name",
        "order": "active DESC, id",
        "fields": [
            F("name", "加算名", required=True),
            F("kind", "種類", "select", options=ADDON_KIND),
            F("units", "単位数", "number"),
            F("unit_type", "算定単位", "select", options=UNIT_TYPE),
            F("active", "算定中", "check"),
            F("notified_on", "届出日", "date"),
            F("start_on", "算定開始日", "date", list=False),
            F("requirements", "主な要件（1行1項目）", "textarea", list=False,
              help="1行ごとに要件確認画面のチェック項目になります"),
            F("evidence", "必要書類・根拠資料", "textarea", list=False),
            F("notes", "備考", "textarea", list=False),
        ],
    },
    "resident_addons": {
        "icon": "🔖",
        "title": "利用者別加算",
        "group": "加算・処遇改善",
        "display": "id",
        "order": "id DESC",
        "fields": [
            F("resident_id", "入居者", "ref", ref="residents", required=True),
            F("addon_id", "加算", "ref", ref="addons", required=True),
            F("start_on", "開始日", "date"),
            F("end_on", "終了日", "date"),
            F("notes", "備考", "textarea", list=False),
        ],
    },
    "shogu_requirements": {
        "icon": "✅",
        "title": "処遇改善 要件マスタ",
        "group": "加算・処遇改善",
        "display": "name",
        "order": "sort",
        "admin_only": True,
        "fields": [
            F("sort", "並び順", "number"),
            F("name", "要件名", required=True),
            F("applies_to", "対象区分（例: Ⅰ,Ⅱ,Ⅲ）"),
            F("description", "内容", "textarea"),
        ],
    },
    "shogu_plans": {
        "guide": "年度ごとに1件作ります。作ったあと「処遇改善」画面で職員への配分を入れます。",
        "icon": "💴",
        "title": "処遇改善 計画",
        "group": "加算・処遇改善",
        "display": "fiscal_year",
        "order": "fiscal_year DESC",
        "admin_only": True,
        "fields": [
            F("fiscal_year", "年度", "number", required=True),
            F("category", "加算区分", "select", options=SHOGU_CATEGORY),
            F("rate", "加算率（%）", "number", step="0.1",
              help="サービス類型ごとの最新の加算率を通知で確認して入力してください"),
            F("revenue", "年間 報酬総額見込（円・処遇改善加算を除く）", "number"),
            F("plan_submitted", "計画書提出日", "date", list=False),
            F("report_submitted", "実績報告提出日", "date", list=False),
            F("checked", "達成済み要件", "hidden", list=False),
            F("notes", "備考", "textarea", list=False),
        ],
    },
    "shogu_allocations": {
        "icon": "👛",
        "title": "処遇改善 職員別配分",
        "group": "加算・処遇改善",
        "display": "id",
        "order": "plan_id DESC, id",
        "admin_only": True,
        "fields": [
            F("plan_id", "計画（年度）", "ref", ref="shogu_plans", required=True),
            F("staff_id", "職員", "ref", ref="staff", required=True),
            F("method", "支給方法", "select", options=PAY_METHOD),
            F("monthly", "月額（円）", "number", help="賞与・一時金の場合は年額を12で割らずに『年額』欄へ"),
            F("annual_lump", "年額（賞与・一時金・円）", "number"),
            F("notes", "備考", "textarea", list=False),
        ],
    },
}

GROUPS = ["入居者", "日誌・記録", "職員・キャリアパス", "加算・処遇改善", "基本情報"]

GROUP_ICONS = {"入居者": "👤", "日誌・記録": "📔", "職員・キャリアパス": "🧑‍💼", "加算・処遇改善": "💴", "基本情報": "🏠"}


def entity(key):
    return ENTITIES[key]


def field_map(key):
    return {f["name"]: f for f in ENTITIES[key]["fields"]}
