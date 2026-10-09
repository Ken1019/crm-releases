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
    # 前から使っている事業所として試す（使い始めた日より前の書きもれ・仕事は出ないため）
    with app.app_context():
        from ghms.db import get_db
        assert get_db().execute("SELECT value FROM settings WHERE key='system_start'").fetchone()[0] == date.today().isoformat()
        get_db().execute("UPDATE settings SET value='2026-01-01' WHERE key='system_start'")
        get_db().commit()
    return c


def staff_client(admin, app, username="worker"):
    """管理者が職員アカウントを作り、職員が初回ログインでパスワードを変えた状態のクライアント"""
    post(admin, "/users", {"action": "add", "username": username, "password": "temppass1", "role": "staff"})
    c = app.test_client()
    c.post("/login", data={"username": username, "password": "temppass1"})
    post(c, "/password", {"current": "temppass1", "password": "mypass2026", "password2": "mypass2026"})
    return c


def use_punch(app):
    with app.app_context():
        from ghms.db import get_db, set_setting
        set_setting("attend_mode", "punch")
        get_db().commit()


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
import os  # noqa: E402
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
    good = {"version": "99.0.0", "installer": "https://github.com/Ken1019/crm-releases/releases/download/v99.0.0/GHMS-Setup-99.0.0.exe",
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
            updater.download_installer(dict(good, installer="http://github.com/a.exe"), str(tmp_path), _fake_opener(files))
            assert False
        except ValueError:
            pass
        # 登録していない場所（ホスト）からは受け取らない
        evil = {"https://evil.example.com/a.exe": exe}
        with pytest.raises(ValueError, match="登録されていません"):
            updater.download_installer(dict(good, installer="https://evil.example.com/a.exe"), str(tmp_path), _fake_opener(evil))
        with pytest.raises(ValueError, match="登録されていません"):
            updater.fetch_manifest("https://evil.example.com/latest.json", _fake_opener({}))
        # ダウンロードのたびに別の（予想できない名前の）フォルダに置く
        path2 = updater.download_installer(m, str(tmp_path), _fake_opener(files))
        assert os.path.dirname(path2) != os.path.dirname(path)
        # 起動の直前にもう一度確かめる：入れかえられていたら起動しない
        with open(path2, "wb") as f:
            f.write(b"MZ swapped")
        monkey_platform = updater.sys.platform
        updater.sys.platform = "win32"
        try:
            with pytest.raises(ValueError, match="変わっています"):
                updater.launch_installer(path2, good["sha256"])
        finally:
            updater.sys.platform = monkey_platform
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
    # 出かけた日（4日）の夜は住居にいないので、夜間支援はつかない（31日 − 外泊3日 − 出かけた日の夜1日 = 27）
    sh, labels = data()
    assert labels[:3] == ["住居外利用", "退居後支援", "夜間支援等体制加算"] and labels[-1] == "集中的支援加算"
    assert totals() == {"夜間支援等体制加算": 27, "日中支援加算": 1}
    # サービス提供の状況：出かけた日（実績は○）に「住居→外泊」、その間「外泊」、戻った日「外泊戻り」、ふつうの日は空欄
    assert [x["label"] for x in sh["rows"][2:8]] == ["", "ひまわり→外泊", "外泊", "外泊", "外泊", "外泊戻り"]
    assert sh["rows"][3]["code"] == "○" and sh["rows"][3]["marks"][2] == ""
    assert sh["rows"][4]["marks"][2] == "" and sh["rows"][7]["marks"][2] == "1"
    page = client.get("/docs/record-sheets?ym=2026-10").get_data(as_text=True)
    assert "共同生活援助サービス提供実績記録票" in page and "令和 8 年 10 月分" in page and "ひまわり→外泊" in page
    assert "27回" in page and "移行支援住居" in page
    assert client.get("/docs/record-sheets.xlsx?ym=2026-10").data[:2] == b"PK"
    # 夜間支援を1日外す・帰宅時支援加算を手でつける
    form = {f"m1_{d}": "1" for d in range(1, 32) if d not in (1, 4, 5, 6, 7)}
    post(client, "/docs/record-marks", dict(form, ym="2026-10", home_id="1", col="3"))
    post(client, "/docs/record-marks", {"m1_5": "1", "ym": "2026-10", "home_id": "1", "col": "5"})
    assert totals() == {"夜間支援等体制加算": 26, "帰宅時支援加算": 1, "日中支援加算": 1}
    # 実績を入院に直すと、夜間支援の印も自動で外れ、状況は「ひまわり→入院」「入院」「入院戻り」
    form = {f"a1_{d}": "○" for d in range(1, 32)}
    form.update({"a1_5": "外", "a1_6": "外", "a1_7": "外", "a1_10": "日", "a1_2": "入", "ym": "2026-10", "home_id": "1"})
    post(client, "/billing/attendance", form)
    sh, _ = data()
    assert [x["label"] for x in sh["rows"][0:3]] == ["ひまわり→入院", "入院", "入院戻り"]
    assert totals()["夜間支援等体制加算"] == 25
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
    use_punch(app)  # 事務所のPCで打刻するしかた（今は画面に出さない）
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
    with app.app_context():  # 住居を前から使っていたことにする（登録前の日は日誌のもれに数えないため）
        from ghms.db import get_db
        get_db().execute("UPDATE homes SET created_at='2026-01-01 00:00:00'")
        get_db().commit()
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
    assert "体温を記録する" in page and "出勤する" not in page and "業務日誌がない日" in page
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
    assert kinds == {"2026-09-01": ["遅刻"], "2026-09-02": ["確定なし"], "2026-09-05": ["予定外"]}
    page = client.get("/work/timecards?ym=2026-09&staff_id=1").get_data(as_text=True)
    assert "遅刻" in page and "確定なし" in page and "予定外" in page and "日勤" in page
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
        assert d["unit"] == 1313                        # 資格手当も時間単価に入る（210,000÷160＝1,312.5 → 四捨五入で1,313）
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
    use_punch(app)  # 事務所のPCで打刻するしかた（今は画面に出さない）
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
    use_punch(app)  # 事務所のPCで打刻するしかた（今は画面に出さない）
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


# ---------------------------------------------------------------- 請求・実績・日誌の不具合修正
def _q(app, sql, args=()):
    import sqlite3
    con = sqlite3.connect(app.config["DATABASE"])
    rows = con.execute(sql, args).fetchall()
    con.commit()
    con.close()
    return rows


def _benefit(app, first, last):
    from ghms.billing import compute_benefit

    with app.test_request_context():
        results, _, _ = compute_benefit(first, last)
    return results


def _billing_resident(client, **extra):
    post(client, "/m/homes/new", {"name": "ひまわり", "home_type": "介護サービス包括型"})
    data = {"name": "山田太郎", "home_id": "1", "status": "入居中", "support_level": "区分3", "move_in": "2026-01-01",
            "burden_cap": "9300"}
    data.update(extra)
    post(client, "/m/residents/new", data)
    post(client, "/m/basic_units/new", {"home_type": "介護サービス包括型", "support_level": "区分3", "units": "400", "active": "1"})


def test_absence_only_month_bills_present_days(client, app):
    _billing_resident(client)
    # 実績は外泊の登録から入った2日だけ（9/11・9/12）。ほかの日は未入力
    post(client, "/m/absences/new", {"resident_id": "1", "kind": "外泊", "start_date": "2026-09-10",
                                     "end_date": "2026-09-13", "auto_attendance": "1"})
    x = _benefit(app, date(2026, 9, 1), date(2026, 9, 30))[0]
    assert x["billable"] == 28 and x["subtotal"] == 400 * 28
    assert "実績が未入力の日が28日あります（在居として計算）" in x["warnings"]
    html = client.get("/billing/benefit?ym=2026-09").get_data(as_text=True)
    assert "実績が未入力の日が28日あります" in html


def test_move_out_mid_month_bills_only_residence_days(client, app):
    _billing_resident(client, move_out="2026-09-15")
    form = {f"a1_{d}": "○" for d in range(1, 31)}
    form.update({"ym": "2026-09", "home_id": "1"})
    post(client, "/billing/attendance", form)
    # 退居日より後の日は保存しない
    assert _q(app, "SELECT COUNT(*) FROM attendance WHERE resident_id=1")[0][0] == 15
    # 退居後の日に実績が残っていても数えない
    _q(app, "INSERT INTO attendance VALUES (1, '2026-09-20', '○', 'x', 'x')")
    x = _benefit(app, date(2026, 9, 1), date(2026, 9, 30))[0]
    assert x["billable"] == 15 and not any("未入力の日" in w for w in x["warnings"])


def test_grid_keeps_absence_ownership_and_absence_edit_clears_days(client, app):
    _billing_resident(client)
    post(client, "/m/absences/new", {"resident_id": "1", "kind": "外泊", "start_date": "2026-09-10",
                                     "end_date": "2026-09-15", "auto_attendance": "1"})
    form = {f"a1_{d}": ("外" if 11 <= d <= 14 else "○") for d in range(1, 31)}
    form.update({"ym": "2026-09", "home_id": "1"})
    post(client, "/billing/attendance", form)
    owners = dict(_q(app, "SELECT date, updated_by FROM attendance WHERE code='外'"))
    assert set(owners.values()) == {"absence:1"} and len(owners) == 4
    # 12日に戻った → 12〜14日の「外」は消える（○にはしない＝未入力）
    post(client, "/m/absences/1/edit", {"resident_id": "1", "kind": "外泊", "start_date": "2026-09-10",
                                        "end_date": "2026-09-12", "auto_attendance": "1"})
    codes = dict(_q(app, "SELECT date, code FROM attendance WHERE resident_id=1"))
    assert codes["2026-09-11"] == "外" and "2026-09-13" not in codes and codes["2026-09-16"] == "○"
    # 手で入れた日は入院・外泊の登録で上書きしない
    post(client, "/m/absences/new", {"resident_id": "1", "kind": "入院", "start_date": "2026-09-19",
                                     "end_date": "2026-09-22", "auto_attendance": "1"})
    codes = dict(_q(app, "SELECT date, code FROM attendance WHERE resident_id=1"))
    assert codes["2026-09-20"] == "○"
    # 戻った日が出発した日より前ならエラーで保存しない
    r = post(client, "/m/absences/new", {"resident_id": "1", "kind": "外泊", "start_date": "2026-09-25",
                                         "end_date": "2026-09-20", "auto_attendance": "1"})
    assert r.status_code == 200 and "より前になっています" in r.get_data(as_text=True)
    assert _q(app, "SELECT COUNT(*) FROM absences")[0][0] == 2


def test_absence_overlap_delete_and_future_return(client, app):
    from datetime import timedelta

    _billing_resident(client)
    post(client, "/m/absences/new", {"resident_id": "1", "kind": "外泊", "start_date": "2026-08-20",
                                     "end_date": "2026-08-28", "auto_attendance": "1"})
    r = post(client, "/m/absences/new", {"resident_id": "1", "kind": "入院", "start_date": "2026-08-22",
                                         "end_date": "2026-08-25", "auto_attendance": "1"})
    assert "期間が重なっています" in client.get(r.headers["Location"]).get_data(as_text=True)
    assert dict(_q(app, "SELECT date, code FROM attendance WHERE resident_id=1"))["2026-08-23"] == "入"
    post(client, "/m/absences/2/delete", {})
    codes = dict(_q(app, "SELECT date, code FROM attendance WHERE resident_id=1"))
    assert codes["2026-08-23"] == "外" and len(codes) == 7
    # 入院中のまま削除 → 状態が入居中に戻る
    post(client, "/m/absences/new", {"resident_id": "1", "kind": "入院", "start_date": date.today().isoformat(),
                                     "auto_attendance": "1"})
    assert _q(app, "SELECT status FROM residents WHERE id=1")[0][0] == "入院中"
    aid = _q(app, "SELECT MAX(id) FROM absences")[0][0]
    post(client, f"/m/absences/{aid}/delete", {})
    assert _q(app, "SELECT status FROM residents WHERE id=1")[0][0] == "入居中"
    # 戻った日に先の日付（予定）を入れても、その日までは不在中
    back = date.today() + timedelta(days=5)
    post(client, "/m/absences/new", {"resident_id": "1", "kind": "入院",
                                     "start_date": (date.today() - timedelta(days=1)).isoformat(),
                                     "end_date": back.isoformat(), "auto_attendance": "1"})
    assert _q(app, "SELECT status FROM absences ORDER BY id DESC")[0][0] == "不在中"
    assert _q(app, "SELECT status FROM residents WHERE id=1")[0][0] == "入院中"
    codes = dict(_q(app, "SELECT date, code FROM attendance WHERE resident_id=1"))
    assert codes[(back - timedelta(days=1)).isoformat()] == "入" and back.isoformat() not in codes


def test_journal_edit_updates_right_record(client, app):
    post(client, "/m/homes/new", {"name": "ひまわり"})
    post(client, "/m/residents/new", {"name": "山田太郎", "home_id": "1", "status": "入居中"})
    post(client, "/m/support_records/new", {"date": "2026-10-02", "resident_id": "1", "time_slot": "終日", "content": "first"})
    post(client, "/m/support_records/new", {"date": "2026-10-02", "resident_id": "1", "time_slot": "終日", "content": "second"})
    html = client.get("/journal?date=2026-10-02&home_id=1&slot=終日").get_data(as_text=True)
    assert 'name="r1_id" value="2"' in html and ">second</textarea>" in html
    base = {"date": "2026-10-02", "home_id": "1", "slot": "終日", "by_id": "1"}
    post(client, "/journal", dict(base, r1_id="2", r1_content="second edited", r1_staff="A"))
    assert _q(app, "SELECT id, content FROM support_records ORDER BY id") == [(1, "first"), (2, "second edited")]
    # 画面を開いたときに記録がなかった行は、新しく作る（ほかの記録を上書きしない）
    post(client, "/journal", dict(base, r1_content="third", r1_staff="A"))
    assert _q(app, "SELECT id, content FROM support_records ORDER BY id")[-1] == (3, "third")
    # 欄を全部消すと、その記録だけ削除
    post(client, "/journal", dict(base, r1_id="3", r1_content="", r1_staff="A"))
    assert [r[0] for r in _q(app, "SELECT id FROM support_records ORDER BY id")] == [1, 2]
    # 業務日誌を全部消して保存すると、消した内容で上書き
    post(client, "/journal", dict(base, summary="誤記"))
    post(client, "/journal", dict(base, summary=""))
    assert _q(app, "SELECT summary FROM daily_logs") == [(None,)]


def test_no_home_residents_can_be_chosen(client, app):
    post(client, "/m/homes/new", {"name": "ひまわり"})
    post(client, "/m/residents/new", {"name": "住居なし花子", "status": "入居中", "move_in": "2026-01-01"})
    html = client.get("/billing/attendance?ym=2026-09").get_data(as_text=True)
    assert "住居未設定（1名）" in html and "住居なし花子" not in html
    assert "住居なし花子" in client.get("/billing/attendance?ym=2026-09&home_id=0").get_data(as_text=True)
    page = client.get("/journal?home_id=0").get_data(as_text=True)
    assert "住居なし花子" in page and "住居未設定" in page
    post(client, "/journal", {"date": "2026-10-02", "home_id": "0", "slot": "終日", "by_id": "1", "r1_content": "元気"})
    assert _q(app, "SELECT content FROM support_records") == [("元気",)]


def test_addon_mid_month_start_counts_only_days_after(client, app):
    _billing_resident(client)
    aid = _q(app, "SELECT id FROM addons WHERE name='重度障害者支援加算'")[0][0]
    _q(app, "UPDATE addons SET active=1, units=180 WHERE id=?", (aid,))
    post(client, "/m/resident_addons/new", {"resident_id": "1", "addon_id": str(aid), "start_on": "2026-09-25"})
    lines = {name: n for name, _, n, _ in _benefit(app, date(2026, 9, 1), date(2026, 9, 30))[0]["lines"]}
    assert lines["重度障害者支援加算"] == 6  # 25日〜30日
    # 加算そのものの算定開始日もあわせて見る
    _q(app, "UPDATE addons SET start_on='2026-09-28' WHERE id=?", (aid,))
    lines = {name: n for name, _, n, _ in _benefit(app, date(2026, 9, 1), date(2026, 9, 30))[0]["lines"]}
    assert lines["重度障害者支援加算"] == 3
    # 回数の加算：実績記録票に同じ名前の項目がなければ注意を出す
    _q(app, "UPDATE addons SET unit_type='回' WHERE id=?", (aid,))
    x = _benefit(app, date(2026, 9, 1), date(2026, 9, 30))[0]
    assert any("回数の加算は自動で数えていません" in w for w in x["warnings"])


def test_benefit_rounding_and_invoice_month(client, app):
    from ghms.billing import shogu_units, units_to_yen

    assert units_to_yen(180, "10.45") == 1881  # 小数の誤差で 1,880 にならない
    assert units_to_yen(1, "10.45") == 10
    assert shogu_units(15, 10) == 2 and shogu_units(14, 10) == 1  # 1.5 → 2（四捨五入）
    post(client, "/m/residents/new", {"name": "山田太郎", "status": "入居中"})
    post(client, "/m/invoices/new", {"ym": "2026-9", "resident_id": "1", "rent": "1000"})
    assert _q(app, "SELECT ym FROM invoices") == [("2026-09",)]
    r = post(client, "/m/invoices/new", {"ym": "10月", "resident_id": "1"})
    assert r.status_code == 200 and "年-月" in r.get_data(as_text=True)
    assert _q(app, "SELECT COUNT(*) FROM invoices")[0][0] == 1


def test_documents_newest_and_choices(client, app):
    post(client, "/m/residents/new", {"name": "山田太郎", "status": "入居中"})
    post(client, "/m/resident_documents/new", {"resident_id": "1", "doc_type": "利用契約書", "signed_on": "2025-04-01",
                                               "expires_on": "2020-01-01"})
    post(client, "/m/resident_documents/new", {"resident_id": "1", "doc_type": "利用契約書", "expires_on": "2099-01-01"})
    html = client.get("/billing/documents").get_data(as_text=True)
    assert "期限切れ" not in html  # 署名日のない新しい登録がいちばん新しい
    # 事業所で増やした書類の種類も確認の対象になる
    post(client, "/settings/choices", {"field": "resident_documents.doc_type", "new": "見学同意書"})
    assert "見学同意書" in client.get("/billing/documents").get_data(as_text=True)


# ---------------------------------------------------------------- 今日のやること（上から片づければ決まりが守れる）
def _sql(app, q, args=()):
    with app.app_context():
        from ghms.db import get_db
        db = get_db()
        cur = db.execute(q, args)
        db.commit()
        return cur.lastrowid


def _todo(app, admin=True, staff_id=None, username="admin"):
    with app.test_request_context("/"):
        from flask import g

        from ghms.db import get_db
        from ghms.today import build
        g.user = get_db().execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        return build(user_admin=admin, staff_id=staff_id)


def _checks(app, **kw):
    with app.test_request_context("/"):
        from flask import g

        from ghms.compliance import run_checks
        from ghms.db import get_db
        g.user = get_db().execute("SELECT * FROM users WHERE username='admin'").fetchone()
        return run_checks(**kw)


def _titles(t):
    return [(gp["level"], x["title"], x["detail"], x["done"]) for gp in t["groups"] for x in gp["items"]]


def test_training_exact_name_and_committee_not_training(client, app):
    from ghms.compliance import attendee_names, is_training_meeting

    assert attendee_names("田中太郎、佐藤　一郎,鈴木さん／山田・ｱｲ") >= {"田中太郎", "佐藤一郎", "鈴木", "山田", "アイ"}
    assert "全員" not in attendee_names("管理者のみ（全員には後日回覧）")
    assert not is_training_meeting("虐待防止委員会", "定例") and is_training_meeting("職員会議", "ＢＣＰの研修")
    t = date.today().isoformat()
    for name in ("田中", "田中太郎", "佐藤 一郎"):
        _sql(app, "INSERT INTO staff (name, status) VALUES (?, '在籍')", (name,))
    # 委員会は研修に数えない
    _sql(app, "INSERT INTO meetings (date, kind, title, attendees) VALUES (?,?,?,?)", (t, "虐待防止委員会", "定例", "田中太郎、田中"))
    # 研修の回：名前がぴったり同じ人だけ（「田中」は「田中太郎」の出席にならない）
    _sql(app, "INSERT INTO meetings (date, kind, title, attendees) VALUES (?,?,?,?)", (t, "虐待防止研修", "", "田中太郎、佐藤　一郎"))
    # 全角の「ＢＣＰ」も見つける。「全員」は1つの名前として書いたときだけ
    _sql(app, "INSERT INTO meetings (date, kind, title, attendees) VALUES (?,?,?,?)", (t, "職員会議", "ＢＣＰ研修", "全員"))
    _sql(app, "INSERT INTO meetings (date, kind, title, attendees) VALUES (?,?,?,?)", (t, "感染症研修", "", "管理者のみ（全員には後日回覧）"))
    miss = {(x["who"], x["msg"].split("」")[0][1:]) for x in _checks(app) if x["check"] == "staff_training"}
    assert ("田中", "虐待防止研修") in miss
    assert ("田中太郎", "虐待防止研修") not in miss and ("佐藤 一郎", "虐待防止研修") not in miss
    assert not any(topic.startswith("業務継続計画") for _, topic in miss)
    assert {("田中", "感染症の研修"), ("田中太郎", "感染症の研修")} <= miss
    # 身体拘束は委員会しかないので全員まだ
    assert {("田中", "身体拘束適正化の研修"), ("田中太郎", "身体拘束適正化の研修")} <= miss


def test_today_skips_empty_home_and_not_yet_moved_in(client, app):
    from datetime import timedelta

    today = date.today()
    future = (today + timedelta(days=10)).isoformat()
    for name in ("あおば", "からっぽ", "みどり"):
        _sql(app, "INSERT INTO homes (name, created_at) VALUES (?, '2020-01-01 00:00:00')", (name,))
    _sql(app, "INSERT INTO residents (name, kana, home_id, move_in, status) VALUES ('これから', 'こ', 1, ?, '入居中')", (future,))
    _sql(app, "INSERT INTO residents (name, kana, home_id, move_in, status) VALUES ('いまの人', 'い', 3, '2020-01-01', '入居中')")
    _sql(app, "INSERT INTO residents (name, kana, home_id, move_in, status) VALUES ('外泊中', 'が', 3, '2020-01-01', '入居中')")
    _sql(app, "INSERT INTO attendance (resident_id, date, code) VALUES (3, ?, '外')", (today.isoformat(),))
    titles = [t for _, t, _, _ in _titles(_todo(app))]
    assert "みどりの業務日誌を書く" in titles
    assert "あおばの業務日誌を書く" not in titles and "からっぽの業務日誌を書く" not in titles
    assert "みどりの入居者の支援記録を書く（1名）" in titles  # 外泊中の人はのぞく
    assert not any("あおば" in t for t in titles)
    found = _checks(app)
    assert not any(x["check"] == "journal" and x["who"] in ("あおば", "からっぽ") for x in found)
    assert not any(x["check"] == "records" and x["who"] == "これから" for x in found)
    assert any(x["check"] == "journal" and x["who"] == "みどり" for x in found)
    # 住居を登録した日より前は、日誌のもれに数えない
    _sql(app, "UPDATE homes SET created_at=? WHERE id=3", (today.isoformat() + " 08:00:00",))
    assert not any(x["check"] == "journal" for x in _checks(app))


def test_staff_items_for_admin_grouped_separately(client, app):
    from datetime import timedelta

    sid = _sql(app, "INSERT INTO staff (name, status, hire_date) VALUES ('職員一', '在籍', '2020-01-01')")
    old = (date.today() - timedelta(days=3)).isoformat()
    _sql(app, "INSERT INTO timecards (staff_id, date, clock_in) VALUES (?, ?, '09:00')", (sid, old))  # 退勤忘れ
    staff = staff_client(client, app)
    _sql(app, "UPDATE users SET staff_id=? WHERE username='worker'", (sid,))
    t = _todo(app, admin=False, staff_id=sid, username="worker")
    levels = [gp["level"] for gp in t["groups"]]
    assert levels[-1] == "tell"
    tell = t["groups"][-1]
    assert not tell["counted"]
    assert any(x["title"].startswith("タイムカードの退勤忘れ") for x in tell["items"])
    assert all(x["detail"].startswith("管理者に連絡") for x in tell["items"])
    counted = [x for gp in t["groups"] if gp["counted"] for x in gp["items"]]
    assert t["total"] == len(counted) and t["left"] == sum(1 for x in counted if not x["done"])
    page = staff.get("/").get_data(as_text=True)
    assert "管理者に伝えること" in page and "管理者に連絡" in page
    # 管理者のリストには「管理者に伝えること」は出ない
    assert "tell" not in [gp["level"] for gp in _todo(app)["groups"]]


def test_manual_yearly_item_key(client, app):
    today = date.today()
    half = f"{today.year}-H{1 if today.month <= 6 else 2}"
    fy = today.year if today.month >= 4 else today.year - 1
    home = client.get("/").get_data(as_text=True)
    assert f'value="shobo:{half}"' in home and f'value="kyoryoku:{fy}"' in home
    assert post(client, "/today/done", {"key": f"shobo:{half}"}).status_code == 302
    assert post(client, "/today/done", {"key": f"kyoryoku:{fy}"}).status_code == 302
    items = {x["key"]: x for gp in _todo(app)["groups"] for x in gp["items"] if x["key"]}
    assert items[f"shobo:{half}"]["done"] and items[f"kyoryoku:{fy}"]["done"]
    # 変な期間・知らないキーは受けつけない
    assert post(client, "/today/done", {"key": "shobo:abc"}).status_code == 400
    assert post(client, "/today/done", {"key": "nothing:2026"}).status_code == 400
    # 職員は押せない
    staff = staff_client(client, app)
    assert staff.post("/today/done", data={"key": f"shobo:{half}", "_csrf": csrf(staff)}).status_code == 403


def test_meeting_track_defaults_migration(app):
    _sql(app, "UPDATE choice_options SET track_days=NULL WHERE field='meetings.kind' AND value IN ('地域連携推進会議', '感染症研修')")
    _sql(app, "DELETE FROM settings WHERE key='track_defaults_v2'")
    from ghms.db import init_db

    init_db(app.config["DATABASE"])
    with app.app_context():
        from ghms.db import get_db
        rows = dict(get_db().execute("SELECT value, track_days FROM choice_options WHERE field='meetings.kind'").fetchall())
    assert rows["地域連携推進会議"] == 365 and rows["感染症研修"] == 183 and rows["虐待防止研修"] == 365


def test_pin_admin_home_hides_money_until_password(client, app):
    post(client, "/m/staff/new", {"name": "佐藤 一郎", "status": "在籍"})
    post(client, "/my-pin", {"current": "password123", "pin": "4826", "pin2": "4826"})
    _register_device(client)
    _logout(client)
    client.post("/pin/1", data={"pin": "4826"})
    home = client.get("/").get_data(as_text=True)
    assert "パスワードで本人確認して開く" in home and "今月の収支" not in home
    # 連絡記録をオフにすると、入院・帰省の画面からも連絡を記録できない
    c = app.test_client()
    c.post("/login", data={"username": "admin", "password": "password123"})
    post(c, "/m/homes/new", {"name": "ひまわり"})
    post(c, "/m/residents/new", {"name": "山田太郎", "home_id": "1", "status": "入居中"})
    post(c, "/m/absences/new", {"resident_id": "1", "kind": "入院", "start_date": "2026-09-01"})
    from ghms.customize import FEATURES

    post(c, "/settings/features", {"on": [k for k, *_ in FEATURES if k != "contact_logs"]})
    assert "入院中" in c.get("/absences/1").get_data(as_text=True) or c.get("/absences/1").status_code == 200
    r = c.post("/absences/1", data={"_csrf": csrf(c), "date": "2026-09-02", "content": "電話"})
    assert r.status_code in (302, 403)
    with app.app_context():
        from ghms.db import get_db
        assert get_db().execute("SELECT COUNT(*) FROM contact_logs").fetchone()[0] == 0


def _flash_texts(html):
    return re.sub(r"<[^>]+>", " ", html)


def test_bad_settings_rejected_and_home_still_opens(client, app):
    # まちがった値は保存せず、前の値のまま。エラーを出す
    post(client, "/settings", {"cert_alert_days": "90", "unit_price": "１０．２８円", "invoice_due_day": "27日"})
    from ghms.db import get_setting
    with app.app_context():
        assert get_setting("cert_alert_days") == "90" and get_setting("unit_price") == "10.28"
        assert get_setting("invoice_due_day") == "27"
    for bad in ("abc", "nan", "1e400", "99999999999999999999", "-5"):
        r = post(client, "/settings", {"cert_alert_days": bad, "unit_price": "10"})
        html = client.get(r.headers["Location"]).get_data(as_text=True)
        assert "前の値のままにしました" in html, bad
        with app.app_context():
            assert get_setting("cert_alert_days") == "90", bad
    assert 'type="number"' in client.get("/settings").get_data(as_text=True)
    # 給与の設定：「9.85%」「１０．２８」は数字にして保存、まちがいは前の値のまま
    post(client, "/payroll/settings", {"ins_health": "9.85%", "pay_fever": "３７．８", "pay_break_default": "abc",
                                       "pay_night_start": "25:99"})
    with app.app_context():
        assert get_setting("ins_health") == "9.85" and get_setting("pay_fever") == "37.8"
        assert get_setting("pay_break_default", "60") == "60" and get_setting("pay_night_start", "22:00") == "22:00"
    # 前の版などで、まちがった値が入ってしまっていても、ホーム・給与・打刻の画面は開ける
    with app.app_context():
        from ghms.db import get_db
        db = get_db()
        for k, v in [("cert_alert_days", "abc"), ("plan_alert_days", "1e400"), ("unit_price", "nan"),
                     ("pay_break_default", "nan"), ("pay_max_shift_hours", "inf"), ("pay_fever", "x"),
                     ("ins_health", "NaN"), ("pay_monthly_hours", "0"), ("full_time_hours", "-1"),
                     ("session_timeout_min", "99999999999999999999"), ("invoice_due_day", "abc")]:
            db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, v))
        db.commit()
    ym = date.today().strftime("%Y-%m")
    for path in ["/", "/addons/check", "/payroll/", "/work/timecards", "/work/health", "/shift/", f"/billing/invoices?ym={ym}",
                 "/settings", "/payroll/settings"]:
        assert client.get(path).status_code == 200, path


