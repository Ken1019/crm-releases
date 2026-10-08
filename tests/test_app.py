import re
from datetime import date

import pytest

from ghms import create_app
from ghms.entities import ENTITIES


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("GHMS_DATA_DIR", str(tmp_path))
    app = create_app({"TESTING": True})
    return app


def csrf(client, path="/"):
    html = client.get(path, follow_redirects=True).get_data(as_text=True)
    m = re.search(r'name="_csrf" value="([0-9a-f]+)"', html)
    return m.group(1)


@pytest.fixture()
def client(app):
    c = app.test_client()
    from ghms.customize import FEATURES
    r = c.post("/setup", data={"office_name": "テストホーム", "username": "admin", "password": "password123",
                               "home_types": "介護サービス包括型", "features": [k for k, *_ in FEATURES]})
    assert r.status_code == 302
    return c


def staff_client(admin, app, username="worker"):
    """管理者が職員アカウントを作り、職員が初回ログインでパスワードを変えた状態のクライアント"""
    post(admin, "/users", {"action": "add", "username": username, "password": "temppass1", "role": "staff"})
    c = app.test_client()
    c.post("/login", data={"username": username, "password": "temppass1"})
    post(c, "/password", {"current": "temppass1", "password": "mypass2026", "password2": "mypass2026"})
    return c


def post(client, path, data):
    data = dict(data, _csrf=csrf(client))
    return client.post(path, data=data)


def test_setup_redirect(app):
    c = app.test_client()
    assert c.get("/").headers["Location"].endswith("/setup")


def test_all_pages_render(client):
    for key in ENTITIES:
        assert client.get(f"/m/{key}/").status_code == 200, key
        assert client.get(f"/m/{key}/new").status_code == 200, key
        r = client.get(f"/m/{key}/export.xlsx")
        assert r.status_code == 200 and r.data[:2] == b"PK", key
    for path in ["/", "/journal", "/reports", "/addons/check", "/shogu/", "/career", "/settings", "/users", "/audit",
                 "/career/export.xlsx", "/journal/export.xlsx", "/backup"]:
        assert client.get(path).status_code == 200, path


def test_csrf_required(client):
    assert client.post("/m/homes/new", data={"name": "x"}).status_code == 400


def test_resident_journal_and_alerts(client):
    assert post(client, "/m/homes/new", {"name": "ひまわり"}).status_code == 302
    r = post(client, "/m/residents/new", {"name": "山田太郎", "home_id": "1", "status": "入居中", "cert_end": "2000-01-01"})
    assert r.status_code == 302
    dash = client.get("/").get_data(as_text=True)
    assert "受給者証" in dash and "個別支援計画が未作成" in dash

    post(client, "/m/residents/new", {"name": "鈴木花子", "home_id": "1", "status": "入居中"})
    r = post(client, "/journal", {"date": "2026-10-01", "home_id": "1", "slot": "終日", "summary": "穏やか",
                                  "r1_meal": "全量", "r1_content": "散歩に行った", "r1_staff": "職員A", "r2_staff": "職員A"})
    # 記録者だけが入っている入居者の記録は作成しない
    assert "1件" in client.get("/m/support_records/").get_data(as_text=True)
    assert r.status_code == 302
    page = client.get("/journal?date=2026-10-01&home_id=1&slot=終日").get_data(as_text=True)
    assert "散歩に行った" in page and "穏やか" in page
    # 同じ日・時間帯は上書き（重複作成しない）
    post(client, "/journal", {"date": "2026-10-01", "home_id": "1", "slot": "終日", "r1_content": "更新"})
    rows = client.get("/m/support_records/?resident_id=1").get_data(as_text=True)
    assert "更新" in rows and "散歩に行った" not in rows
    assert client.get("/journal/export.xlsx?ym=2026-10&home_id=1").status_code == 200
    assert client.get("/residents/1/records.xlsx?ym=2026-10").status_code == 200

    # 参照されている入居者は削除できない
    post(client, "/m/residents/1/delete", {})
    assert "山田太郎" in client.get("/m/residents/").get_data(as_text=True)


def test_required_and_number_validation(client):
    r = post(client, "/m/homes/new", {"name": "", "capacity": "abc"})
    html = r.get_data(as_text=True)
    assert "必須" in html and "数値" in html


def test_shogu_calculation(client):
    post(client, "/m/staff/new", {"name": "佐藤花子", "status": "在籍"})
    post(client, "/m/shogu_plans/new", {"fiscal_year": "2026", "category": "Ⅱ", "rate": "10", "revenue": "10000000"})
    post(client, "/m/shogu_allocations/new", {"plan_id": "1", "staff_id": "1", "method": "手当（毎月）", "monthly": "50000",
                                              "annual_lump": "200000"})
    html = client.get("/shogu/1").get_data(as_text=True)
    assert "1,000,000" in html  # 加算額見込
    assert "800,000" in html  # 50,000×12 + 200,000
    assert "-200,000" in html  # 不足
    assert "キャリアパス要件Ⅴ" not in html  # 区分Ⅱは対象外
    post(client, "/shogu/1", {"req": ["1", "2"]})
    assert client.get("/shogu/1/export.xlsx").status_code == 200


def test_addon_check(client):
    post(client, "/m/addons/1/edit", {"name": "夜間支援等体制加算（Ⅰ）", "kind": "体制加算（事業所全体）", "units": "100",
                                      "unit_type": "日", "active": "1", "requirements": "夜勤者配置\n記録整備"})
    html = client.get("/addons/check?ym=2026-10").get_data(as_text=True)
    assert "夜勤者配置" in html
    post(client, "/addons/check", {"ym": "2026-10", "a1_0": "1", "a1_1": "1"})
    assert "要件OK" in client.get("/addons/check?ym=2026-10").get_data(as_text=True)


def test_staff_role_cannot_see_admin_pages(client, app):
    c = staff_client(client, app)
    assert c.get("/").status_code == 200
    assert c.get("/m/residents/").status_code == 200
    for path in ["/m/staff/", "/m/shogu_plans/", "/shogu/", "/career", "/users", "/backup", "/billing/invoices",
                 "/m/invoices/", "/shift/"]:
        assert c.get(path).status_code == 403, path


def test_open_redirect_blocked(client):
    r = post(client, "/m/homes/new", {"name": "a", "_next": "//evil.example"})
    assert r.headers["Location"].startswith("/m/homes/")


def test_purpose_menu(client, app):
    for key in ["daily", "residents", "staff", "money", "settings"]:
        assert client.get(f"/do/{key}").status_code == 200, key
    home = client.get("/").get_data(as_text=True)
    assert "やりたいことを言葉でさがす" in home and "研修を受けた記録をつける" in home
    # 入居者の画面ではメニューの「入居者のこと」が選択状態になり、道しるべが出る
    page = client.get("/m/support_plans/").get_data(as_text=True)
    assert 'class="on"><span class="ic">👤' in page and "＞" in page

    c = staff_client(client, app)
    assert c.get("/do/staff").status_code == 404
    home = c.get("/").get_data(as_text=True)
    assert "職員のこと" not in home and "処遇改善の計画と配分を見る" not in home
    # 職員のメニューは「毎日の記録」「入居者のこと」「勤怠・給与」だけ（お金・書類・設定は出さない）
    assert "今月の加算の要件をチェックする" not in home and "請求・お金" not in home and "今日の日誌・記録を書く" in home
    assert c.get("/do/money").status_code == 404 and c.get("/do/work").status_code == 200


def _setup_billing(client):
    post(client, "/m/homes/new", {"name": "ひまわり", "home_type": "介護サービス包括型"})
    post(client, "/m/residents/new", {
        "name": "山田太郎", "home_id": "1", "status": "入居中", "support_level": "区分4", "move_in": "2026-09-01",
        "rent": "40000", "rent_subsidy": "10000", "utility": "10000", "daily_goods": "3000",
        "food_type": "日額（食べた日数で計算）", "food_amount": "800", "burden_cap": "9300", "pay_method": "振込"})
    # 単位数はテスト用の仮の値
    post(client, "/m/basic_units/new", {"home_type": "介護サービス包括型", "support_level": "区分4", "units": "500", "active": "1"})
    post(client, "/m/addons/new", {"name": "日中支援加算（テスト）", "kind": "個別加算（利用者ごと）", "units": "100",
                                   "unit_type": "日", "active": "1"})
    addon_id = len(__import__("ghms.seed", fromlist=["ADDONS"]).ADDONS) + 1
    post(client, "/m/resident_addons/new", {"resident_id": "1", "addon_id": str(addon_id)})
    post(client, "/m/shogu_plans/new", {"fiscal_year": "2026", "category": "Ⅰ", "rate": "10", "revenue": "1"})
    # 2026年10月：31日中 外泊3日・日中支援1日・残り在居
    form = {f"a1_{d}": "○" for d in range(1, 32)}
    form.update({"a1_5": "外", "a1_6": "外", "a1_7": "外", "a1_10": "日", "ym": "2026-10", "home_id": "1"})
    assert post(client, "/billing/attendance", form).status_code == 302


