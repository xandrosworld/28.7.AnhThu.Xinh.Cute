from conftest import login


def test_login_success_and_me(client):
    response, csrf = login(client)
    assert response.status_code == 200
    assert csrf
    assert response.get_json()["user"]["role"] == "admin"

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.get_json()["user"]["username"] == "admin"


def test_login_validation_and_wrong_password(client):
    missing = client.post("/api/auth/login", json={})
    assert missing.status_code == 422
    assert set(missing.get_json()["errors"]) == {"username", "password"}

    wrong = client.post(
        "/api/auth/login", json={"username": "admin", "password": "wrong"}
    )
    assert wrong.status_code == 401


def test_locked_user_cannot_login(client):
    response, _ = login(client, "khoatam", "Locked@123")
    assert response.status_code == 403


def test_protected_api_and_csrf(client, admin_login):
    anonymous = client.application.test_client().get("/api/inventory")
    assert anonymous.status_code == 401

    no_csrf = client.post(
        "/api/categories",
        json={"code": "NEW", "name": "Danh mục mới", "status": "active"},
    )
    assert no_csrf.status_code == 403


def test_logout_clears_session(client, admin_login):
    _, csrf = admin_login
    response = client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    assert client.get("/api/auth/me").status_code == 401


def test_page_redirect_and_admin_page_authorization(client, staff_login):
    anonymous = client.application.test_client()
    assert anonymous.get("/inventory").status_code == 302
    assert client.get("/users").status_code == 403
