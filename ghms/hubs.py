"""「何をしたいか」で選べるメニュー。

左のメニューには目的（ハブ）だけを並べ、各ハブの画面で「〜したい」を大きなボタンで選ぶ。
ここに1行足せば、メニューとホーム画面の検索に自動で反映されます。
"""

from flask import request, url_for

from .auth import is_admin

# (ラベル, 説明, url を作る関数, 管理者のみ)
HUBS = [
    {
        "key": "daily", "icon": "📔", "title": "毎日の記録", "desc": "日誌・ヒヤリハット・会議の記録を書く・見る",
        "entities": ["daily_logs", "support_records", "incidents", "meetings", "contact_logs"], "endpoints": ["views.journal"],
        "tasks": [
            ("今日の日誌を書く", "業務日誌と入居者ごとの様子を1画面でまとめて", lambda: url_for("views.journal"), False),
            ("連絡を記録する（家族・病院・相談員など）", "電話・面会・メールの内容を残す", lambda: url_for("crud.new", key="contact_logs"), False),
            ("ヒヤリハット・事故を書く", "ヒヤッとしたら小さなことでもすぐ記録", lambda: url_for("crud.new", key="incidents"), False),
            ("会議・委員会の記録を書く", "虐待防止・身体拘束・感染症・BCPなど", lambda: url_for("crud.new", key="meetings"), False),
            ("避難訓練の記録を書く", "火災・地震などの訓練（実施日・参加者・反省点）", lambda: url_for("crud.new", key="meetings", kind="避難訓練"), False),
            ("前に書いた日誌を見る・直す", "日付や住居でさがせます", lambda: url_for("crud.index", key="daily_logs"), False),
            ("入居者の記録をさかのぼって見る", "入居者や日付でさがせます", lambda: url_for("crud.index", key="support_records"), False),
            ("ヒヤリハットの一覧を見る", "これまでの報告と再発防止策", lambda: url_for("crud.index", key="incidents"), False),
            ("会議・委員会の一覧を見る", "開催日・内容をふりかえる", lambda: url_for("crud.index", key="meetings"), False),
            ("連絡記録の一覧を見る", "入居者・相手・日付でさがせます", lambda: url_for("crud.index", key="contact_logs"), False),
        ],
    },
    {
        "key": "residents", "icon": "👤", "title": "入居者のこと", "desc": "入居者の情報・個別支援計画・入院や帰省",
        "entities": ["residents", "support_plans", "resident_documents", "absences"],
        "endpoints": ["billing.documents", "absences.index", "absences.detail"],
        "tasks": [
            ("入居者をさがす・情報を見る", "連絡先・服薬・受給者証など", lambda: url_for("crud.index", key="residents"), False),
            ("入院・帰宅（帰省）・外泊を登録する", "期間は実績に自動で入ります", lambda: url_for("crud.new", key="absences"), False),
            ("入院中・帰省中の方と連絡のやりとりを見る", "最後に連絡した日・面会の回数", lambda: url_for("absences.index"), False),
            ("新しい入居者を登録する", "入居が決まったら", lambda: url_for("crud.new", key="residents"), False),
            ("個別支援計画を作る", "目標と支援内容・計画期間", lambda: url_for("crud.new", key="support_plans"), False),
            ("個別支援計画・モニタリングを見る", "期限が近いものはホームにも出ます", lambda: url_for("crud.index", key="support_plans"), False),
            ("契約書・同意書がそろっているか見る", "足りない書類がひと目で分かります", lambda: url_for("billing.documents"), False),
            ("契約書・同意書を登録する", "署名日・更新日・保管場所", lambda: url_for("crud.new", key="resident_documents"), False),
            ("入居者の加算を登録する", "重度障害者支援加算など、人ごとの加算", lambda: url_for("crud.new", key="resident_addons"), False),
        ],
    },
    {
        "key": "staff", "icon": "🧑‍💼", "title": "職員のこと", "desc": "職員情報・キャリアパス・研修・評価",
        "entities": ["staff", "career_grades", "trainings", "training_plans", "evaluations", "shift_types"],
        "endpoints": ["views.career", "shift.index", "shift.export"],
        "admin": True,
        "tasks": [
            ("勤務表を作る", "職員ごとの勤務・常勤換算・夜間の体制", lambda: url_for("shift.index"), True),
            ("キャリアパスの全体を見る", "等級ごとの人数・職員ごとの研修や評価", lambda: url_for("views.career"), True),
            ("職員をさがす・情報を見る", "資格・入職日・等級など", lambda: url_for("crud.index", key="staff"), True),
            ("新しい職員を登録する", "入職したら", lambda: url_for("crud.new", key="staff"), True),
            ("研修を受けた記録をつける", "受講日・研修名・時間", lambda: url_for("crud.new", key="trainings"), True),
            ("今年度の研修計画を立てる", "処遇改善のキャリアパス要件Ⅱの根拠", lambda: url_for("crud.index", key="training_plans"), True),
            ("人事評価・面談を記録する", "目標・評価・昇格の推薦", lambda: url_for("crud.new", key="evaluations"), True),
            ("等級（キャリアパス）の決まりを直す", "職位・任用要件・賃金", lambda: url_for("crud.index", key="career_grades"), True),
            ("勤務の種類（日勤・夜勤など）を直す", "記号・時間・夜間かどうか", lambda: url_for("crud.index", key="shift_types"), True),
        ],
    },
    {
        "key": "money", "icon": "💴", "title": "加算・処遇改善", "desc": "加算の要件チェック・処遇改善の配分",
        "entities": ["addons", "resident_addons", "shogu_requirements", "shogu_plans", "shogu_allocations"],
        "endpoints": ["views.addon_check", "views.shogu_index", "views.shogu_plan"],
        "tasks": [
            ("今月の加算の要件をチェックする", "算定している加算の要件を毎月確認", lambda: url_for("views.addon_check"), False),
            ("算定する加算を設定する", "算定中のチェック・単位数・要件", lambda: url_for("crud.index", key="addons"), False),
            ("処遇改善の計画と配分を見る", "加算額の見込みと職員への配分", lambda: url_for("views.shogu_index"), True),
            ("処遇改善の新しい年度計画を作る", "区分・加算率・報酬の見込み", lambda: url_for("crud.new", key="shogu_plans"), True),
            ("処遇改善の要件を直す", "改定で変わったときに", lambda: url_for("crud.index", key="shogu_requirements"), True),
        ],
    },
    {
        "key": "billing", "icon": "🧾", "title": "請求・お金", "desc": "実績・給付費の確認・利用料の請求書・預り金",
        "entities": ["basic_units", "invoices", "deposits"],
        "endpoints": ["billing.attendance", "billing.benefit", "billing.benefit_export", "billing.invoices",
                      "billing.invoice_print", "billing.invoices_export", "billing.deposits", "billing.deposit_ledger"],
        "tasks": [
            ("今月の実績（在居・外泊・入院）を入れる", "請求のもとになります。月末にまとめて入れてもOK", lambda: url_for("billing.attendance"), False),
            ("給付費の概算と実績記録を見る", "国保連に請求する前の確認（加算の漏れ・入れ忘れ）", lambda: url_for("billing.benefit"), False),
            ("利用料の請求書を作る・印刷する", "家賃・食費・光熱水費・利用者負担をまとめて", lambda: url_for("billing.invoices"), True),
            ("入金を記録する・未入金を見る", "請求の一覧から入金済にします", lambda: url_for("crud.index", key="invoices", status="請求済"), True),
            ("預り金の出し入れを記録する", "お小遣い・買い物の代金など", lambda: url_for("crud.new", key="deposits"), False),
            ("預り金の残高と出納帳を見る", "入居者ごとの残高・出納帳（Excel）", lambda: url_for("billing.deposits"), False),
            ("入居者ごとの家賃・食費などを設定する", "入居者の情報の「利用料」の欄に入れます", lambda: url_for("crud.index", key="residents"), False),
            ("基本報酬の単位数を設定する", "障害支援区分ごとの1日の単位数", lambda: url_for("crud.index", key="basic_units"), False),
        ],
    },
    {
        "key": "docs", "icon": "🖨️", "title": "書類を作る", "desc": "Excelや印刷で書類を出す",
        "entities": [], "endpoints": ["views.reports", "views.journal_export", "views.career_export"],
        "tasks": [
            ("書類をえらんでExcelで出す", "日誌（月ごと）・入居者の記録・一覧表など", lambda: url_for("views.reports"), False),
        ],
        "direct": lambda: url_for("views.reports"),
    },
    {
        "key": "settings", "icon": "⚙️", "title": "設定", "desc": "事業所・住居・ログインする人・バックアップ",
        "entities": ["homes"], "endpoints": ["views.settings", "auth.users", "views.audit_log", "system.update", "auth.reauth"],
        "tasks": [
            ("住居（ユニット）を登録・変更する", "ホームの名前・定員・住所", lambda: url_for("crud.index", key="homes"), False),
            ("事業所の情報を変える", "事業所名・番号・単価・お知らせの日数", lambda: url_for("views.settings"), True),
            ("ログインする人を追加する・パスワードを変える", "職員ごとにアカウントを作ります", lambda: url_for("auth.users"), True),
            ("自分のパスワードを変える", "", lambda: url_for("auth.my_password"), False),
            ("バックアップを保存する", "データをファイルに保存（毎日のバックアップは backup.bat）", lambda: url_for("views.backup"), True),
            ("だれが何をしたか見る", "操作の記録", lambda: url_for("views.audit_log"), True),
            ("ソフトを最新の版に更新する", "新しい版の確認・ネット経由で更新", lambda: url_for("system.update"), True),
        ],
    },
]

HUB_BY_KEY = {h["key"]: h for h in HUBS}


def visible_tasks(hub):
    admin = is_admin()
    return [{"label": l, "desc": d, "url": u()} for l, d, u, adm in hub["tasks"] if admin or not adm]


def visible_hubs():
    admin = is_admin()
    return [h for h in HUBS if (admin or not h.get("admin")) and visible_tasks(h)]


def current_hub():
    """いま開いている画面がどの目的に属するか（メニューの強調・道しるべ用）"""
    ep = request.endpoint or ""
    if ep == "views.hub":
        return HUB_BY_KEY.get((request.view_args or {}).get("key"))
    key = (request.view_args or {}).get("key")
    for h in HUBS:
        if ep in h["endpoints"] or (ep.startswith("crud.") and key in h["entities"]):
            return h
    return None


def hub_url(h):
    return h["direct"]() if h.get("direct") else url_for("views.hub", key=h["key"])