def test_benefit_and_invoice(client):
    _setup_billing(client)
    html = client.get("/billing/benefit?ym=2026-10").get_data(as_text=True)
    # 基本 500×28日 + 日中支援 100×1日 = 14,100、処遇改善10% = 1,410 → 15,510単位 × 10円
    assert "15,510" in html and "155,100" in html
    assert "9,300" in html  # 上限額で頭打ち
    assert client.get("/billing/benefit.xlsx?ym=2026-10").data[:2] == b"PK"

    post(client, "/billing/invoices", {"ym": "2026-10"})
    html = client.get("/billing/invoices?ym=2026-10").get_data(as_text=True)
    # 家賃40,000 − 助成10,000 + 食費800×28 + 光熱水費10,000 + 日用品3,000 + 負担9,300 = 74,700
    assert '<b class="rowtotal">74,700</b>' in html
    # 2回押しても重複しない
    post(client, "/billing/invoices", {"ym": "2026-10"})
    assert client.get("/m/invoices/").get_data(as_text=True).count("✏️ 直す") == 1
    printed = client.get("/billing/invoice/1/print").get_data(as_text=True)
    assert "請 求 書" in printed and "74,700" in printed and "2026-11-27" in printed
    assert "領 収 書" in client.get("/billing/invoice/1/print?kind=receipt").get_data(as_text=True)
    assert client.get("/billing/invoices.xlsx?ym=2026-10").status_code == 200

    # 手で直したら合計が再計算される
    post(client, "/m/invoices/1/edit", {"ym": "2026-10", "resident_id": "1", "status": "入金済", "rent": "40000",
                                        "rent_subsidy": "10000", "food": "0", "other_label": "行事費", "other_amount": "500"})
    assert "30,500" in client.get("/m/invoices/1").get_data(as_text=True)


def test_invoice_prorates_move_in(client):
    post(client, "/m/homes/new", {"name": "ひまわり"})
    post(client, "/m/residents/new", {"name": "途中入居", "home_id": "1", "move_in": "2026-10-17", "rent": "31000",
                                      "food_type": "月額", "food_amount": "31000"})
    post(client, "/billing/invoices", {"ym": "2026-10"})
    # 17日〜31日の15日分：31,000 × 15/31 = 15,000（家賃・食費とも）
    assert '<b class="rowtotal">30,000</b>' in client.get("/billing/invoices?ym=2026-10").get_data(as_text=True)


def test_deposits_documents_and_dashboard(client):
    post(client, "/m/homes/new", {"name": "ひまわり"})
    post(client, "/m/residents/new", {"name": "山田太郎", "home_id": "1", "status": "入居中"})
    post(client, "/m/deposits/new", {"date": "2026-10-01", "resident_id": "1", "kind": "入金", "amount": "10000"})
    post(client, "/m/deposits/new", {"date": "2026-10-02", "resident_id": "1", "kind": "出金", "amount": "1200", "purpose": "おやつ"})
    html = client.get("/billing/deposits").get_data(as_text=True)
    assert "8,800円" in html and "1件" in html  # レシートなし1件
    assert client.get("/billing/deposits/1.xlsx").status_code == 200

    html = client.get("/billing/documents").get_data(as_text=True)
    assert html.count("なし ＋") == 6
    post(client, "/m/resident_documents/new", {"resident_id": "1", "doc_type": "利用契約書", "signed_on": "2026-04-01",
                                               "expires_on": "2026-10-10"})
    assert client.get("/billing/documents").get_data(as_text=True).count("なし ＋") == 5
    assert "利用契約書の更新" in client.get("/").get_data(as_text=True)
    assert "避難訓練" in client.get("/").get_data(as_text=True)


def test_shift_roster(client):
    post(client, "/m/staff/new", {"name": "常勤さん", "status": "在籍", "job": "世話人", "employment": "常勤"})
    post(client, "/m/staff/new", {"name": "パートさん", "status": "在籍", "job": "世話人", "employment": "パート"})
    form = {"ym": "2026-10", "s1_1": "夜", "s1_2": "明"}
    form.update({f"s2_{d}": "日" for d in range(1, 11)})  # 日勤8h×10日=80h
    assert post(client, "/shift/", form).status_code == 302
    html = client.get("/shift/?ym=2026-10").get_data(as_text=True)
    assert "0.5" in html and "1.5" in html  # パート 80/160、世話人合計 1.5
    assert client.get("/shift/export.xlsx?ym=2026-10").data[:2] == b"PK"


def _codes(app, rid):
    import sqlite3
    con = sqlite3.connect(app.config["DATABASE"])
    rows = dict(con.execute("SELECT date, code FROM attendance WHERE resident_id=?", (rid,)).fetchall())
    status = con.execute("SELECT status FROM residents WHERE id=?", (rid,)).fetchone()[0]
    con.close()
    return rows, status


def test_absence_fills_attendance_and_status(client, app):
    from datetime import date, timedelta

    post(client, "/m/homes/new", {"name": "ひまわり"})
    post(client, "/m/residents/new", {"name": "山田太郎", "home_id": "1", "status": "入居中"})
    # 終わった入院：出発日・戻った日は在居のまま、その間だけ「入」
    post(client, "/m/absences/new", {"resident_id": "1", "kind": "入院", "start_date": "2026-09-20", "end_date": "2026-09-25",
                                     "auto_attendance": "1", "place": "〇〇病院"})
    codes, status = _codes(app, 1)
    assert [k for k, v in sorted(codes.items()) if v == "入"] == ["2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24"]
    assert status == "入居中"
    assert "戻った" in client.get("/m/absences/1").get_data(as_text=True)

    # いま入院中（戻った日なし）：今日までのびる・状態が入院中・10日連絡なしでお知らせ
    start = date.today() - timedelta(days=10)
    post(client, "/m/absences/new", {"resident_id": "1", "kind": "入院", "start_date": start.isoformat(),
                                     "auto_attendance": "1", "place": "△△病院"})
    codes, status = _codes(app, 1)
    assert codes[date.today().isoformat()] == "入" and start.isoformat() not in codes
    assert status == "入院中"
    home = client.get("/").get_data(as_text=True)
    assert "入院中の連絡が10日ありません" in home and "△△病院" in home
    assert "△△病院" in client.get("/journal?home_id=1").get_data(as_text=True)

    # 詳細画面から連絡を記録 → お知らせが消える
    r = post(client, "/absences/2", {"date": date.today().isoformat(), "counterpart": "医療機関・病院",
                                     "method": "面会・訪問", "content": "面会。食事は半分ほど。"})
    assert r.status_code == 302
    page = client.get("/absences/2").get_data(as_text=True)
    assert "面会。食事は半分ほど。" in page and "今月の面会・訪問 1回" in page
    assert "連絡が10日ありません" not in client.get("/").get_data(as_text=True)
    assert "面会。食事は半分ほど。" in client.get("/m/contact_logs/?resident_id=1").get_data(as_text=True)

    # 退院：戻った日を入れると状態が戻る
    post(client, "/m/absences/2/edit", {"resident_id": "1", "kind": "入院", "status": "不在中", "start_date": start.isoformat(),
                                        "end_date": date.today().isoformat(), "auto_attendance": "1"})
    codes, status = _codes(app, 1)
    assert status == "入居中" and date.today().isoformat() not in codes


def test_absence_delete_clears_attendance(client, app):
    post(client, "/m/homes/new", {"name": "ひまわり"})
    post(client, "/m/residents/new", {"name": "山田太郎", "home_id": "1", "status": "入居中"})
    post(client, "/m/absences/new", {"resident_id": "1", "kind": "帰宅（帰省）", "start_date": "2026-09-01",
                                     "end_date": "2026-09-05", "auto_attendance": "1"})
    assert set(_codes(app, 1)[0].values()) == {"帰"}
    post(client, "/m/absences/1/delete", {})
    assert _codes(app, 1)[0] == {}
    # 自動反映をオフにすると実績は変えない
    post(client, "/m/absences/new", {"resident_id": "1", "kind": "外泊", "start_date": "2026-09-10", "end_date": "2026-09-13"})
    assert _codes(app, 1)[0] == {}


def test_resident_summary(client):
    post(client, "/m/homes/new", {"name": "ひまわり"})
    post(client, "/m/residents/new", {"name": "山田太郎", "home_id": "1", "status": "入居中"})
    post(client, "/m/contact_logs/new", {"date": "2026-10-01", "resident_id": "1", "counterpart": "家族",
                                         "content": "週末の帰省について母と相談"})
    post(client, "/m/absences/new", {"resident_id": "1", "kind": "帰宅（帰省）", "start_date": "2026-10-03"})
    page = client.get("/m/residents/1").get_data(as_text=True)
    assert "最近のようす" in page and "週末の帰省について母と相談" in page and "いま<b>帰宅（帰省）</b>中です" in page


def test_hidden_attribute_wins_over_css(app):
    # .todo { display:grid } などに負けて hidden の要素が見えてしまわないこと
    css = open(app.static_folder + "/style.css", encoding="utf-8").read()
    assert "[hidden] { display:none !important; }" in css


