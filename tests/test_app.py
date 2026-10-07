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
    for path in ["/m/staff/", "/m/shogu_plans/", "/shogu/", "/career", "/users", "/backup"]:
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