def test_legacy_builtin_record_columns_keep_counting(app):
    """前の版（帰宅時支援・入院時支援が builtin）の列は、auto の列にして実績の帰・入で数え続ける。
    事業所で足した列は消さず、様式18-1の列は「使わない」で足す"""
    import sqlite3
    from ghms.docs import AUTO_CODES, mark_of, seed_record_columns
    con = sqlite3.connect(app.config["DATABASE"])
    con.execute("DELETE FROM record_columns")
    con.execute("DELETE FROM settings WHERE key='record_layout'")
    con.executemany("INSERT INTO record_columns (label, sort, active, auto, builtin, mark) VALUES (?,?,1,?,?,'1')",
                    [("日中支援", 10, "", "day"), ("帰宅時支援", 20, "", "home"), ("入院時支援", 30, "", "hosp"),
                     ("夜間支援", 40, "stay", None), ("通院の付き添い", 50, "", None)])
    con.commit()
    seed_record_columns(con)
    con.row_factory = sqlite3.Row
    cols = {r["label"]: r for r in con.execute("SELECT * FROM record_columns")}
    assert cols["帰宅時支援"]["auto"] == "home" and cols["帰宅時支援"]["builtin"] is None
    assert cols["入院時支援"]["auto"] == "hosp" and cols["入院時支援"]["builtin"] is None
    assert mark_of(cols["帰宅時支援"], "帰", None) and mark_of(cols["入院時支援"], "入", None)
    assert not mark_of(cols["帰宅時支援"], "○", None)
    assert "home" in AUTO_CODES and "hosp" in AUTO_CODES
    assert cols["通院の付き添い"]["active"] == 1  # 事業所で足した列は消さない
    assert cols["住居外利用"]["active"] == 0 and "日中支援加算" not in cols  # 足りない列は「使わない」で足す
    assert con.execute("SELECT value FROM settings WHERE key='record_layout'").fetchone()[0] == "18-1"
    n = con.execute("SELECT COUNT(*) FROM record_columns").fetchone()[0]
    seed_record_columns(con)  # 2回目は何も変えない
    assert con.execute("SELECT COUNT(*) FROM record_columns").fetchone()[0] == n
    # 前の並びのまま（4列だけ・○なし）なら様式18-1に入れ替える
    con.execute("DELETE FROM record_columns")
    con.execute("DELETE FROM settings WHERE key='record_layout'")
    con.executemany("INSERT INTO record_columns (label, sort, active, auto, builtin, mark) VALUES (?,?,1,?,?,'1')",
                    [("日中支援", 10, "", "day"), ("帰宅時支援", 20, "", "home"), ("入院時支援", 30, "", "hosp"),
                     ("夜間支援", 40, "stay", None)])
    con.commit()
    seed_record_columns(con)
    labels = [r[0] for r in con.execute("SELECT label FROM record_columns ORDER BY sort")]
    assert labels[0] == "住居外利用" and "帰宅時支援" not in labels and len(labels) == 10
    con.close()