# ---------------------------------------------------------------- ログイン・個人情報の保護
def test_new_staff_must_change_password_first(client, app):
    post(client, "/users", {"action": "add", "username": "newbie", "password": "temppass1", "role": "staff"})
    c = app.test_client()
    c.post("/login", data={"username": "newbie", "password": "temppass1"})
    r = c.get("/m/residents/")
    assert r.status_code == 302 and r.headers["Location"].endswith("/password")
    assert "最初に自分だけが知っているパスワードに変えてください" in c.get("/password").get_data(as_text=True)
    # 決まりに合わないパスワード・確認の不一致は受け付けない
    post(c, "/password", {"current": "temppass1", "password": "abcdefgh", "password2": "abcdefgh"})
    post(c, "/password", {"current": "temppass1", "password": "mypass2026", "password2": "other2026"})
    assert c.get("/m/residents/").status_code == 302
    post(c, "/password", {"current": "temppass1", "password": "mypass2026", "password2": "mypass2026"})
    assert c.get("/m/residents/").status_code == 200


def test_password_rules(client):
    r = post(client, "/users", {"action": "add", "username": "u1", "password": "onlyletters", "role": "staff"})
    assert "英字と数字の両方" in client.get(r.headers["Location"]).get_data(as_text=True)
    post(client, "/users", {"action": "add", "username": "abc12345", "password": "ABC12345", "role": "staff"})
    users = client.get("/users").get_data(as_text=True)
    assert "<b>u1</b>" not in users and "<b>abc12345</b>" not in users


def test_lockout_after_failures_and_unlock(client, app):
    staff_client(client, app, "locky")
    c = app.test_client()
    for _ in range(5):
        c.post("/login", data={"username": "locky", "password": "wrong1234"})
    r = c.post("/login", data={"username": "locky", "password": "mypass2026"})
    assert "ログインできない状態です" in r.get_data(as_text=True)
    assert "ロック中" in client.get("/users").get_data(as_text=True)
    post(client, "/users", {"action": "unlock", "id": "2"})
    r = c.post("/login", data={"username": "locky", "password": "mypass2026"})
    assert r.status_code == 302


def test_disabled_user_is_logged_out(client, app):
    c = staff_client(client, app)
    assert c.get("/").status_code == 200
    post(client, "/users", {"action": "disable", "id": "2"})
    assert c.get("/").status_code == 302  # 使っている途中でも追い出される
    r = c.post("/login", data={"username": "worker", "password": "mypass2026"})
    assert "違うか" in r.get_data(as_text=True)


def test_last_admin_is_protected(client):
    post(client, "/users", {"action": "role", "id": "1", "role": "staff"})
    post(client, "/users", {"action": "disable", "id": "1"})
    assert client.get("/users").status_code == 200  # まだ管理者のまま


def test_idle_timeout(client):
    with client.session_transaction() as s:
        s["seen"] = s["seen"] - 31 * 60
    r = client.get("/m/residents/")
    assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_staff_export_needs_permission(client, app):
    c = staff_client(client, app)
    assert c.get("/m/residents/export.xlsx").status_code == 403
    assert "Excelで出す" not in c.get("/m/residents/").get_data(as_text=True)
    post(client, "/settings", {"staff_can_export": "1", "session_timeout_min": "30"})
    assert c.get("/m/residents/export.xlsx").status_code == 200


def test_audit_records_views_exports_and_failures(client, app):
    post(client, "/m/residents/new", {"name": "山田太郎"})
    client.get("/m/residents/1")
    client.get("/m/residents/export.xlsx")
    app.test_client().post("/login", data={"username": "admin", "password": "wrong1234"})
    html = client.get("/audit").get_data(as_text=True)
    assert "閲覧" in html and "Excel出力" in html and "ログイン失敗" in html and "/m/residents/export.xlsx" in html
    r = client.get("/m/residents/1")
    assert r.headers["Cache-Control"] == "no-store" and r.headers["X-Frame-Options"] == "DENY"


# ---------------------------------------------------------------- PINでの交代ログイン
def _register_device(admin):
    r = post(admin, "/devices", {"action": "register", "name": "第1ホーム リビングPC"})
    assert r.status_code == 302


def _logout(c):
    post(c, "/logout", {})


def test_pin_rules_and_setting(client, app):
    c = staff_client(client, app)
    for bad in ["1111", "1234", "9876", "1212", "12a4", "123"]:
        post(c, "/my-pin", {"current": "mypass2026", "pin": bad, "pin2": bad})
        assert "まだ設定していません" in c.get("/password").get_data(as_text=True), bad
    post(c, "/my-pin", {"current": "wrong", "pin": "4826", "pin2": "4826"})
    assert "まだ設定していません" in c.get("/password").get_data(as_text=True)
    post(c, "/my-pin", {"current": "mypass2026", "pin": "4826", "pin2": "4826"})
    page = c.get("/password").get_data(as_text=True)
    assert "設定済み" in page
    # PINの欄は本文に表示されている（<title> の中ではない）
    title = page.split("<title>")[1].split("</title>")[0]
    assert "PIN" not in title and 'action="/my-pin"' in page.split("<main>")[1]


def test_pin_login_only_on_registered_device(client, app):
    staff = staff_client(client, app)
    post(staff, "/my-pin", {"current": "mypass2026", "pin": "4826", "pin2": "4826"})
    # 登録していない端末ではPINの画面に入れない
    other = app.test_client()
    r = other.post("/pin/2", data={"pin": "4826"})
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    assert other.get("/").status_code == 302

    # 管理者がこの端末（client）を登録 → ログアウトすると名前が並ぶ
    _register_device(client)
    _logout(client)
    page = client.get("/login").get_data(as_text=True)
    assert "自分の名前を押して" in page and "worker" in page
    r = client.post("/pin/2", data={"pin": "4826"})
    assert r.status_code == 302
    home = client.get("/").get_data(as_text=True)
    assert "worker さん" in home and "交代する" in home


def test_pin_lockout(client, app):
    staff = staff_client(client, app)
    post(staff, "/my-pin", {"current": "mypass2026", "pin": "4826", "pin2": "4826"})
    _register_device(client)
    _logout(client)
    for _ in range(5):
        client.post("/pin/2", data={"pin": "0000"})
    r = client.post("/pin/2", data={"pin": "4826"})
    assert "しばらくログインできません" in r.get_data(as_text=True)


def test_admin_via_pin_must_confirm_password(client, app):
    post(client, "/my-pin", {"current": "password123", "pin": "4826", "pin2": "4826"})
    _register_device(client)
    _logout(client)
    client.post("/pin/1", data={"pin": "4826"})
    assert client.get("/").status_code == 200
    r = client.get("/users")
    assert r.status_code == 302 and "/reauth" in r.headers["Location"]
    assert client.get("/m/staff/").status_code == 302  # 給与など管理者だけの一覧も
    post(client, "/reauth?next=/users", {"password": "wrong"})
    assert client.get("/users").status_code == 302
    r = post(client, "/reauth?next=/users", {"password": "password123"})
    assert r.headers["Location"].endswith("/users")
    assert client.get("/users").status_code == 200


def test_removed_device_and_short_timeout(client, app):
    staff = staff_client(client, app)
    post(staff, "/my-pin", {"current": "mypass2026", "pin": "4826", "pin2": "4826"})
    _register_device(client)
    post(client, "/devices", {"action": "remove", "id": "1"})
    _logout(client)
    assert "自分の名前を押して" not in client.get("/login").get_data(as_text=True)
    assert client.post("/pin/2", data={"pin": "4826"}).headers["Location"].endswith("/login")

    # PINで入ったときは15分で自動ログアウト（パスワードは30分）
    dev = app.test_client()
    dev.post("/login", data={"username": "admin", "password": "password123"})
    _register_device(dev)
    _logout(dev)
    dev.post("/pin/2", data={"pin": "4826"})
    with dev.session_transaction() as s:
        s["seen"] = s["seen"] - 16 * 60
    assert dev.get("/").status_code == 302


# ---------------------------------------------------------------- ネット経由の更新
import hashlib  # noqa: E402
import io  # noqa: E402
import json as _json  # noqa: E402


def _fake_opener(files):
    """URL → 中身（bytes）の辞書で、urlopen の代わりをする"""
    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        if url not in files:
            raise OSError("not found")
        return Resp(files[url])
    return opener


def test_version_compare():
    from ghms.updater import is_newer, parse_version
    assert parse_version("v1.2.10") == (1, 2, 10)
    assert is_newer("1.0.1", "1.0.0") and is_newer("1.10.0", "1.9.9")
    assert not is_newer("1.0.0", "1.0.0") and not is_newer("0.9.9", "1.0.0")


