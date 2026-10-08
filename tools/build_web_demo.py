"""サンプルデータ入りで実際の画面を描画し、静的なWebデモ（複数ページのHTML）として書き出す。

    python tools/build_web_demo.py . 出力フォルダ

架空のサンプルデータを一時フォルダのDBに入れて描画するため、本番のデータには触れません。
"""
import html as htmlmod
import os
import re
import shutil
import sys
import tempfile
from collections import Counter, deque
from datetime import date, timedelta
from urllib.parse import urlsplit

repo, out = sys.argv[1], sys.argv[2]
sys.path.insert(0, repo)
os.environ["GHMS_DATA_DIR"] = tempfile.mkdtemp()
from ghms import create_app  # noqa: E402

app = create_app({"TESTING": True})
c = app.test_client()
c.post("/setup", data={"office_name": "グループホーム ひだまり（デモ）", "username": "admin", "display_name": "管理者",
                       "password": "password123"})


def token():
    m = re.search(r'name="_csrf" value="([0-9a-f]+)"', c.get("/").get_data(as_text=True))
    return m.group(1)


TOKEN = token()


def post(path, data):
    r = c.post(path, data=dict(data, _csrf=TOKEN))
    assert r.status_code in (200, 302), (path, r.status_code)
    return r


today = date.today()
ym = today.strftime("%Y-%m")
d = lambda n: (today + timedelta(days=n)).isoformat()  # noqa: E731

# ---------------- サンプルデータ（すべて架空） ----------------
post("/settings", {"office_name": "グループホーム ひだまり（デモ）", "office_no": "0000000000", "unit_price": "10.00",
                   "cert_alert_days": "60", "plan_alert_days": "30", "office_address": "札幌市〇〇区〇〇 1-2-3",
                   "office_tel": "011-000-0000", "bank_info": "〇〇銀行 △△支店 普通 1234567", "invoice_due_day": "27",
                   "full_time_hours": "160"})
post("/m/homes/new", {"name": "ひだまり第1ホーム", "home_type": "介護サービス包括型", "capacity": "5"})
post("/m/homes/new", {"name": "ひだまり第2ホーム", "home_type": "介護サービス包括型", "capacity": "4"})
residents = [
    ("青木 春子", "あおきはるこ", 1, "101", "区分4", "知的障害", d(40), "9300"),
    ("石田 健", "いしだけん", 1, "102", "区分3", "精神障害", d(200), "0"),
    ("上野 さくら", "うえのさくら", 1, "103", "区分5", "知的障害", d(300), "9300"),
    ("江口 大輔", "えぐちだいすけ", 2, "201", "区分2", "発達障害", d(150), "9300"),
    ("小川 みどり", "おがわみどり", 2, "202", "区分4", "重複", d(-3), "37200"),
]
for name, kana, home, room, lvl, dis, cert_end, cap in residents:
    post("/m/residents/new", {"name": name, "kana": kana, "status": "入居中", "home_id": str(home), "room": room,
                              "support_level": lvl, "disability_type": dis, "cert_end": cert_end, "move_in": "2025-04-01",
                              "rent": "38000", "rent_subsidy": "10000", "utility": "12000", "daily_goods": "3000",
                              "food_type": "日額（食べた日数で計算）", "food_amount": "900", "burden_cap": cap,
                              "pay_method": "口座振替", "emergency_name": "家族（サンプル）", "day_activity": "就労継続支援B型〇〇"})
for i, (rid, end) in enumerate([(1, d(20)), (2, d(120)), (3, d(90)), (4, d(160))]):
    post("/m/support_plans/new", {"resident_id": str(rid), "status": "同意済", "created_on": d(-150), "period_start": d(-150),
                                  "period_end": end, "sabikan": "佐藤", "long_goal": "地域で安心して暮らし続ける",
                                  "short_goal": "生活リズムを整え、日中活動に毎日通う", "next_monitoring": d(10 + i * 30)})
staff = [("佐藤 一郎", "さとういちろう", "サービス管理責任者", "常勤", "4", "2019-04-01", "介護福祉士\n社会福祉士"),
         ("鈴木 由美", "すずきゆみ", "世話人", "常勤", "3", "2020-06-01", "介護福祉士"),
         ("高橋 誠", "たかはしまこと", "生活支援員", "常勤", "2", "2022-04-01", "初任者研修"),
         ("田中 恵", "たなかめぐみ", "世話人", "パート", "1", "2025-10-01", ""),
         ("中村 拓也", "なかむらたくや", "夜間支援従事者", "パート", "1", "2024-01-15", "")]
for name, kana, job, emp, grade, hire, q in staff:
    post("/m/staff/new", {"name": name, "kana": kana, "status": "在籍", "job": job, "employment": emp, "grade_id": grade,
                          "hire_date": hire, "qualifications": q, "home_id": "1"})