def test_config_ini_with_percent(tmp_path, monkeypatch):
    from ghms import runtime
    monkeypatch.setenv("GHMS_HOME", str(tmp_path))
    (tmp_path / "config.ini").write_text("[server]\nport = 8123\n[product]\nname = 100%サポート\n"
                                         "update_url = https://example.com/a%20b/latest.json\n", encoding="utf-8")
    assert runtime.load_product()["name"] == "100%サポート"
    assert runtime.load_product()["update_url"].endswith("a%20b/latest.json")
    assert runtime.load_config()["port"] == 8123
    # ポートがまちがっていれば 8000（落ちない）
    (tmp_path / "config.ini").write_text("[server]\nport = abc\n", encoding="utf-8")
    assert runtime.load_config()["port"] == 8000
    (tmp_path / "config.ini").write_text("[server]\nport = 70000\nlan = 1\n", encoding="utf-8")
    assert runtime.load_config() == {"port": 8000, "lan": True, "window": True}
    # Shift-JIS（インストーラーが書く形）でも読める
    (tmp_path / "config.ini").write_bytes("[office]\nname = ひだまり\n".encode("cp932"))
    assert runtime.load_office()["name"] == "ひだまり"


def test_huge_and_odd_numbers_do_not_crash(client, app):
    # 数値の欄：無限大・とても大きい値はエラーとして画面に出す（500にしない）
    for v in ("1e400", "inf", "nan", "1e13"):
        r = post(client, "/m/homes/new", {"name": "テスト住居", "capacity": v})
        html = r.get_data(as_text=True)
        assert r.status_code == 200 and ("数値で入力" in html or "大きすぎます" in html), v
    for v in ("99999999999999999999", "-3", "1.5"):
        r = post(client, "/m/residents/new", {"name": "テスト", "home_id": v})
        assert r.status_code == 200 and "値が正しくありません" in r.get_data(as_text=True), v
    # 一覧のページ・期間・年月の値がおかしくても開ける
    for path in ["/m/residents/?page=abc", "/m/residents/?page=99999999999999999999", "/docs/committee?days=abc",
                 "/docs/committee?days=99999999999", "/docs/committee?days=-5", "/billing/invoices?ym=9999-12",
                 "/billing/invoices?ym=0001-01", "/docs/menus?week=9999-12-31", "/docs/record-sheets?ym=9999-12",
                 "/journal?date=9999-12-31", "/journal?date=0001-01-01", "/work/health?date=9999-12-31",
                 "/m/residents/99999999999999999999", "/journal?home_id=99999999999999999999"]:
        assert client.get(path).status_code in (200, 404), path
    from ghms.views import parse_date, parse_ym
    assert parse_ym("9999-12")[0] == date.today().replace(day=1)
    assert parse_ym("0001-01")[0] == date.today().replace(day=1)
    assert parse_date("9999-12-31") is None and parse_date("1950-04-01") == date(1950, 4, 1)