def test_update_check_and_download(app, tmp_path):
    from ghms import VERSION, updater
    exe = b"MZ fake installer"
    good = {"version": "99.0.0", "installer": "https://example.com/GHMS-Setup-99.0.0.exe",
            "sha256": hashlib.sha256(exe).hexdigest(), "notes": "新機能"}
    files = {updater.DEFAULT_URL: _json.dumps(good).encode(), good["installer"]: exe}
    with app.app_context():
        m, err = updater.check(_fake_opener(files))
        assert err is None and updater.cached_latest()["version"] == "99.0.0"
        path = updater.download_installer(m, str(tmp_path), _fake_opener(files))
        assert open(path, "rb").read() == exe
        # 中身が違えば（改ざん・破損）止める
        bad = dict(good, sha256="0" * 64)
        try:
            updater.download_installer(bad, str(tmp_path / "x"), _fake_opener(files))
            assert False, "should fail"
        except ValueError as e:
            assert "正しくありません" in str(e)
        # https 以外は使わない
        try:
            updater.download_installer(dict(good, installer="http://example.com/a.exe"), str(tmp_path), _fake_opener(files))
            assert False
        except ValueError:
            pass
        # ネットにつながらなくても落ちない
        m, err = updater.check(_fake_opener({}))
        assert m is None and "取得できませんでした" in err
        # 同じ版なら「新しい版」は出さない
        updater.check(_fake_opener({updater.DEFAULT_URL: _json.dumps(dict(good, version=VERSION)).encode()}))
        assert updater.cached_latest() is None


def test_update_page_and_banner(client, app):
    from ghms import updater
    with app.app_context():
        from ghms.db import get_db
        get_db().execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('update_latest', ?)",
                         (_json.dumps({"version": "99.0.0", "notes": "新しい画面を追加"}),))
        get_db().commit()
    assert "新しい版 99.0.0 があります" in client.get("/").get_data(as_text=True)
    page = client.get("/update").get_data(as_text=True)
    assert "新しい画面を追加" in page and "インストーラーで入れたものではない" in page
    # 開発版（インストーラーでない）では更新を実行しない
    post(client, "/update", {"action": "install"})
    assert updater.VERSION in client.get("/update").get_data(as_text=True)


def test_update_page_is_admin_only(client, app):
    c = staff_client(client, app)
    assert c.get("/update").status_code == 403
    assert "新しい版" not in c.get("/").get_data(as_text=True)


def test_shutdown_needs_token(client, app, tmp_path, monkeypatch):
    from ghms import runtime
    monkeypatch.setattr(runtime, "token_path", lambda: str(tmp_path / "run.token"))
    (tmp_path / "run.token").write_text("secret-token")
    c = app.test_client()
    assert c.post("/__shutdown").status_code == 403
    assert c.post("/__shutdown", headers={"X-GHMS-Token": "wrong"}).status_code == 403
    assert c.post("/__shutdown", headers={"X-GHMS-Token": "secret-token"},
                  environ_base={"REMOTE_ADDR": "192.168.1.20"}).status_code == 403
    assert c.get("/__ping").get_json()["ok"] is True


# ---------------------------------------------------------------- 事業所ごとのカスタマイズ
def test_features_can_be_turned_off(client, app):
    post(client, "/settings/features", {"on": ["absences", "incidents", "meetings", "billing"]})
    r = client.get("/shift/")
    assert r.status_code == 302  # 使わない機能は開けない
    assert "使わない」設定" in client.get(r.headers["Location"]).get_data(as_text=True)
    staff_hub = client.get("/do/staff").get_data(as_text=True)
    assert "勤務表を作る" not in staff_hub and "キャリアパスの全体を見る" not in staff_hub
    home = client.get("/").get_data(as_text=True)
    assert "処遇改善" not in home and "最近のヒヤリハット" in home
    post(client, "/settings/features", {"on": ["shift"]})
    home = client.get("/").get_data(as_text=True)
    assert "最近のヒヤリハット" not in home and "委員会の最終実施日" not in home
    assert client.get("/shift/").status_code == 200


def test_choices_add_hide_and_track(client):
    post(client, "/settings/choices", {"field": "meetings.kind", "new": "苦情解決委員会", "new_track": "90"})
    form = client.get("/m/meetings/new").get_data(as_text=True)
    assert "苦情解決委員会" in form
    assert "苦情解決委員会" in client.get("/").get_data(as_text=True)  # ホームに最終実施日
    post(client, "/m/meetings/new", {"date": "2026-10-01", "kind": "避難訓練", "title": "夜間想定"})
    # 避難訓練を使わない設定に（並び順などはそのまま送る）
    page = client.get("/settings/choices?field=meetings.kind").get_data(as_text=True)
    values = re.findall(r'name="active::([^"]+)"', page)
    data = {"field": "meetings.kind"}
    for v in values:
        if v != "避難訓練":
            data[f"active::{v}"] = "1"
    post(client, "/settings/choices", data)
    assert "避難訓練</option>" not in client.get("/m/meetings/new").get_data(as_text=True)
    assert ">避難訓練</option>" in client.get("/m/meetings/1/edit").get_data(as_text=True)  # 前の記録は残る
    # 選択肢にない値は受け付けない
    r = post(client, "/m/meetings/new", {"date": "2026-10-02", "kind": "ありえない会議"})
    assert "値が不正" in r.get_data(as_text=True)


def test_custom_fields(client):
    post(client, "/settings/fields", {"entity": "residents", "action": "add", "label": "好きな食べ物", "type": "text",
                                      "list_show": "1"})
    post(client, "/settings/fields", {"entity": "residents", "action": "add", "label": "血液型", "type": "select",
                                      "options": "A\nB\nO\nAB"})
    form = client.get("/m/residents/new").get_data(as_text=True)
    assert "独自の項目" in form and "好きな食べ物" in form and "<option>AB</option>" in form
    post(client, "/m/residents/new", {"name": "山田太郎", "cf_1": "カレー", "cf_2": "O"})
    assert "カレー" in client.get("/m/residents/").get_data(as_text=True)
    assert "O" in client.get("/m/residents/1").get_data(as_text=True)
    r = post(client, "/m/residents/new", {"name": "鈴木", "cf_2": "Z"})
    assert "値が不正" in r.get_data(as_text=True)
    assert client.get("/m/residents/export.xlsx").status_code == 200
    # 使わないにすると隠れる（データは残る）
    post(client, "/settings/fields", {"entity": "residents", "action": "update", "id": "1", "label": "好きな食べ物"})
    assert "好きな食べ物" not in client.get("/m/residents/new").get_data(as_text=True)


def test_first_setup_optimizes(tmp_path, monkeypatch):
    home = tmp_path / "ghms_home"
    home.mkdir()
    (home / "config.ini").write_text("[office]\nname = ひだまり\nno = 0110000000\n", encoding="utf-8")
    monkeypatch.setenv("GHMS_HOME", str(home))
    monkeypatch.setenv("GHMS_DATA_DIR", str(tmp_path / "data"))
    app = create_app({"TESTING": True})
    c = app.test_client()
    page = c.get("/setup").get_data(as_text=True)
    assert 'value="ひだまり"' in page and 'value="0110000000"' in page  # インストーラーで入れた値
    # 入力が足りないときは、入れた内容を残してやり直し
    r = c.post("/setup", data={"office_name": "ひだまり", "username": "admin", "password": "password123"})
    assert "類型を1つ以上" in r.get_data(as_text=True)
    c.post("/setup", data={
        "office_name": "ひだまり", "office_no": "0110000000", "unit_price": "10.30", "home_types": "介護サービス包括型",
        "home_name_1": "第1ホーム", "home_cap_1": "5", "home_name_2": "第2ホーム",
        "features": ["incidents", "meetings", "billing"], "username": "admin", "password": "password123"})
    home_page = c.get("/").get_data(as_text=True)
    assert "はじめての設定が終わりました" in home_page and "第1ホーム" in home_page
    assert c.get("/shift/").status_code == 302          # 使わない機能
    assert c.get("/billing/attendance").status_code == 200
    with app.app_context():
        from ghms.db import get_db, get_setting
        assert get_setting("unit_price") == "10.30"
        assert get_db().execute("SELECT COUNT(*) FROM homes").fetchone()[0] == 2
        assert get_db().execute("SELECT COUNT(*) FROM addons WHERE name='夜勤職員加配加算'").fetchone()[0] == 0


def test_config_ini_written_by_installer_in_cp932(tmp_path, monkeypatch):
    from ghms import runtime
    monkeypatch.setenv("GHMS_HOME", str(tmp_path))
    (tmp_path / "config.ini").write_bytes("[office]\r\nname=グループホーム ひだまり\r\nno=0110000000\r\n[server]\r\nlan=1\r\n".encode("cp932"))
    assert runtime.load_office() == {"name": "グループホーム ひだまり", "no": "0110000000"}
    assert runtime.load_config()["lan"] is True
    (tmp_path / "config.ini").write_text("[server]\nport = 8100\n", encoding="utf-8")
    assert runtime.load_config()["port"] == 8100