for sid, title, hrs in [(2, "虐待防止・権利擁護研修", "2"), (3, "強度行動障害支援者養成研修（基礎）", "12"), (4, "新任職員研修", "3"),
                        (2, "感染症・BCP研修", "1.5")]:
    post("/m/trainings/new", {"date": d(-30), "staff_id": str(sid), "kind": "外部研修", "title": title, "hours": hrs})
fy = today.year if today.month >= 4 else today.year - 1
for m, t in [(5, "虐待防止・権利擁護研修"), (7, "感染症・BCP研修"), (10, "身体拘束適正化研修"), (1, "救命講習")]:
    post("/m/training_plans/new", {"fiscal_year": str(fy), "month": str(m), "title": t, "target": "全職員",
                                   "status": "実施済" if m in (5, 7) else "予定"})
post("/m/evaluations/new", {"date": d(-60), "staff_id": "2", "period": "上期", "evaluator": "佐藤", "score": "A",
                            "recommend_grade_id": "3"})
# 日誌・支援記録
for back in (1, 0):
    post("/journal", {"date": d(-back), "home_id": "1", "slot": "終日", "day_staff": "鈴木", "night_staff": "中村",
                      "summary": "全員落ち着いて過ごす。夕食は手作りカレー。", "handover": "上野さん 明日通院（9:30）",
                      "r1_meal": "全量", "r1_medication": "済", "r1_mood": "良好", "r1_temperature": "36.4",
                      "r1_content": "作業所から帰宅後、入浴。", "r2_meal": "半量", "r2_medication": "済", "r2_mood": "不安定",
                      "r2_content": "夕方に不安の訴え。傾聴し落ち着く。", "r3_meal": "全量", "r3_mood": "普通",
                      "r3_content": "リビングでテレビを見て過ごす。"})
post("/m/incidents/new", {"date": d(-5), "kind": "ヒヤリハット", "resident_id": "2", "home_id": "1", "place": "浴室",
                          "content": "浴室の床で足をすべらせそうになった。", "prevention": "滑り止めマットを設置", "reporter": "鈴木"})
post("/m/incidents/new", {"date": d(-12), "kind": "ヒヤリハット", "resident_id": "3", "home_id": "1",
                          "content": "朝の服薬を飲み忘れそうになった。", "prevention": "服薬チェック表に記入欄を追加", "reporter": "高橋"})
for kind, back in [("虐待防止委員会", 120), ("身体拘束適正化委員会", 120), ("感染症対策委員会", 200), ("避難訓練", 220)]:
    post("/m/meetings/new", {"date": d(-back), "kind": kind, "title": kind, "attendees": "佐藤・鈴木・高橋", "recorder": "佐藤"})
# 加算（単位数は仮の値）
post("/m/addons/1/edit", {"name": "夜間支援等体制加算（Ⅰ）", "kind": "体制加算（事業所全体）", "units": "100", "unit_type": "日",
                          "active": "1", "notified_on": "2025-04-01",
                          "requirements": "夜勤を行う夜間支援従事者を配置\n勤務表・夜勤記録を整備", "notes": "デモ用の仮の単位数"})
post("/m/addons/11/edit", {"name": "日中支援加算", "kind": "個別加算（利用者ごと）", "units": "200", "unit_type": "日", "active": "1",
                           "requirements": "日中活動先に通えない日に住居内で日中支援を実施\n支援記録に実施内容を記録",
                           "notes": "デモ用の仮の単位数"})
post("/m/resident_addons/new", {"resident_id": "2", "addon_id": "11"})
for lvl, u in [("区分2", 300), ("区分3", 400), ("区分4", 500), ("区分5", 600)]:
    post("/m/basic_units/new", {"home_type": "介護サービス包括型", "support_level": lvl, "label": "（例）デモ用の仮の値",
                                "units": str(u), "active": "1"})
post("/m/shogu_plans/new", {"fiscal_year": str(fy), "category": "Ⅱ", "rate": "14.0", "revenue": "48000000"})
for sid, method, mon, lump in [(1, "手当（毎月）", "15000", "100000"), (2, "手当（毎月）", "15000", "80000"),
                               (3, "基本給", "18000", "60000"), (4, "手当（毎月）", "10000", "40000"), (5, "手当（毎月）", "10000", "40000")]:
    post("/m/shogu_allocations/new", {"plan_id": "1", "staff_id": str(sid), "method": method, "monthly": mon, "annual_lump": lump})
# 実績（今月）
import calendar  # noqa: E402