def test_features_ignore_unknown_keys(client, app):
    from ghms.customize import FEATURES
    keys = [k for k, *_ in FEATURES]
    r = post(client, "/settings/features", {"on": keys[:1] + ["nonexistent_feature", "<script>"]})
    assert r.status_code == 302
    with app.app_context():
        from ghms.db import get_setting
        off = get_setting("features_off").split(",")
    assert set(off) == set(keys[1:])
    assert client.get("/").status_code == 200


# ---------------------------------------------------------------- 見直し（記録票の状況・夜間支援・打刻・端数・使い始めた日など）
def _sheet(app, ym_first, ym_last):
    from ghms.docs import record_sheet_data

    with app.test_request_context("/"):
        from flask import g

        from ghms.db import get_db
        g.user = get_db().execute("SELECT * FROM users WHERE username='admin'").fetchone()
        sheets, cols = record_sheet_data(ym_first, ym_last)
        return sheets[0], [c["label"] for c in cols]


def test_record_label_on_departure_day_and_no_night_support(client, app):
    _setup_billing(client)
    # 1泊だけの外泊（12日に出て13日に戻る）：実績はどちらも○のまま。11月1日からの外泊は10月31日が出かけた日
    post(client, "/m/absences/new", {"resident_id": "1", "kind": "外泊", "start_date": "2026-10-12", "end_date": "2026-10-13",
                                     "auto_attendance": "1"})
    _sql(app, "INSERT INTO attendance (resident_id, date, code) VALUES (1, '2026-11-01', '外')")
    sh, labels = _sheet(app, date(2026, 10, 1), date(2026, 10, 31))
    night = labels.index("夜間支援等体制加算")
    rows = {x["d"].day: x for x in sh["rows"]}
    assert rows[12]["code"] == "○" and rows[12]["label"] == "ひまわり→外泊" and rows[12]["marks"][night] == ""
    assert rows[13]["label"] == "外泊戻り" and rows[13]["marks"][night] == "1"
    assert rows[4]["label"] == "ひまわり→外泊" and rows[4]["marks"][night] == ""      # 4日に出て5〜7日が外泊
    assert [rows[d]["label"] for d in (5, 6, 7, 8)] == ["外泊", "外泊", "外泊", "外泊戻り"]
    assert rows[31]["label"] == "ひまわり→外泊" and rows[31]["marks"][night] == ""    # 次の月の1日が外泊
    assert rows[11]["label"] == "" and rows[11]["marks"][night] == "1"
    # 31日 − 外泊3日 − 出かけた日の夜（4日・12日・31日）= 25
    assert sh["totals"][night] == 25
    # 月の1日から外泊がはじまるときは、前の月の末日が出かけた日
    sh, _ = _sheet(app, date(2026, 11, 1), date(2026, 11, 30))
    assert sh["rows"][0]["label"] == "外泊" and sh["rows"][1]["label"] == "外泊戻り"