def test_monthly_fee_entry(client):
    _setup_billing(client)
    # 10月：表で日用品費とその他を今月の金額に直して保存 → 請求ができる
    page = client.get("/billing/invoices?ym=2026-10").get_data(as_text=True)
    assert 'name="r1_daily_goods" value="3000"' in page
    post(client, "/billing/invoices", {"ym": "2026-10", "action": "save", "r1_rent": "40000", "r1_rent_subsidy": "10000",
                                       "r1_food": "22400", "r1_utility": "10000", "r1_daily_goods": "2,480",
                                       "r1_user_burden": "9300", "r1_other_label": "行事費", "r1_other_amount": "1500"})
    page = client.get("/billing/invoices?ym=2026-10").get_data(as_text=True)
    # 40000 − 10000 + 22400 + 10000 + 2480 + 9300 + 1500 = 75,680
    assert '<b class="rowtotal">75,680</b>' in page and 'value="行事費"' in page
    # 11月：前の月の金額をコピーできるよう、10月の値が渡っている
    page = client.get("/billing/invoices?ym=2026-11").get_data(as_text=True)
    assert 'data-prev-daily_goods="2480"' in page and 'data-prev-other_label="行事費"' in page
    # 入金済みは表から変えられない
    post(client, "/m/invoices/1/edit", {"ym": "2026-10", "resident_id": "1", "status": "入金済", "rent": "40000",
                                        "rent_subsidy": "10000", "food": "22400", "utility": "10000", "daily_goods": "2480",
                                        "user_burden": "9300", "other_label": "行事費", "other_amount": "1500"})
    post(client, "/billing/invoices", {"ym": "2026-10", "action": "save", "r1_daily_goods": "99999"})
    assert '<b class="rowtotal">75,680</b>' in client.get("/billing/invoices?ym=2026-10").get_data(as_text=True)


# ---------------------------------------------------------------- 書類（献立表・実績記録票・指定更新）
def test_menus_week_copy_print(client, app):
    post(client, "/m/homes/new", {"name": "ひまわり"})
    post(client, "/m/residents/new", {"name": "山田太郎", "home_id": "1", "allergy": "えび"})
    post(client, "/docs/menus", {"home_id": "1", "week": "2026-10-05", "action": "save",
                                 "2026-10-05_朝食": "ごはん\n味噌汁", "2026-10-07_夕食": "カレーライス"})
    page = client.get("/docs/menus?home_id=1&week=2026-10-07").get_data(as_text=True)  # 週の途中の日でも同じ週
    assert "カレーライス" in page and "えび" in page
    post(client, "/docs/menus", {"home_id": "1", "week": "2026-10-12", "action": "copy_prev"})
    assert "カレーライス" in client.get("/docs/menus?home_id=1&week=2026-10-12").get_data(as_text=True)
    printed = client.get("/docs/menus?home_id=1&week=2026-10-12&print=1").get_data(as_text=True)
    assert "献 立 表" in printed and "10/14（水）" in printed
    assert client.get("/docs/menus.xlsx?home_id=1&ym=2026-10").data[:2] == b"PK"
    staff = staff_client(client, app)
    assert staff.get("/docs/menus").status_code == 200       # 職員も献立表は使える
    assert staff.get("/docs/renewal").status_code == 403     # 指定更新の書類は管理者だけ


def test_record_sheets(client):
    _setup_billing(client)
    page = client.get("/docs/record-sheets?ym=2026-10").get_data(as_text=True)
    assert "共同生活援助サービス提供実績記録票" in page and "山田太郎" in page
    assert client.get("/docs/record-sheets.xlsx?ym=2026-10").data[:2] == b"PK"


def test_record_sheet_columns(client, app):
    _setup_billing(client)
    from datetime import date

    from ghms.docs import record_sheet_data

    def data():
        with app.test_request_context():
            sheets, cols = record_sheet_data(date(2026, 10, 1), date(2026, 10, 31))
            return sheets[0], [c["label"] for c in cols]

    def totals():
        sh, labels = data()
        return {k: v for k, v in zip(labels, sh["totals"]) if v}

    # 様式18-1と同じ並び。夜間支援等体制加算は在居の日（外泊から戻った日も）に自動、日中支援加算は実績の「日」
    sh, labels = data()
    assert labels[:3] == ["住居外利用", "退居後支援", "夜間支援等体制加算"] and labels[-1] == "集中的支援加算"
    assert totals() == {"夜間支援等体制加算": 28, "日中支援加算": 1}
    # サービス提供の状況：出た日「住居→外泊」、中日「外泊」、戻った日「外泊戻り」、ふつうの日は空欄
    assert [x["label"] for x in sh["rows"][3:8]] == ["", "ひまわり→外泊", "外泊", "外泊", "外泊戻り"]
    assert sh["rows"][4]["marks"][2] == "" and sh["rows"][7]["marks"][2] == "1"
    page = client.get("/docs/record-sheets?ym=2026-10").get_data(as_text=True)
    assert "共同生活援助サービス提供実績記録票" in page and "令和 8 年 10 月分" in page and "ひまわり→外泊" in page
    assert "28回" in page and "移行支援住居" in page
    assert client.get("/docs/record-sheets.xlsx?ym=2026-10").data[:2] == b"PK"
    # 夜間支援を1日外す・帰宅時支援加算を手でつける
    form = {f"m1_{d}": "1" for d in range(1, 32) if d not in (1, 5, 6, 7)}
    post(client, "/docs/record-marks", dict(form, ym="2026-10", home_id="1", col="3"))
    post(client, "/docs/record-marks", {"m1_5": "1", "ym": "2026-10", "home_id": "1", "col": "5"})
    assert totals() == {"夜間支援等体制加算": 27, "帰宅時支援加算": 1, "日中支援加算": 1}
    # 実績を入院に直すと、夜間支援の印も自動で外れ、状況は「ひまわり→入院」「入院戻り」
    form = {f"a1_{d}": "○" for d in range(1, 32)}
    form.update({"a1_5": "外", "a1_6": "外", "a1_7": "外", "a1_10": "日", "a1_2": "入", "ym": "2026-10", "home_id": "1"})
    post(client, "/billing/attendance", form)
    sh, _ = data()
    assert [x["label"] for x in sh["rows"][1:3]] == ["ひまわり→入院", "入院戻り"]
    assert totals()["夜間支援等体制加算"] == 26
    # 列の名前・記号・単位を変える、使わない、増やす
    cols = {"active::3": "1", "label::3": "夜間支援", "mark::3": "2", "sort::3": "30", "auto::3": "stay",
            "active::6": "1", "label::6": "日中支援加算", "sort::6": "60",
            "active::1": "1", "label::1": "住居外利用", "unit::1": "日", "sort::1": "10",
            "new": "送迎", "new_auto": "", "new_unit": "回"}
    post(client, "/docs/record-columns", cols)
    sh, labels = data()
    assert labels == ["住居外利用", "夜間支援", "日中支援加算", "送迎"]
    assert sh["rows"][2]["marks"][1] == "2"
    page = client.get("/docs/record-sheets?ym=2026-10").get_data(as_text=True)
    assert "送迎" in page and "医療連携体制加算" not in page
    # 消す
    post(client, "/docs/record-columns", dict(cols, new="", **{"active::11": "1", "label::11": "送迎", "delete::11": "1"}))
    assert "送迎" not in data()[1]
    # 職員は印を入れられるが、項目の変更は管理者だけ
    staff = staff_client(client, app)
    assert staff.get("/docs/record-marks").status_code == 200
    assert staff.get("/docs/record-columns").status_code == 403


def test_renewal_documents(client):
    post(client, "/settings", {"office_name": "ひだまり", "corp_name": "社会福祉法人テスト会", "service_area": "札幌市中央区",
                               "complaint_manager": "管理者 佐藤", "session_timeout_min": "30", "staff_can_export": "0"})
    post(client, "/m/homes/new", {"name": "第1ホーム", "capacity": "5", "home_type": "介護サービス包括型"})
    post(client, "/m/staff/new", {"name": "佐藤 一郎", "status": "在籍", "job": "サービス管理責任者", "employment": "常勤",
                                  "career_history": "2015年4月〜 生活支援員", "certified_trainings": "基礎研修 2020年修了"})
    post(client, "/m/residents/new", {"name": "山田太郎", "home_id": "1", "support_level": "区分4", "rent": "38000"})
    page = client.get("/docs/renewal").get_data(as_text=True)
    assert "運営規程" in page and "0 / " in page
    post(client, "/docs/renewal", {"done_1": "1", "note_1": "総務が作成", "new": "消防の点検結果の写し", "due": "2027-03-31"})
    page = client.get("/docs/renewal").get_data(as_text=True)
    assert "1 / " in page and "消防の点検結果の写し" in page and "2027-03-31" in page
    rules = client.get("/docs/rules").get_data(as_text=True)
    assert "社会福祉法人テスト会" in rules and "札幌市中央区" in rules and "利用定員は5名" in rules and "38,000円" in rules
    resume = client.get("/docs/resumes").get_data(as_text=True)
    assert "佐藤 一郎" in resume and "基礎研修 2020年修了" in resume
    assert "区分4" in client.get("/docs/residents-status").get_data(as_text=True)
    assert "佐藤 一郎" in client.get("/docs/staff-list").get_data(as_text=True)
    assert client.get("/docs/committee").status_code == 200