last = calendar.monthrange(today.year, today.month)[1]
for home, rids in [(1, [1, 2, 3]), (2, [4, 5])]:
    form = {"ym": ym, "home_id": str(home)}
    for rid in rids:
        for day in range(1, last + 1):
            form[f"a{rid}_{day}"] = "○"
    form.update({"a1_11": "外", "a1_12": "外", "a2_15": "日", "a2_16": "日", "a5_20": "入", "a5_21": "入", "a5_22": "入",
                 "a4_25": "帰", "a4_26": "帰"})
    post("/billing/attendance", form)
post("/billing/invoices", {"ym": ym})
for rid, kind, amt, purpose, receipt in [(1, "入金", "20000", "家族より", "1"), (1, "出金", "1280", "日用品（シャンプー）", "1"),
                                         (2, "入金", "15000", "本人より", "1"), (2, "出金", "800", "おやつ", ""),
                                         (3, "入金", "10000", "家族より", "1")]:
    post("/m/deposits/new", {"date": d(-7), "resident_id": str(rid), "kind": kind, "amount": amt, "purpose": purpose,
                             "receipt": receipt, "staff": "鈴木"})
for rid in (1, 2, 3):
    for t in ["利用契約書", "重要事項説明書", "個人情報使用同意書"]:
        post("/m/resident_documents/new", {"resident_id": str(rid), "doc_type": t, "signed_on": "2025-04-01", "place": "事務所 書庫"})
post("/m/resident_documents/new", {"resident_id": "4", "doc_type": "利用契約書", "signed_on": "2025-04-01", "expires_on": d(20)})
# 入院・帰省と連絡記録
post("/m/absences/new", {"resident_id": "5", "kind": "入院", "start_date": d(-12), "end_plan": d(5), "place": "△△病院 3階病棟",
                         "place_tel": "011-000-1111", "contact_person": "主治医 〇〇先生／担当看護師 △△さん",
                         "reason": "肺炎の治療のため", "belongings": "着替え3日分・保険証・お薬手帳", "auto_attendance": "1"})
post("/absences/1", {"date": d(-12), "time": "10:30", "counterpart": "医療機関・病院", "counterpart_name": "△△病院 外来",
                     "method": "電話", "direction": "こちらから", "content": "発熱のため受診。肺炎の診断で入院となる。", "staff": "鈴木",
                     "next_action": "家族へ連絡済。着替えを明日届ける。"})
post("/absences/1", {"date": d(-9), "time": "14:00", "counterpart": "医療機関・病院", "counterpart_name": "3階病棟 看護師",
                     "method": "面会・訪問", "direction": "こちらから", "content": "面会。熱は下がり、食事は半分ほど食べられている。",
                     "staff": "佐藤", "next_action": "退院の見込みは来週。サビ管が退院前カンファレンスに参加予定。"})
post("/m/absences/new", {"resident_id": "4", "kind": "帰宅（帰省）", "start_date": d(-2), "end_plan": d(1), "place": "実家（母）",
                         "contact_person": "母 江口〇〇（090-0000-0000）", "reason": "週末の帰省", "auto_attendance": "1",
                         "belongings": "お薬3日分"})
post("/absences/2", {"date": d(-1), "time": "19:00", "counterpart": "家族", "counterpart_name": "母", "method": "電話",
                     "direction": "先方から", "content": "家で落ち着いて過ごしている。お薬も飲めているとのこと。", "staff": "中村"})
post("/m/contact_logs/new", {"date": d(-3), "time": "11:00", "resident_id": "1", "counterpart": "相談支援専門員",
                             "counterpart_name": "〇〇相談支援事業所 田中さん", "method": "電話", "direction": "先方から",
                             "content": "モニタリングの日程調整。来月10日に来所予定。", "staff": "佐藤"})
shift = {"ym": ym}
pattern = {1: "日日日日日休休", 2: "日日休日日日休", 3: "遅遅日休日日休", 4: "早早休早早休休", 5: "夜明休夜明休休"}
for sid, p in pattern.items():
    for day in range(1, last + 1):
        shift[f"s{sid}_{day}"] = p[(day - 1) % 7]
post("/shift/", shift)

# ログインする人（職員のアカウント）
post("/users", {"action": "add", "username": "suzuki", "display_name": "鈴木 由美", "password": "temppass1", "role": "staff"})
post("/users", {"action": "add", "username": "takahashi", "display_name": "高橋 誠", "password": "temppass1", "role": "staff"})

# サンプル登録で溜まった「登録しました」の表示を消しておく
c.get("/m/homes/")

# ---------------- 画面を集める ----------------
SKIP = ("/logout", "/backup", "/setup", "/login")
CAP_PER_PATH = 2         # 同じ画面の絞り込み違いは2つまで
CAP_DETAIL = {"view": 2, "edit": 1}
MAX_PAGES = 235