def test_benefit_counts_night_support_from_record_sheet(client, app):
    _setup_billing(client)
    _sql(app, "UPDATE addons SET active=1, units=100 WHERE name='夜間支援等体制加算（Ⅰ）'")
    from ghms.billing import compute_benefit

    def nights():
        with app.test_request_context("/"):
            res, _, _ = compute_benefit(date(2026, 10, 1), date(2026, 10, 31))
            return {name: n for name, _, n, _ in res[0]["lines"]}.get("夜間支援等体制加算（Ⅰ）")

    assert nights() == 27  # 外泊3日と、出かけた日（4日）の夜は数えない
    # 記録票の表で20日の夜を外すと、概算も減る
    form = {f"m1_{d}": "1" for d in range(1, 32) if d not in (4, 5, 6, 7, 20)}
    post(client, "/docs/record-marks", dict(form, ym="2026-10", home_id="1", col="3"))
    assert nights() == 26
    from ghms.billing import addon_key
    assert addon_key("夜間支援等体制加算（Ⅰ）") == addon_key("夜間支援等体制加算") == "夜間支援等体制加算"


def test_forgotten_clock_out_then_new_clock_in(client, app):
    use_punch(app)  # 事務所のPCで打刻するしかた（今は画面に出さない）
    from datetime import datetime, timedelta

    from werkzeug.datastructures import MultiDict

    sid = _sql(app, "INSERT INTO staff (name, status, pay_type, hourly_wage) VALUES ('佐藤 一郎', '在籍', '時給', '1200')")
    start = datetime.now() - timedelta(hours=14)  # 退勤を押し忘れて14時間（上限の20時間より前）
    _sql(app, "INSERT INTO timecards (staff_id, date, clock_in) VALUES (?, ?, ?)", (sid, start.date().isoformat(), start.strftime("%H:%M")))
    with app.test_request_context("/"):
        from flask import g

        from ghms.db import get_db
        from ghms.work import forgotten_cards, month_summary, open_card, punch
        db = get_db()
        g.user = db.execute("SELECT * FROM users WHERE username='admin'").fetchone()
        assert open_card(sid) is not None and not forgotten_cards(sid)
        msgs = punch(sid, "in", MultiDict({"temp": "36.5"}), "admin")
        assert any(f"前回（{start.month}/{start.day} {start:%H:%M}〜）の退勤が押されていません" in m for _, m in msgs)
        assert any("出勤しました" in m for _, m in msgs)
        cards = db.execute("SELECT * FROM timecards WHERE staff_id=? ORDER BY id", (sid,)).fetchall()
        assert len(cards) == 2 and cards[0]["clock_out"] is None
        assert open_card(sid)["id"] == cards[1]["id"]                      # 退勤を押すと新しい打刻が閉じる
        assert [c["id"] for c in forgotten_cards(sid)] == [cards[0]["id"]]  # 前の打刻は「退勤忘れ」
        punch(sid, "out", MultiDict({"break_min": "0"}), "admin")
        db.commit()
        cards = db.execute("SELECT * FROM timecards WHERE staff_id=? ORDER BY id", (sid,)).fetchall()
        assert cards[0]["clock_out"] is None and cards[1]["clock_out"] is not None
        first = start.date().replace(day=1)
        s = month_summary(sid, first, date.today())
        assert s["yakin"] == 0 and s["missing"] == 1                     # 前の打刻と今日の退勤をつなげて夜勤にしない
        # すぐにもう一度出勤を押すと、出勤中なので断る
        punch(sid, "in", MultiDict({"temp": "36.5"}), "admin")
        msgs = punch(sid, "in", MultiDict({"temp": "36.5"}), "admin")
        assert msgs[0][0] == "error" and "すでに" in msgs[0][1]


