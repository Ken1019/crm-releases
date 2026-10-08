import re

import pytest

from ghms import create_app
from ghms.entities import ENTITIES


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("GHMS_DATA_DIR", str(tmp_path))
    app = create_app({"TESTING": True})
    return app


def csrf(client, path="/"):
    html = client.get(path).get_data(as_text=True)
    m = re.search(r'name="_csrf" value="([0-9a-f]+)"', html)
    return m.group(1)


@pytest.fixture()
def client(app):
    c = app.test_client()
    r = c.post("/setup", data={"office_name": "テストホーム", "username": "admin", "password": "password123"})
    assert r.status_code == 302
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
    post(client, "/users", {"action": "add", "username": "worker", "password": "password123", "role": "staff"})
    c = app.test_client()
    c.post("/login", data={"username": "worker", "password": "password123"})
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

    post(client, "/users", {"action": "add", "username": "worker", "password": "password123", "role": "staff"})
    c = app.test_client()
    c.post("/login", data={"username": "worker", "password": "password123"})
    assert c.get("/do/staff").status_code == 404
    home = c.get("/").get_data(as_text=True)
    assert "職員のこと" not in home and "処遇改善の計画と配分を見る" not in home
    assert "今月の加算の要件をチェックする" in home


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
    assert "74,700円" in html
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
    assert "30,000円" in client.get("/billing/invoices?ym=2026-10").get_data(as_text=True)


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