PRIORITY = ["/", "/do/daily", "/do/residents", "/do/staff", "/do/money", "/do/billing", "/do/settings", "/journal",
            "/reports", "/billing/attendance", "/billing/benefit", "/billing/invoices", "/billing/invoice/1/print",
            "/billing/invoice/1/print?kind=receipt", "/billing/deposits", "/billing/documents", "/shift/", "/career",
            "/shogu/", "/shogu/1", "/addons/check", "/settings", "/users", "/audit", "/password",
            "/m/residents/1", "/m/residents/1/edit", "/m/residents/new", "/m/incidents/new", "/m/support_plans/1",
            "/absences/", "/absences/1", "/absences/2", "/m/absences/new", "/m/contact_logs/", "/m/contact_logs/new",
            "/m/residents/5", "/m/residents/4"]
pages, queue, seen = {}, deque(PRIORITY), set(PRIORITY)
per_path, per_kind = Counter(), Counter()


def kind_of(path):
    m = re.match(r"^/m/([a-z_]+)/(\d+)(/edit)?$", path)
    if m:
        return (m.group(1), "edit" if m.group(3) else "view")
    return None


def allowed(url):
    sp = urlsplit(url)
    path = sp.path
    if path.startswith(SKIP) or path.endswith(".xlsx") or path.startswith("/static"):
        return False
    if path.endswith("/delete"):
        return False
    k = kind_of(path)
    if k and per_kind[k] >= CAP_DETAIL[k[1]]:
        return False
    if sp.query and per_path[path] >= CAP_PER_PATH:
        return False
    return True


HREF = re.compile(r'(href|action|src)="(/[^"]*)"')
while queue and len(pages) < MAX_PAGES:
    url = queue.popleft()
    r = c.get(url)
    if r.status_code != 200 or not r.mimetype.startswith("text/html"):
        continue
    sp = urlsplit(url)
    per_path[sp.path] += 1
    k = kind_of(sp.path)
    if k:
        per_kind[k] += 1
    text = r.get_data(as_text=True)
    pages[url] = text
    for _, link in HREF.findall(text):
        link = htmlmod.unescape(link)
        if link.startswith("//") or link in seen:
            continue
        if allowed(link):
            seen.add(link)
            queue.append(link)

names = {"/": "p_home.html"}
for i, url in enumerate(u for u in pages if u != "/"):
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", url).strip("_")[:50]
    names[url] = f"p{i:03d}_{slug}.html"

INJECT = """
<div class="demo-bar" title="表示しているのはすべて架空のサンプルデータで、入力しても保存されません">🧪 デモ版（保存されません）<span class="demo-msg" hidden></span></div>
<style>
.demo-bar{position:fixed;left:12px;bottom:12px;z-index:50;background:rgba(43,43,43,.88);color:#fff;font-size:12px;padding:4px 12px;border-radius:99px}
.demo-msg{display:inline-block;margin-left:10px;background:#ff8a3d;color:#fff;border-radius:8px;padding:1px 8px}
</style>
<script>
(function(){
  var msg=document.querySelector('.demo-msg'),t;
  function say(s){msg.textContent=s;msg.hidden=false;clearTimeout(t);t=setTimeout(function(){msg.hidden=true;},3500);}
  document.querySelectorAll('form').forEach(function(f){f.addEventListener('submit',function(e){e.preventDefault();e.stopImmediatePropagation();
    say(f.method.toLowerCase()==='post'?'デモ版のため保存はされません':'デモ版のため絞り込み・月の切り替えはできません');},true);});
  document.querySelectorAll('[data-demo-off]').forEach(function(a){a.addEventListener('click',function(e){e.preventDefault();
    say(a.getAttribute('data-demo-off'));});});
  document.querySelectorAll('select[onchange],input[onchange]').forEach(function(el){el.removeAttribute('onchange');});
})();
</script>
"""


def rewrite(text):
    def sub(m):
        attr, link = m.group(1), htmlmod.unescape(m.group(2))
        if link.startswith("/static/"):
            return f'{attr}="{link.rsplit("/", 1)[1]}"'
        if link in names:
            return f'{attr}="{names[link]}"'
        if attr == "action":
            return f'{attr}="#"'
        why = "デモ版ではExcelのダウンロードはできません" if ".xlsx" in link else "デモ版ではこの画面は省略しています"
        return f'{attr}="#" data-demo-off="{why}"'

    text = HREF.sub(sub, text)
    text = text.replace(" data-guard", "")
    return text.replace("</body>", INJECT + "</body>")


shutil.rmtree(out, ignore_errors=True)
os.makedirs(out)
for url, text in pages.items():
    with open(os.path.join(out, names[url]), "w", encoding="utf-8") as f:
        f.write(rewrite(text))
shutil.copy(os.path.join(repo, "ghms", "static", "style.css"), os.path.join(out, "style.css"))
print(len(pages), "pages")