# ---------------------------------------------------------------- 行事・レクリエーション
def test_activities_with_participants_and_birthdays(client):
    from datetime import date
    today = date.today()
    post(client, "/m/homes/new", {"name": "ひまわり"})
    post(client, "/m/residents/new", {"name": "山田太郎", "home_id": "1", "birthdate": f"1980-{today.month:02d}-15"})
    post(client, "/m/residents/new", {"name": "鈴木花子", "home_id": "1"})
    post(client, "/m/residents/new", {"name": "佐藤次郎", "home_id": "1"})
    # 今月の誕生日がホームに出て、押すと誕生日会の記録が入った状態で開く
    home = client.get("/").get_data(as_text=True)
    assert "今月の誕生日" in home and f"15日で{today.year - 1980}歳" in home
    form = client.get("/m/activities/new?kind=誕生日会&title=山田太郎さんの誕生日会&participants=1").get_data(as_text=True)
    assert 'value="1" checked' in form and "山田太郎さんの誕生日会" in form
    # 参加者を複数えらんで記録
    r = client.post("/m/activities/new", data={"_csrf": csrf(client), "date": "2026-10-04", "kind": "バーベキュー",
                                               "title": "秋のバーベキュー", "participants": ["1", "3"], "cost": "6000"})
    assert r.status_code == 302
    lst = client.get("/m/activities/").get_data(as_text=True)
    assert "山田太郎、佐藤次郎" in lst
    # 入居者で絞り込める・入居者の画面に出る
    assert "秋のバーベキュー" in client.get("/m/activities/?participants=3").get_data(as_text=True)
    assert "秋のバーベキュー" not in client.get("/m/activities/?participants=2").get_data(as_text=True)
    assert "秋のバーベキュー" in client.get("/m/residents/1").get_data(as_text=True)
    assert client.get("/m/activities/export.xlsx").status_code == 200
    # 参加した記録がある入居者は削除できない
    post(client, "/m/residents/3/delete", {})
    assert "佐藤次郎" in client.get("/m/residents/").get_data(as_text=True)


# ---------------------------------------------------------------- タイムカード・打刻・給与
def test_timecard_kiosk_and_payroll(client, app):
    post(client, "/m/staff/new", {"name": "佐藤 一郎", "status": "在籍", "pay_type": "時給", "hourly_wage": "1200",
                                  "night_allowance": "5000", "employment_insurance": "1",
                                  "commute_type": "1日あたり×出勤日数", "commute": "300"})
    staff = staff_client(client, app)
    post(client, "/users", {"action": "staff", "id": "2", "staff_id": "1"})
    post(staff, "/my-pin", {"current": "mypass2026", "pin": "4826", "pin2": "4826"})
    # 登録していない端末では打刻できない
    assert "登録したPC" in app.test_client().get("/work/kiosk").get_data(as_text=True)
    _register_device(client)
    _logout(client)                              # 事務所のPC（ログインしていない状態）
    assert "佐藤 一郎" in client.get("/work/kiosk").get_data(as_text=True)
    page = client.get("/work/kiosk?staff_id=1").get_data(as_text=True)
    k = re.search(r'name="_k" value="([0-9a-f]+)"', page).group(1)
    client.post("/work/kiosk", data={"_k": k, "staff_id": "1", "action": "in", "temp": "36.4", "pin": "0000"})
    with app.app_context():
        from ghms.db import get_db
        assert get_db().execute("SELECT COUNT(*) FROM timecards").fetchone()[0] == 0
    client.post("/work/kiosk", data={"_k": k, "staff_id": "1", "action": "in", "pin": "4826"})   # 体温なし
    client.post("/work/kiosk", data={"_k": k, "staff_id": "1", "action": "in", "temp": "37.8", "pin": "4826"})
    assert "勤務中" in client.get("/work/kiosk").get_data(as_text=True)
    assert client.get("/").status_code == 302    # 打刻してもログインしたままにならない
    with app.app_context():
        from ghms.db import get_db
        db = get_db()
        assert db.execute("SELECT COUNT(*) FROM timecards WHERE clock_out IS NULL").fetchone()[0] == 1
        assert db.execute("SELECT temp FROM health_checks").fetchone()[0] == 37.8
        db.execute("DELETE FROM timecards")
        db.commit()

    admin = app.test_client()
    admin.post("/login", data={"username": "admin", "password": "password123"})
    assert "佐藤 一郎" in admin.get("/work/health").get_data(as_text=True)
    # 管理者がタイムカードを入れる：夜勤 17:00〜翌9:00（休憩60分）と日勤 9:00〜18:30（休憩60分）
    post(admin, "/work/timecards", {"ym": "2026-10", "staff_id": "1", "in_new_1": "17:00", "out_new_1": "09:00", "br_new_1": "60",
                                    "in_new_2": "09:00", "out_new_2": "18:30", "br_new_2": "60"})
    page = admin.get("/work/timecards?ym=2026-10&staff_id=1").get_data(as_text=True)
    assert "23:30" in page and "夜勤 1回" in page
    # 夜勤は「1回いくら（深夜手当こみ）」：日勤の分だけ時間で計算 → 基本 10,200（8.5h）＋時間外(0.5h×25%) 150
    # ＋夜勤 5,000（1回）＋交通費 600 ＝ 15,950、雇用保険 88。1回5,000円は最低賃金を下回るので確認が出る
    page = admin.get("/payroll/?ym=2026-10").get_data(as_text=True)
    assert "15,950" in page and "15,862" in page and "最低賃金の確認 1" in page
    # 「時間で計算」：基本 28,200＋時間外(7.5h×25%) 2,250＋深夜(22〜翌9時の11h×25%) 3,300＋夜勤手当 5,000＋交通費 600 ＝ 39,350
    post(admin, "/payroll/settings", {"pay_yakin_mode": "時間で計算"})
    page = admin.get("/payroll/?ym=2026-10").get_data(as_text=True)
    assert "39,350" in page and "39,134" in page and "最低賃金の確認" not in page
    # 深夜を法律どおり22〜翌5時にすると 7h → 2,100
    post(admin, "/payroll/settings", {"pay_yakin_mode": "時間で計算", "pay_night_start": "22:00", "pay_night_end": "05:00"})
    assert "38,150" in admin.get("/payroll/?ym=2026-10").get_data(as_text=True)
    post(admin, "/payroll/settings", {"pay_yakin_mode": "時間で計算"})
    post(admin, "/payroll/?ym=2026-10", {"action": "save_all", "ym": "2026-10"})
    form = {f"e_{k}": v for k, v in [("base", 28200), ("ot", 2250), ("night", 2100), ("yakin", 5000), ("qual", 0), ("shogu", 0),
                                     ("other", 3000), ("commute", 600)]}
    form.update({f"d_{k}": v for k, v in [("health", 0), ("care", 0), ("pension", 0), ("emp", 210), ("itax", 0), ("rtax", 0),
                                          ("other_ded", 0)]})
    post(admin, "/payroll/1/2026-10", dict(form, action="confirm"))
    assert "41,150" in admin.get("/payroll/1/2026-10/slip").get_data(as_text=True)
    assert "まだ明細はありません" in staff.get("/payroll/mine").get_data(as_text=True)     # 見せる前は出ない
    post(admin, "/payroll/1/2026-10", {"action": "share"})
    assert "2026年10月分" in staff.get("/payroll/mine").get_data(as_text=True)
    assert "40,940" in staff.get("/payroll/mine/2026-10").get_data(as_text=True)
    assert staff.get("/payroll/").status_code == 403 and staff.get("/payroll/1/2026-10/slip").status_code == 403
    assert admin.get("/payroll/ledger.xlsx?year=2026").data[:2] == b"PK"
    assert admin.get("/work/timecards.xlsx?ym=2026-10").data[:2] == b"PK"
    # 収支
    post(admin, "/m/expenses/new", {"date": "2026-10-05", "kind": "水道光熱費", "item": "電気代", "amount": "12000"})
    page = admin.get("/payroll/profit?fy=2026").get_data(as_text=True)
    assert "給与（総支給）" in page and "水道光熱費" in page and "12,000" in page


def test_income_tax_estimate():
    from ghms.payroll import half_down, income_tax

    assert income_tax(300000, 0, 2025) == 8350
    assert income_tax(300000, 2, 2025) < income_tax(300000, 0, 2025)
    assert income_tax(100000, 0, 2026) == 0
    assert half_down(100.5) == 100 and half_down(100.51) == 101


def test_compliance_warnings_on_home(client, app):
    post(client, "/m/homes/new", {"name": "ひまわり"})
    post(client, "/m/staff/new", {"name": "佐藤 一郎", "status": "在籍"})
    post(client, "/m/residents/new", {"name": "山田太郎", "home_id": "1", "status": "入居中", "move_in": "2026-01-01"})
    page = client.get("/").get_data(as_text=True)
    assert "実地指導チェック" in page and "虐待防止委員会" in page
    full = client.get("/compliance/").get_data(as_text=True)
    assert "「虐待防止研修」の受講" in full and "業務日誌がない日" in full and "支援記録がない日" in full
    assert "未入力：受給者証番号" in full and "未入力：職種・雇用形態・週の勤務時間" in full
    # 研修を記録する・会議の出席者に名前がある → 消える
    post(client, "/m/trainings/new", {"staff_id": "1", "date": date.today().isoformat(), "kind": "内部研修", "title": "虐待防止研修"})
    post(client, "/m/meetings/new", {"date": date.today().isoformat(), "kind": "感染症研修", "attendees": "佐藤一郎、鈴木"})
    full = client.get("/compliance/").get_data(as_text=True)
    assert "「虐待防止研修」の受講" not in full and "「感染症の研修」の受講" not in full and "「身体拘束適正化の研修」の受講" in full
    # チェックを止める
    post(client, "/compliance/", {"comp_days": "7", "comp_trainings": "", **{f"on::{k}": "1" for k in
                                  ["meetings", "journal", "records", "plans", "incidents", "residents", "attendance", "timecard", "health", "payroll"]}})
    full = client.get("/compliance/").get_data(as_text=True)
    assert "の受講が" not in full and "業務日誌がない日" in full
    # 職員の画面：管理者むけのお知らせ・メニューは出ない
    staff = staff_client(client, app)
    page = staff.get("/").get_data(as_text=True)
    assert "出勤する" in page and "業務日誌がない日" in page
    assert "実地指導チェック" not in page and "受給者証番号" not in page and "収支" not in page
    assert staff.get("/compliance/").status_code == 403