def test_pay_rounding_half_up(client, app):
    from decimal import Decimal

    from ghms.payroll import compute_pay, yen_half_up

    assert yen_half_up(Decimal("862.5")) == 863 and yen_half_up(862.5) == 863 and yen_half_up(862.49) == 862
    sid = _sql(app, "INSERT INTO staff (name, status, pay_type, hourly_wage) VALUES ('佐藤 一郎', '在籍', '時給', '1150')")
    # 9:00〜20:00（休憩なし）= 11時間、時間外3時間 × 1,150円 × 25% = 862.5円 → 863円
    _sql(app, "INSERT INTO timecards (staff_id, date, clock_in, clock_out, break_min) VALUES (?, '2026-09-01', '09:00', '20:00', 0)", (sid,))
    with app.test_request_context("/"):
        from ghms.db import get_db
        s = get_db().execute("SELECT * FROM staff WHERE id=?", (sid,)).fetchone()
        d = compute_pay(s, date(2026, 9, 1), date(2026, 9, 30))
    assert d["earnings"]["ot"] == 863 and d["earnings"]["base"] == 12650


def test_nothing_before_system_start(client, app):
    from datetime import timedelta

    today = date.today()
    _sql(app, "INSERT INTO homes (name, created_at) VALUES ('みどり', '2020-01-01 00:00:00')")
    _sql(app, "INSERT INTO residents (name, kana, home_id, move_in, status) VALUES ('いまの人', 'い', 1, '2020-01-01', '入居中')")
    found = _checks(app)
    assert any(x["check"] == "journal" for x in found) and any(x["check"] == "records" for x in found)
    titles = [t for _, t, _, _ in _titles(_todo(app))]
    pf = (today.replace(day=1) - timedelta(days=1))
    assert any(t.startswith(f"{pf.month}月の実績を全員・全日入れる（みどり）") for t in titles)
    # 今日から使い始めた：きのうまでの書きもれ・先月の仕事は出さない
    _sql(app, "UPDATE settings SET value=? WHERE key='system_start'", (today.isoformat(),))
    found = _checks(app)
    assert not any(x["check"] in ("journal", "records", "attendance") for x in found)
    titles = [t for _, t, _, _ in _titles(_todo(app))]
    assert not any("実績を全員" in t or "国保連" in t or "源泉所得税" in t for t in titles)
    assert "みどりの業務日誌を書く" in titles   # 今日の分は出る
    # 先月の書きもれは先月の1日から見る（7日より前の分も、直すまで出つづける）
    _sql(app, "UPDATE settings SET value='2020-01-01' WHERE key='system_start'")
    x = next(x for x in _checks(app) if x["check"] == "journal")
    assert f"{(today - pf.replace(day=1)).days}日（{pf.month}/1、" in x["msg"]
    # 設定の画面で直せる
    post(client, "/settings", {"system_start": today.isoformat()})
    assert not any(x["check"] == "journal" for x in _checks(app))
    assert today.isoformat() in client.get("/settings").get_data(as_text=True)


def test_journal_lists_residents_living_there_on_that_day(client):
    post(client, "/m/homes/new", {"name": "ひまわり"})
    post(client, "/m/residents/new", {"name": "退居した人", "home_id": "1", "status": "退居", "move_in": "2026-01-01",
                                      "move_out": "2026-09-30"})
    post(client, "/m/residents/new", {"name": "これから入る人", "home_id": "1", "status": "入居中", "move_in": "2026-10-20"})
    page = client.get("/journal?date=2026-09-15&home_id=1").get_data(as_text=True)
    assert "退居した人" in page and "これから入る人" not in page
    page = client.get("/journal?date=2026-10-25&home_id=1").get_data(as_text=True)
    assert "退居した人" not in page and "これから入る人" in page


