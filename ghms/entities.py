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
    "避難訓練", "虐待防止研修", "身体拘束適正化研修", "感染症研修",
    "職員会議", "ケース会議", "地域連携推進会議", "その他",
]
TRAINING_KIND = ["内部研修", "外部研修", "OJT", "資格取得"]
INCIDENT_KIND = ["ヒヤリハット", "事故", "苦情", "その他"]
ACTIVITY_KIND = ["外出", "誕生日会", "バーベキュー", "季節の行事", "買い物", "外食", "散歩", "創作活動", "運動・体操",
                 "地域の行事", "その他"]
SHOGU_CATEGORY = ["Ⅰ", "Ⅱ", "Ⅲ", "Ⅳ"]
PAY_METHOD = ["基本給", "手当（毎月）", "賞与・一時金"]
PAY_TYPE = ["月給", "時給", "日給"]
EXPENSE_KIND = ["家賃・地代", "水道光熱費", "食材費", "日用品・消耗品", "車両・ガソリン", "修繕費", "通信費", "保険料", "研修費", "委託料", "その他"]
PLAN_STATUS = ["作成中", "同意済", "モニタリング済", "終了"]
INCOME_CLASS = ["生活保護", "低所得", "一般1", "一般2"]
FOOD_TYPE = ["日額（食べた日数で計算）", "月額"]
PAY_METHOD_RES = ["口座振替", "振込", "現金", "預り金から"]
INVOICE_STATUS = ["未請求", "請求済", "入金済"]
ABSENCE_KIND = ["入院", "帰宅（帰省）", "外泊", "その他"]
ABSENCE_STATUS = ["予定", "不在中", "戻った"]
COUNTERPART = ["家族", "医療機関・病院", "相談支援専門員", "日中活動先", "市町村", "成年後見人等", "その他"]
CONTACT_METHOD = ["電話", "面会・訪問", "メール・FAX", "来所", "その他"]
CONTACT_DIRECTION = ["こちらから", "先方から"]
DEPOSIT_KIND = ["入金", "出金"]
DOC_TYPE = [
    "利用契約書", "重要事項説明書", "個人情報使用同意書", "個別支援計画への同意", "受給者証の写し",
    "緊急連絡先届", "金銭管理（預り金）契約", "身体拘束等に関する同意", "その他",
]
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
            F("rent", "家賃（月額・円）", "number", list=False, section="利用料（請求書に使います）"),
            F("rent_subsidy", "家賃助成（補足給付・月額・円）", "number", list=False,
              help="特定障害者特別給付費など、家賃から差し引く額"),
            F("utility", "光熱水費（月額・円）", "number", list=False),
            F("daily_goods", "日用品費（月額・円）", "number", list=False),
            F("food_type", "食費の計算方法", "select", options=FOOD_TYPE, list=False),
            F("food_amount", "食費（円）", "number", list=False, help="日額なら1日分、月額なら1か月分"),
            F("income_class", "所得区分", "select", options=INCOME_CLASS, list=False),
            F("burden_cap", "利用者負担上限月額（円）", "number", list=False, help="受給者証に書かれている上限額"),
            F("pay_method", "支払方法", "select", options=PAY_METHOD_RES, list=False),
        ],
    },
    "absences": {
        "icon": "🏥",
        "guide": "入院・帰宅（帰省）・外泊を登録します。期間は「実績」に自動で入り、入院中・帰省中の方はホームと日誌の画面に表示されます。連絡したときは「連絡を記録する」から残しましょう。",
        "title": "入院・帰宅・外泊",
        "group": "入居者",
        "display": "kind",
        "order": "CASE status WHEN '不在中' THEN 0 WHEN '予定' THEN 1 ELSE 2 END, start_date DESC",
        "fields": [
            F("resident_id", "入居者", "ref", ref="residents", required=True),
            F("kind", "種類", "select", options=ABSENCE_KIND, required=True),
            F("status", "状態", "select", options=ABSENCE_STATUS, default="不在中", help="戻った日を入れると自動で「戻った」になります"),
            F("start_date", "出発した日（入院した日）", "date", required=True, default="today"),
            F("end_plan", "戻る予定日", "date"),
            F("end_date", "戻った日（退院した日）", "date"),
            F("place", "行き先（病院名・帰省先）"),
            F("place_tel", "行き先の電話番号", list=False),
            F("contact_person", "先方の担当者（主治医・家族など）", list=False),
            F("reason", "理由・病名・目的", "textarea", list=False),
            F("belongings", "持ち物・お薬・預けたもの", "textarea", list=False),
            F("auto_attendance", "実績（在居・入院など）に自動で反映する", "check", list=False, default=1,
              help="出発した日と戻った日は「在居」のまま、その間の日を入院・帰宅・外泊にします。算定の扱いは報酬告示で確認してください"),
            F("notes", "備考", "textarea", list=False),
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
    "contact_logs": {
        "icon": "📞",
        "guide": "家族・病院・相談支援専門員などとの連絡を残します。入院中・帰省中の連絡は、その入院・帰省を選ぶとまとめて見られます。",
        "title": "連絡記録",
        "group": "日誌・記録",
        "display": "date",
        "order": "date DESC, time DESC, id DESC",
        "fields": [
            F("date", "日付", "date", required=True, default="today"),
            F("time", "時刻", "time"),
            F("resident_id", "入居者", "ref", ref="residents"),
            F("absence_id", "入院・帰宅（関係するとき）", "ref", ref="absences", list=False),
            F("counterpart", "連絡した相手", "select", options=COUNTERPART),
            F("counterpart_name", "相手の名前・所属", list=False),
            F("method", "方法", "select", options=CONTACT_METHOD),
            F("direction", "どちらから", "select", options=CONTACT_DIRECTION, list=False),
            F("content", "連絡の内容", "textarea", required=True),
            F("next_action", "今後の対応・申し送り", "textarea", list=False),
            F("staff", "対応した職員"),
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
    "activities": {
        "icon": "🎉",
        "guide": "外出・誕生日会・バーベキューなどの行事やレクリエーションを記録します。参加した入居者をえらぶと、入居者ごとの記録にも出ます。",
        "title": "行事・レクリエーション",
        "group": "日誌・記録",
        "display": "title",
        "order": "date DESC, id DESC",
        "fields": [
            F("date", "日付", "date", required=True, default="today"),
            F("time", "時間", list=False),
            F("kind", "種類", "select", options=ACTIVITY_KIND),
            F("title", "行事の名前", required=True, help="例：〇〇さんの誕生日会、〇〇公園でお花見"),
            F("home_id", "住居", "ref", ref="homes"),
            F("place", "場所"),
            F("participants", "参加した入居者", "multiref", ref="residents"),
            F("staff", "担当した職員"),
            F("cost", "費用の合計（円）", "number", list=False),
            F("cost_note", "費用の内訳・だれが払ったか", list=False, help="例：材料費 3,000円（事業所負担）、入場料は各自の預り金から"),
            F("content", "内容・入居者のようす", "textarea", list=False),
            F("reflection", "感想・次回への申し送り", "textarea", list=False),
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
            F("birthdate", "生年月日", "date", list=False, section="指定申請・更新の書類に使う情報（経歴書など）"),
            F("career_history", "職歴（年月と勤務先・仕事の内容）", "textarea", list=False,
              help="例：2015年4月〜2019年3月　〇〇福祉会 生活介護事業所 生活支援員"),
            F("certified_trainings", "修了した研修（研修名と修了日）", "textarea", list=False,
              help="例：サービス管理責任者等基礎研修 2020年11月修了／実践研修 2022年12月修了"),
            F("weekly_hours", "1週間の勤務時間（時間）", "number", list=False, help="勤務体制一覧表・従業者の一覧に使います"),
            F("pay_type", "給与の形", "select", options=PAY_TYPE, default="月給", list=False, section="給与計算に使う情報"),
            F("daily_wage", "日給（円）", "number", list=False),
            F("allowance_qual", "資格手当（月額・円）", "number", list=False),
            F("allowance_other", "その他の手当（月額・円）", "number", list=False),
            F("commute", "通勤手当（月額・円・非課税）", "number", list=False),
            F("night_allowance", "夜勤手当（1回・円）", "number", list=False, help="タイムカードで日をまたいだ勤務（夜勤）1回ごとに付きます"),
            F("dependents", "扶養親族等の数（源泉徴収）", "number", list=False, help="扶養控除等申告書の人数。所得税の概算に使います"),
            F("resident_tax", "住民税（月額・円）", "number", list=False, help="市区町村からの特別徴収税額通知書の金額"),
            F("social_insurance", "社会保険（健康保険・厚生年金）に加入", "check", list=False),
            F("std_monthly", "標準報酬月額（円）", "number", list=False, help="空欄なら毎月の総支給額で計算します（概算）"),
            F("employment_insurance", "雇用保険に加入", "check", list=False),
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
    # ---------------- 請求・お金 ----------------
    "basic_units": {
        "icon": "🧮",
        "guide": "共同生活援助サービス費（基本報酬）の1日あたりの単位数です。最新の報酬告示で確認して入力してください。給付費の概算に使います。",
        "title": "基本報酬の単位数",
        "group": "請求・お金",
        "display": "label",
        "order": "home_type, support_level",
        "fields": [
            F("home_type", "類型", "select", options=HOME_TYPE),
            F("support_level", "障害支援区分", "select", options=SUPPORT_LEVEL, required=True),
            F("label", "名称（人員配置など）"),
            F("units", "単位数（1日）", "number", required=True),
            F("active", "使う", "check", default=1),
        ],
    },
    "expenses": {
        "icon": "🧮",
        "guide": "給与以外にかかったお金（住居の家賃・光熱水費・食材費など）を記録します。「事業所の収支」に使います。",
        "title": "経費",
        "group": "請求・お金",
        "display": "item",
        "order": "date DESC, id DESC",
        "admin_only": True,
        "fields": [
            F("date", "日付", "date", required=True, default="today"),
            F("kind", "費目", "select", options=EXPENSE_KIND, required=True),
            F("item", "内容", required=True, help="例：10月分 電気代"),
            F("amount", "金額（円）", "number", required=True),
            F("home_id", "住居", "ref", ref="homes"),
            F("payee", "支払先", list=False),
            F("notes", "備考", "textarea", list=False),
        ],
    },
    "invoices": {
        "icon": "🧾",
        "guide": "利用料の請求です。「請求書を作る」画面で月ごとにまとめて作れます。入金があったら「状態」を「入金済」にしてください。",
        "title": "利用料の請求",
        "group": "請求・お金",
        "display": "ym",
        "order": "ym DESC, id",
        "admin_only": True,
        "fields": [
            F("ym", "請求月（例 2026-10）", required=True),
            F("resident_id", "入居者", "ref", ref="residents", required=True),
            F("status", "状態", "select", options=INVOICE_STATUS, default="未請求"),
            F("issue_date", "発行日", "date", list=False),
            F("due_date", "支払期限", "date"),
            F("rent", "家賃", "number", list=False),
            F("rent_subsidy", "家賃助成（差引）", "number", list=False),
            F("food", "食費", "number", list=False),
            F("utility", "光熱水費", "number", list=False),
            F("daily_goods", "日用品費", "number", list=False),
            F("user_burden", "利用者負担額（障害福祉サービス）", "number", list=False),
            F("other_label", "その他の内容", list=False),
            F("other_amount", "その他の金額", "number", list=False),
            F("total", "請求額（自動計算）", "number", help="保存すると自動で計算されます"),
            F("paid_on", "入金日", "date"),
            F("paid_amount", "入金額", "number", list=False),
            F("pay_method", "支払方法", "select", options=PAY_METHOD_RES, list=False),
            F("notes", "備考", "textarea", list=False),
        ],
    },
    "deposits": {
        "icon": "👛",
        "guide": "入居者からお預かりしているお金の出し入れです。レシートは必ず保管し、定期的に本人・家族に残高を報告しましょう。",
        "title": "預り金の出し入れ",
        "group": "請求・お金",
        "display": "date",
        "order": "date DESC, id DESC",
        "fields": [
            F("date", "日付", "date", required=True, default="today"),
            F("resident_id", "入居者", "ref", ref="residents", required=True),
            F("kind", "入金／出金", "select", options=DEPOSIT_KIND, required=True),
            F("amount", "金額（円）", "number", required=True),
            F("purpose", "内容（買ったもの・入金元）"),
            F("receipt", "レシートあり", "check"),
            F("staff", "対応した職員"),
            F("checker", "確認者", list=False),
        ],
    },
    # ---------------- 勤務表 ----------------
    "shift_types": {
        "icon": "🕘",
        "guide": "勤務表で使う勤務の種類です。時間数は常勤換算の計算に使います。",
        "title": "勤務の種類",
        "group": "職員・キャリアパス",
        "display": "code",
        "order": "sort, id",
        "admin_only": True,
        "fields": [
            F("sort", "並び順", "number"),
            F("code", "記号（1〜2文字）", required=True),
            F("name", "名前", required=True),
            F("start", "開始", "time"),
            F("end", "終了", "time"),
            F("hours", "勤務時間（休憩を除く）", "number", step="0.25"),
            F("night", "夜間の勤務（夜勤・宿直）", "check"),
        ],
    },
    # ---------------- 書類 ----------------
    "resident_documents": {
        "icon": "📑",
        "guide": "契約書・同意書などの書類を登録します。「書類がそろっているか」の画面で、足りない書類が分かります。",
        "title": "契約書・同意書",
        "group": "入居者",
        "display": "doc_type",
        "order": "resident_id, doc_type",
        "fields": [
            F("resident_id", "入居者", "ref", ref="residents", required=True),
            F("doc_type", "書類の種類", "select", options=DOC_TYPE, required=True),
            F("signed_on", "署名・同意日", "date"),
            F("expires_on", "有効期限・更新日", "date"),
            F("place", "保管場所"),
            F("notes", "備考", "textarea", list=False),
        ],
    },
}

GROUPS = ["入居者", "日誌・記録", "職員・キャリアパス", "加算・処遇改善", "請求・お金", "基本情報"]

GROUP_ICONS = {"入居者": "👤", "日誌・記録": "📔", "職員・キャリアパス": "🧑‍💼", "加算・処遇改善": "💴", "請求・お金": "🧾", "基本情報": "🏠"}


def entity(key):
    return ENTITIES[key]


def field_map(key):
    return {f["name"]: f for f in ENTITIES[key]["fields"]}