def test_shift_view_for_staff_and_differences(client, app):
    post(client, "/m/staff/new", {"name": "佐藤 一郎", "status": "在籍", "pay_type": "時給", "hourly_wage": "1200"})
    staff = staff_client(client, app)
    post(client, "/users", {"action": "staff", "id": "2", "staff_id": "1"})
    # 管理者が勤務表を組む（2026年9月）：1日 日勤・2日 日勤・3日 夜勤・4日 明け・5日 公休
    post(client, "/shift/", {"ym": "2026-09", "s1_1": "日", "s1_2": "日", "s1_3": "夜", "s1_4": "明", "s1_5": "休"})
    # 打刻：1日は20分おくれ、2日は打刻なし、3日は夜勤どおり、5日は公休なのに出勤
    post(client, "/work/timecards", {"ym": "2026-09", "staff_id": "1", "in_new_1": "09:20", "out_new_1": "18:00", "br_new_1": "60",
                                     "in_new_3": "16:00", "out_new_3": "10:00", "br_new_3": "120",
                                     "in_new_5": "09:00", "out_new_5": "17:00", "br_new_5": "60"})
    from datetime import date as _d

    from ghms.work import shift_differences

    with app.test_request_context():
        diffs = shift_differences(1, _d(2026, 9, 1), _d(2026, 9, 30))
    kinds = {k: [x[0] for x in v] for k, v in diffs.items()}
    assert kinds == {"2026-09-01": ["遅刻"], "2026-09-02": ["打刻なし"], "2026-09-05": ["予定外"]}
    page = client.get("/work/timecards?ym=2026-09&staff_id=1").get_data(as_text=True)
    assert "遅刻" in page and "打刻なし" in page and "予定外" in page and "日勤" in page
    # 職員は自分の勤務表を見られる（変えられない）
    page = staff.get("/work/my-shift?ym=2026-09").get_data(as_text=True)
    assert "日勤" in page and "夜勤" in page and "16:00〜10:00" in page
    assert staff.get("/shift/").status_code == 403
    # 職員のホームに、これから2週間の自分の勤務
    t = date.today()
    post(client, "/shift/", {"ym": t.strftime("%Y-%m"), f"s1_{t.day}": "夜"})
    home = staff.get("/").get_data(as_text=True)
    assert "自分の勤務（これから2週間）" in home and "夜勤" in home


def test_paid_leave_grants_balance_and_pay(client, app):
    post(client, "/m/staff/new", {"name": "佐藤 一郎", "status": "在籍", "hire_date": "2025-03-01", "weekly_hours": "40",
                                  "pay_type": "月給", "base_salary": "220000"})
    post(client, "/m/staff/new", {"name": "田中 恵", "status": "在籍", "hire_date": "2026-01-15", "weekly_hours": "20",
                                  "pay_type": "時給", "hourly_wage": "1100"})
    # 田中さん：入職から6か月、週3日ペースで出勤（タイムカード）
    from datetime import date as _d, timedelta as _td

    with app.app_context():
        from ghms.db import get_db
        db = get_db()
        d = _d(2026, 1, 15)
        while d < _d(2026, 8, 31):
            if d.weekday() in (0, 2, 4):
                db.execute("INSERT INTO timecards (staff_id, date, clock_in, clock_out, break_min) VALUES (2, ?, '09:00', '13:00', 0)",
                           (d.isoformat(),))
            d += _td(days=1)
        db.commit()
    # 勤務表で有給：佐藤さん 9/10・9/11、田中さん 8/3
    post(client, "/shift/", {"ym": "2026-09", "s1_10": "有", "s1_11": "有"})
    post(client, "/shift/", {"ym": "2026-08", "s2_3": "有"})
    page = client.get("/leave/").get_data(as_text=True)
    assert "19.0日" in page            # 佐藤：2025/9/1 に10日＋2026/9/1 に11日 − 2日
    page = client.get("/leave/2").get_data(as_text=True)
    assert "2026-07-15" in page and "週3日相当" in page and 'value="5.0"' in page
    # 時給の人は有給の日に賃金（平均賃金）。月給の人は基本給のまま
    page = client.get("/payroll/2/2026-08").get_data(as_text=True)
    assert "有給休暇の賃金" in page and 'name="e_leave" value="0"' not in page
    assert 'name="e_leave" value="0"' in client.get("/payroll/1/2026-09").get_data(as_text=True)
    # 年5日：最初の付与（2025/9/1）から1年で0日 → 実地指導チェックに出る
    assert "5日のうち 0日" in client.get("/compliance/").get_data(as_text=True)
    # 付与を直す・足す
    post(client, "/leave/1", {"action": "add", "grant_date": "2025-04-01", "days": "3", "basis": "前のソフトからの繰り越し"})
    assert "前のソフトからの繰り越し" in client.get("/leave/1").get_data(as_text=True)


def test_today_list_on_home(client, app):
    post(client, "/m/homes/new", {"name": "ひまわり"})
    post(client, "/m/residents/new", {"name": "山田太郎", "home_id": "1", "status": "入居中", "move_in": "2026-01-01"})
    t = date.today().isoformat()
    home = client.get("/").get_data(as_text=True)
    assert "今日のやること" in home and "ひまわりの業務日誌を書く" in home and "支援記録を書く（1名）" in home
    assert "国保連に請求する" in home and "まだ：山田太郎" in home
    # 日誌と記録を書くと「済」
    post(client, "/journal", {"date": t, "home_id": "1", "slot": "終日", "summary": "穏やか", "r1_content": "落ち着いて過ごす"})
    home = client.get("/").get_data(as_text=True)
    assert "全員書きました" in home
    m = re.search(r'name="key" value="(kokuho:\d{4}-\d{2})"', home)
    assert m
    post(client, "/today/done", {"key": m.group(1)})
    home = client.get("/").get_data(as_text=True)
    assert "取り消す" in home
    # 職員のホームにも（打刻・日誌・記録）。お金の仕事は出ない
    staff = staff_client(client, app)
    page = staff.get("/").get_data(as_text=True)
    assert "今日のやること" in page and "ひまわりの業務日誌を書く" in page and "国保連" not in page
    assert staff.post("/today/done", data={"key": m.group(1), "_csrf": csrf(staff)}).status_code == 403


def test_security_redirects_sessions_and_staff_delete(client, app):
    # next= に制御文字をまぜた外部へのリダイレクトは通さない
    c = app.test_client()
    r = c.post("/login?next=/%09/evil.example", data={"username": "admin", "password": "password123"})
    assert r.headers["Location"].endswith("/") and "evil" not in r.headers["Location"]
    # タイムカードがある職員は消せない（次の職員にデータが引き継がれないように）
    post(client, "/m/staff/new", {"name": "旧 職員", "status": "在籍"})
    with app.app_context():
        from ghms.db import get_db
        get_db().execute("INSERT INTO timecards (staff_id, date, clock_in, clock_out) VALUES (1, '2026-09-01', '09:00', '18:00')")
        get_db().commit()
    post(client, "/m/staff/1/delete", {})
    assert "旧 職員" in client.get("/m/staff/").get_data(as_text=True)
    # ログアウトする前にコピーしたログインは、ログアウトのあと使えない
    with c.session_transaction() as s:
        copied = dict(s)
    post(c, "/logout", {})
    c2 = app.test_client()
    with c2.session_transaction() as s:
        s.update(copied)
    assert c2.get("/").status_code == 302


# ---------------------------------------------------------------- 給与・タイムカード・有給の直し
def test_overtime_per_day_and_early_shift_not_yakin(client, app):
    post(client, "/m/staff/new", {"name": "A", "status": "在籍", "pay_type": "時給", "hourly_wage": "1200"})
    with app.app_context():
        from ghms.db import get_db
        db = get_db()
        # 1日に2回：7〜12時と15〜21時 → 11時間 → 残業3時間。早番 5〜14時（休憩60分）は夜勤ではない
        db.execute("INSERT INTO timecards (staff_id, date, clock_in, clock_out, break_min) VALUES "
                   "(1,'2026-09-01','07:00','12:00',0), (1,'2026-09-01','15:00','21:00',0), (1,'2026-09-02','05:00','14:00',60)")
        db.commit()
    with app.test_request_context():
        from ghms.work import day_cards, month_cards, month_summary, work_minutes

        sm = month_summary(1, date(2026, 9, 1), date(2026, 9, 30))
        assert sm["total"] == 19 * 60 and sm["over"] == 180 and sm["yakin"] == 0 and sm["days"] == 2
        assert not work_minutes({"clock_in": "05:00", "clock_out": "14:00", "break_min": 60})["yakin"]
        assert work_minutes({"clock_in": "17:00", "clock_out": "09:00", "break_min": 60})["yakin"]
        overs = [x["w"]["over"] for x in day_cards(month_cards(1, date(2026, 9, 1), date(2026, 9, 30)))]
        assert overs == [0, 180, 0]
    page = client.get("/work/timecards?ym=2026-09&staff_id=1").get_data(as_text=True)
    assert "残業 3:00" in page and "夜勤 0回" in page