def test_admin_sets_initial_pin(client, app):
    use_punch(app)  # 事務所のPCで打刻するしかた（今は画面に出さない）
    post(client, "/m/staff/new", {"name": "新人 さん", "status": "在籍"})
    post(client, "/users", {"action": "add", "username": "newbie", "password": "temppass1", "role": "staff", "staff_id": "1",
                            "pin": "1234"})   # 推測されやすいPINはだめ
    with app.app_context():
        from ghms.db import get_db
        assert get_db().execute("SELECT COUNT(*) FROM users WHERE username='newbie'").fetchone()[0] == 0
    post(client, "/users", {"action": "add", "username": "newbie", "password": "temppass1", "role": "staff", "staff_id": "1",
                            "pin": "4826"})
    # 事務所のPCですぐ打刻できる（ログインの前でも）
    _register_device(client)
    _logout(client)
    page = client.get("/work/kiosk?staff_id=1").get_data(as_text=True)
    k = re.search(r'name="_k" value="([0-9a-f]+)"', page).group(1)
    client.post("/work/kiosk", data={"_k": k, "staff_id": "1", "action": "in", "temp": "36.4", "pin": "4826"})
    with app.app_context():
        from ghms.db import get_db
        db = get_db()
        assert db.execute("SELECT COUNT(*) FROM timecards").fetchone()[0] == 1
        uid = db.execute("SELECT id FROM users WHERE username='newbie'").fetchone()[0]
    # あとから管理者が入れ直す
    client.post("/login", data={"username": "admin", "password": "password123"})
    post(client, "/users", {"action": "set_pin", "id": str(uid), "pin": "1111"})
    post(client, "/users", {"action": "set_pin", "id": str(uid), "pin": "5937"})
    with app.app_context():
        from werkzeug.security import check_password_hash

        from ghms.db import get_db
        db = get_db()
        assert check_password_hash(db.execute("SELECT pin_hash FROM users WHERE id=?", (uid,)).fetchone()[0], "5937")
        assert db.execute("SELECT COUNT(*) FROM audit_log WHERE action='user_pin_set'").fetchone()[0] == 2


def test_rent_subsidy_not_prorated_and_yakin_checked(client):
    post(client, "/m/homes/new", {"name": "ひまわり"})
    post(client, "/m/residents/new", {"name": "途中入居", "home_id": "1", "move_in": "2026-10-17", "rent": "31000",
                                      "rent_subsidy": "10000"})
    post(client, "/billing/invoices", {"ym": "2026-10"})
    # 家賃は15日分 15,000、家賃助成は日割りしないで 10,000 → 5,000
    assert '<b class="rowtotal">5,000</b>' in client.get("/billing/invoices?ym=2026-10").get_data(as_text=True)
    post(client, "/payroll/settings", {"pay_yakin_checked": "1"})
    assert 'name="pay_yakin_checked" value="1" checked' in client.get("/payroll/settings").get_data(as_text=True)


# ---------------------------------------------------------------- バックアップ
def test_backup_auto_rotate_and_dest2(client, app, tmp_path):
    import os

    from ghms import backup as bk

    db_path = app.config["DATABASE"]
    st = bk.run_auto(db_path)
    assert st["last_at"] and not st.get("error")
    root = bk.local_root(db_path)
    assert bk._files(os.path.join(root, "daily")) and bk._files(os.path.join(root, "monthly"))
    assert bk.check_file(st["last_file"]) == (True, "")
    # 古いものは決まった数だけ残す
    for i in range(40):
        open(os.path.join(root, "daily", f"ghms_2000{i:04d}.sqlite3"), "wb").close()
    bk.prune(os.path.join(root, "daily"), bk.KEEP["daily"])
    names = bk._files(os.path.join(root, "daily"))
    assert len(names) == 30 and names[-1].startswith(f"ghms_{date.today():%Y%m%d}")
    # 2か所目：まだ設定していなければ「今日のやること」に出る
    assert "2か所目" in client.get("/").get_data(as_text=True)
    # データのフォルダの中は選べない
    post(client, "/backup", {"action": "dest2", "dest2": os.path.join(os.path.dirname(db_path), "x")})
    with app.app_context():
        from ghms.db import get_setting
        assert get_setting("backup_dir2", "") == ""
    usb = tmp_path.parent / (tmp_path.name + "_usb")
    usb.mkdir()
    r = post(client, "/backup", {"action": "dest2", "dest2": str(usb)})
    assert r.status_code == 302
    assert bk._files(os.path.join(bk.dest2_root(str(usb)), "daily"))
    assert "2か所目（USBメモリ" not in client.get("/").get_data(as_text=True)
    # USBメモリをはずしたとき（とどかない）は知らせる
    import shutil
    shutil.rmtree(usb)
    st = bk.run_auto(db_path)
    assert st["dest2"]["error"] and not st.get("error")
    page = client.get("/backup").get_data(as_text=True)
    assert "見つかりません" in page


def test_backup_restore(client, app):
    import os

    from ghms import backup as bk

    post(client, "/m/homes/new", {"name": "もどす前の住居"})
    assert post(client, "/backup", {"action": "now"}).status_code == 302
    post(client, "/m/homes/new", {"name": "あとで足した住居"})
    items = bk.list_backups(app.config["DATABASE"])
    man = [b for b in items if b["kind"] == "manual"][0]
    # 確認の言葉がなければしない・一覧にないものは選べない
    post(client, "/backup/restore", {"id": man["id"], "word": ""})
    post(client, "/backup/restore", {"id": "local:manual:../ghms.sqlite3", "word": "復元する"})
    assert "あとで足した住居" in client.get("/m/homes/").get_data(as_text=True)
    r = post(client, "/backup/restore", {"id": man["id"], "word": "復元する"})
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    # ログインし直し
    assert client.get("/").status_code == 302
    client.post("/login", data={"username": "admin", "password": "password123"})
    html = client.get("/m/homes/").get_data(as_text=True)
    assert "もどす前の住居" in html and "あとで足した住居" not in html
    assert bk._files(os.path.join(bk.local_root(app.config["DATABASE"]), "before_restore"))
    # GHMSのデータでないファイルは使えない
    import io
    data = {"_csrf": csrf(client), "word": "復元する", "file": (io.BytesIO(b"not a db"), "x.sqlite3")}
    client.post("/backup/restore", data=data, content_type="multipart/form-data")
    assert "もどす前の住居" in client.get("/m/homes/").get_data(as_text=True)


def test_backup_staff_forbidden(client, app):
    c = staff_client(client, app)
    for path in ["/backup", "/backup/download"]:
        assert c.get(path).status_code == 403, path
    assert client.get("/backup/download").data[:15] == b"SQLite format 3"


def test_backup_before_version_change(tmp_path, monkeypatch):
    import os
    import sqlite3

    from ghms import VERSION
    from ghms import backup as bk

    monkeypatch.setenv("GHMS_DATA_DIR", str(tmp_path))
    app = create_app({"TESTING": True})
    db_path = app.config["DATABASE"]
    assert bk.stored_version(db_path) == VERSION
    con = sqlite3.connect(db_path)
    con.execute("UPDATE settings SET value='0.9.0' WHERE key='app_version'")
    con.commit()
    con.close()
    create_app({"TESTING": True})
    files = bk._files(os.path.join(bk.local_root(db_path), "before_update"))
    assert len(files) == 1 and "0.9.0_to_" + VERSION in files[0]
    assert bk.stored_version(db_path) == VERSION
    # 古い版で開いても、使った版（新しいほう）は上書きしない
    con = sqlite3.connect(db_path)
    con.execute("UPDATE settings SET value='99.0.0' WHERE key='app_version'")
    con.commit()
    con.close()
    create_app({"TESTING": True})
    assert bk.stored_version(db_path) == "99.0.0"