def test_timecard_empty_clock_in_is_kept(client, app):
    post(client, "/m/staff/new", {"name": "A", "status": "在籍", "pay_type": "時給", "hourly_wage": "1200"})
    post(client, "/shift/", {"ym": "2026-09", "s1_1": "日"})
    post(client, "/work/timecards", {"ym": "2026-09", "staff_id": "1", "in_new_1": "09:00", "out_new_1": "18:00", "br_new_1": "60"})
    post(client, "/work/timecards", {"ym": "2026-09", "staff_id": "1", "in_1": "", "out_1": "18:00", "br_1": "60"})
    with app.app_context():
        from ghms.db import get_db
        assert get_db().execute("SELECT clock_in FROM timecards").fetchone()[0] == "09:00"
        # 古いデータで出勤が空の行があっても画面は開ける
        get_db().execute("UPDATE timecards SET clock_in=NULL")
        get_db().commit()
    assert client.get("/work/timecards?ym=2026-09&staff_id=1").status_code == 200
    post(client, "/work/timecards", {"ym": "2026-09", "staff_id": "1", "del_1": "1"})
    with app.app_context():
        from ghms.db import get_db
        assert get_db().execute("SELECT COUNT(*) FROM timecards").fetchone()[0] == 0


def test_standard_monthly_grade_and_rounding():
    from decimal import Decimal

    from ghms.payroll import care_applies, half_down, health_grade, std_monthly

    s = {"std_monthly": None}
    assert std_monthly(s, 200000 + 10000) == {"health": 220000, "pension": 220000, "source": "今月の総支給額 210,000円から"}
    assert health_grade(62999) == 58000 and health_grade(63000) == 68000 and health_grade(1400000) == 1390000
    assert std_monthly(s, 60000)["pension"] == 88000 and std_monthly(s, 900000)["pension"] == 650000
    assert std_monthly({"std_monthly": 700000}, 100000)["health"] == 700000
    assert std_monthly({"std_monthly": 700000}, 100000)["pension"] == 650000
    assert half_down(Decimal(150000) * Decimal("10.15") / 100 / 2) == 7612
    assert half_down(Decimal(1000) * Decimal("0.55") / 100) == 5
    # 介護保険：40歳の誕生日の前日がある月から、65歳の誕生日の前日がある月の前の月まで
    assert care_applies("1986-10-02", date(2026, 9, 30)) is False
    assert care_applies("1986-10-02", date(2026, 10, 31)) is True
    assert care_applies("1986-10-01", date(2026, 9, 30)) is True       # 9/30 に40歳
    assert care_applies("1961-10-02", date(2026, 9, 30)) is True
    assert care_applies("1961-10-02", date(2026, 10, 31)) is False


def test_payroll_uses_grade_commute_and_allowances(client, app):
    post(client, "/m/staff/new", {"name": "A", "status": "在籍", "hire_date": "2020-04-01", "pay_type": "月給",
                                  "base_salary": "200000", "allowance_qual": "10000", "commute_type": "毎月定額", "commute": "10000",
                                  "social_insurance": "1", "birthdate": "1990-01-01"})
    post(client, "/payroll/settings", {"ins_health": "10.15"})
    post(client, "/m/staff/new", {"name": "B", "status": "在籍", "hire_date": "2026-11-15", "pay_type": "月給", "base_salary": "250000"})
    with app.test_request_context():
        from ghms.db import get_db
        from ghms.payroll import compute_pay, payroll_staff

        s = get_db().execute("SELECT * FROM staff WHERE id=1").fetchone()
        d = compute_pay(s, date(2026, 9, 1), date(2026, 9, 30))
        assert d["gross"] == 220000 and d["std"]["health"] == 220000
        assert d["deductions"]["health"] == 11165       # 220,000 × 10.15% ÷ 2
        assert d["unit"] == round(210000 / 160)         # 資格手当も時間単価に入る
        assert d["employer"]["rosai"] == round(220000 * 0.003)
        names = [r["name"] for r in payroll_staff(date(2026, 9, 1), date(2026, 9, 30))]
        assert names == ["A"]                            # 入職前の B は出さない
    assert "標準報酬月額" in client.get("/payroll/1/2026-09").get_data(as_text=True)
    assert "週40時間" in client.get("/payroll/?ym=2026-09").get_data(as_text=True)


def test_leave_part_table_and_deleted_grant_stays(client, app):
    from ghms.leave import PART

    assert PART[4] == [7, 8, 9, 10, 12, 13, 15]
    post(client, "/m/staff/new", {"name": "A", "status": "在籍", "hire_date": "2024-04-01", "weekly_hours": "40"})
    client.get("/leave/1")

    def grants():
        with app.app_context():
            from ghms.db import get_db
            return [tuple(r) for r in get_db().execute("SELECT grant_date, days FROM leave_grants ORDER BY grant_date")]

    assert grants() == [("2024-10-01", 10), ("2025-10-01", 11), ("2026-10-01", 12)]
    with app.app_context():
        from ghms.db import get_db
        gid = get_db().execute("SELECT id FROM leave_grants ORDER BY grant_date").fetchone()[0]
    post(client, "/leave/1", {"action": "save", f"del_{gid}": "1"})
    client.get("/leave/1")
    client.get("/leave/")
    assert grants() == [("2024-10-01", 0), ("2025-10-01", 11), ("2026-10-01", 12)]
    assert "付与なし（管理者が消した）" in client.get("/leave/1").get_data(as_text=True)
    # 入職日を直すと、自動の付与は新しい付与日に作り直す
    with app.app_context():
        from ghms.db import get_db
        get_db().execute("UPDATE staff SET hire_date='2024-06-01' WHERE id=1")
        get_db().commit()
    client.get("/leave/1")
    assert grants() == [("2024-12-01", 10), ("2025-12-01", 11)]


def test_kiosk_failures_do_not_lock_password_login(client, app):
    post(client, "/m/staff/new", {"name": "佐藤 一郎", "status": "在籍"})
    post(client, "/m/staff/new", {"name": "管理 花子", "status": "在籍"})
    staff = staff_client(client, app)
    post(client, "/users", {"action": "staff", "id": "2", "staff_id": "1"})
    post(client, "/users", {"action": "staff", "id": "1", "staff_id": "2"})   # 管理者も職員の情報とつなぐ
    post(staff, "/my-pin", {"current": "mypass2026", "pin": "4826", "pin2": "4826"})
    _register_device(client)
    # 管理者の画面を開いたまま打刻の画面にしても、ログインは残らない。管理者は打刻の一覧に出ない
    page = client.get("/work/kiosk").get_data(as_text=True)
    assert "佐藤 一郎" in page and "管理 花子" not in page
    assert client.get("/").status_code == 302
    page = client.get("/work/kiosk?staff_id=1").get_data(as_text=True)
    k = re.search(r'name="_k" value="([0-9a-f]+)"', page).group(1)
    for _ in range(5):
        client.post("/work/kiosk", data={"_k": k, "staff_id": "1", "action": "in", "temp": "36.4", "pin": "0000"})
    r = client.post("/work/kiosk", data={"_k": k, "staff_id": "1", "action": "in", "temp": "36.4", "pin": "4826"},
                    follow_redirects=True)
    assert "打刻できません" in r.get_data(as_text=True)
    with app.app_context():
        from ghms.db import get_db
        assert get_db().execute("SELECT COUNT(*) FROM timecards").fetchone()[0] == 0
        assert get_db().execute("SELECT locked_until FROM users WHERE id=2").fetchone()[0] is None
    other = app.test_client()
    r = other.post("/login", data={"username": "worker", "password": "mypass2026"})
    assert r.status_code == 302 and "/login" not in r.headers["Location"]


def test_kiosk_closed_when_timecard_feature_off(client, app):
    with app.app_context():
        from ghms.db import set_setting
        set_setting("features_off", "timecard")
    kiosk = app.test_client()
    assert "使わない設定" in kiosk.get("/work/kiosk").get_data(as_text=True)
    assert kiosk.post("/work/kiosk", data={"staff_id": "1", "action": "in"}).status_code == 403


def test_pin_admin_redirected_to_reauth_for_timecards(client, app):
    post(client, "/my-pin", {"current": "password123", "pin": "4826", "pin2": "4826"})
    _register_device(client)
    _logout(client)
    client.post("/pin/1", data={"pin": "4826"})
    for path in ["/work/timecards", "/work/health"]:
        r = client.get(path)
        assert r.status_code == 302 and "/reauth" in r.headers["Location"], path
    r = client.post("/work/timecards", data={"_csrf": csrf(client, "/"), "staff_id": "1"})
    assert r.status_code == 302 and "/reauth" in r.headers["Location"]