def test_data_dir_from_config(tmp_path, monkeypatch):
    from ghms import runtime

    monkeypatch.delenv("GHMS_DATA_DIR", raising=False)
    monkeypatch.setenv("GHMS_HOME", str(tmp_path))
    assert runtime.data_dir() == str(tmp_path / "data")
    chosen = tmp_path / "選んだ場所"
    (tmp_path / "config.ini").write_text(f"[data]\ndir = {chosen}\n", encoding="cp932")
    assert runtime.data_dir() == str(chosen)
    (tmp_path / "config.ini").write_text("[data]\ndir = relative\\path\n", encoding="cp932")
    assert runtime.data_dir() == str(tmp_path / "data")


# ---------------------------------------------------------------- 勤務の確定（勤務表から。打刻のかわり）
def test_attend_confirm_from_shift(client, app):
    from datetime import timedelta

    for n in ("確定一郎", "確定花子", "応援三郎"):
        post(client, "/m/staff/new", {"name": n, "status": "在籍"})
    today = date.today()
    ts, ys = today.isoformat(), (today - timedelta(days=1)).isoformat()
    with app.app_context():
        from ghms.db import get_db
        db = get_db()
        ids = {r["name"]: r["id"] for r in db.execute("SELECT id, name FROM staff")}
        for sid, d, code in [(ids["確定一郎"], ts, "日"), (ids["確定花子"], ts, "日"), (ids["確定一郎"], ys, "夜")]:
            db.execute("INSERT INTO shifts (staff_id, date, code) VALUES (?,?,?)", (sid, d, code))
        db.commit()
    a, b, c = ids["確定一郎"], ids["確定花子"], ids["応援三郎"]
    home = client.get("/").get_data(as_text=True)
    assert "今日の勤務を確定する（2人）" in home and "勤務の確定をしていない日があります" in home
    # 打刻の画面は出さない（しくみは残してある）
    assert "出勤・退勤の打刻はこちら" not in app.test_client().get("/login").get_data(as_text=True)
    assert client.get("/work/clock").status_code == 302
    page = client.get(f"/work/confirm?date={ts}").get_data(as_text=True)
    assert "確定一郎" in page and "応援三郎" in page  # 応援三郎は「勤務表にない人」に出る

    def rows(sql, *args):
        with app.app_context():
            from ghms.db import get_db
            return get_db().execute(sql, args).fetchall()

    # 出勤：勤務表の時間（日勤 9:00〜18:00・勤務8時間→休憩60分）で出勤簿ができる
    post(client, "/work/confirm", {"date": ts, "staff_id": a, "action": "work", "start": "", "end": "", "break_min": "", "temp": "36.4"})
    card = rows("SELECT * FROM timecards WHERE staff_id=? AND date=?", a, ts)[0]
    assert (card["clock_in"], card["clock_out"], card["break_min"]) == ("09:00", "18:00", 60)
    assert rows("SELECT temp FROM health_checks WHERE staff_id=?", a)[0]["temp"] == 36.4
    # 時間を直す（残業）：同じ日のタイムカードは1つのまま
    post(client, "/work/confirm", {"date": ts, "staff_id": a, "action": "work", "start": "09:00", "end": "19:30", "break_min": "60"})
    cs = rows("SELECT * FROM timecards WHERE staff_id=? AND date=?", a, ts)
    assert len(cs) == 1 and cs[0]["clock_out"] == "19:30"
    # 休み：勤務表を「休」に直し、出勤簿は作らない。取り消すと元の勤務表にもどる
    post(client, "/work/confirm", {"date": ts, "staff_id": b, "action": "off:休"})
    assert rows("SELECT code FROM shifts WHERE staff_id=? AND date=?", b, ts)[0]["code"] == "休"
    assert not rows("SELECT * FROM timecards WHERE staff_id=?", b)
    assert "全員確定しました" in client.get("/").get_data(as_text=True)
    post(client, "/work/confirm", {"date": ts, "staff_id": b, "action": "undo"})
    assert rows("SELECT code FROM shifts WHERE staff_id=? AND date=?", b, ts)[0]["code"] == "日"
    # 有給（前の日の夜勤）→ 出勤に直す：夜勤 16:00〜10:00（勤務16時間→休憩120分）
    post(client, "/work/confirm", {"date": ys, "staff_id": a, "action": "off:有"})
    assert rows("SELECT code FROM shifts WHERE staff_id=? AND date=?", a, ys)[0]["code"] == "有"
    post(client, "/work/confirm", {"date": ys, "staff_id": a, "action": "work", "start": "", "end": "", "break_min": ""})
    assert rows("SELECT code FROM shifts WHERE staff_id=? AND date=?", a, ys)[0]["code"] == "夜"
    with app.app_context():
        from ghms.work import month_summary, work_minutes
        yk = rows("SELECT * FROM timecards WHERE staff_id=? AND date=?", a, ys)[0]
        assert yk["break_min"] == 120 and work_minutes(yk)["yakin"]
        first = (today - timedelta(days=1)).replace(day=1)
        assert month_summary(a, first, today)["yakin"] == 1
    # まとめて確定
    post(client, "/work/confirm", {"date": ts, "action": "all"})
    assert rows("SELECT * FROM attend_days WHERE staff_id=? AND date=?", b, ts)[0]["status"] == "work"
    # 勤務表にない人を足す → 勤務表にも入る。取り消すと勤務表からも消える
    post(client, "/work/confirm", {"date": ts, "staff_id": c, "action": "add", "code": "早", "start": "", "end": ""})
    assert rows("SELECT code FROM shifts WHERE staff_id=? AND date=?", c, ts)[0]["code"] == "早"
    assert rows("SELECT clock_in FROM timecards WHERE staff_id=?", c)[0]["clock_in"] == "06:00"
    post(client, "/work/confirm", {"date": ts, "staff_id": c, "action": "undo"})
    assert not rows("SELECT * FROM shifts WHERE staff_id=? AND date=?", c, ts)
    assert not rows("SELECT * FROM timecards WHERE staff_id=?", c)
    home = client.get("/").get_data(as_text=True)
    assert "勤務の確定をしていない日" not in home
    # まだ来ていない日は確定できない（今日にする）
    future = (today + timedelta(days=3)).isoformat()
    post(client, "/work/confirm", {"date": future, "staff_id": c, "action": "add", "code": "日"})
    assert not rows("SELECT * FROM attend_days WHERE date=?", future)
    # 職員は確定できない
    st = staff_client(client, app)
    assert st.get("/work/confirm").status_code == 403


def test_staff_records_own_temperature(client, app):
    post(client, "/m/staff/new", {"name": "体温太郎", "status": "在籍"})
    st = staff_client(client, app)
    with app.app_context():
        from ghms.db import get_db
        db = get_db()
        sid = db.execute("SELECT id FROM staff WHERE name='体温太郎'").fetchone()[0]
        db.execute("UPDATE users SET staff_id=? WHERE username='worker'", (sid,))
        db.execute("INSERT INTO shifts (staff_id, date, code) VALUES (?,?,?)", (sid, date.today().isoformat(), "日"))
        db.commit()
    page = st.get("/").get_data(as_text=True)
    assert "体温を記録する" in page and "出勤する" not in page
    post(st, "/work/my-health", {"temp": "38.0"})
    with app.app_context():
        from ghms.db import get_db
        assert get_db().execute("SELECT temp FROM health_checks WHERE staff_id=?", (sid,)).fetchone()[0] == 38.0
    assert "38.0" in client.get("/work/health").get_data(as_text=True)
