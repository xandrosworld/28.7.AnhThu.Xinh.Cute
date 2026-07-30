from conftest import login


def test_admin_settings_page_has_accessible_role_and_unit_controls(
    client, admin_login
):
    response = client.get("/settings")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    for marker in (
        'data-page="settings"',
        'id="role-body"',
        'id="unit-body"',
        'id="role-form"',
        'id="unit-form"',
        'aria-labelledby="role-modal-title"',
        'aria-labelledby="unit-modal-title"',
        'aria-live="polite"',
        "Cấu hình hệ thống",
    ):
        assert marker in html


def test_settings_page_is_admin_only_and_link_is_not_shown_to_staff(client):
    anonymous = client.get("/settings")
    assert anonymous.status_code == 302
    assert anonymous.headers["Location"].endswith(("/", "/index.html"))

    login(client, "nhanvien", "NV@123456")
    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    assert 'href="/settings"' not in dashboard.get_data(as_text=True)

    forbidden = client.get("/settings")
    assert forbidden.status_code == 403
    assert "Cấu hình hệ thống" not in forbidden.get_data(as_text=True)


def test_settings_link_is_hidden_from_admin_sidebar(client, admin_login):
    dashboard = client.get("/dashboard")

    assert dashboard.status_code == 200
    assert 'href="/settings"' not in dashboard.get_data(as_text=True)


def test_settings_frontend_uses_public_role_and_unit_mutation_contract(client):
    script = client.get("/static/app.js")

    assert script.status_code == 200
    source = script.get_data(as_text=True)
    for contract in (
        'api("/api/roles")',
        'api("/api/units")',
        "`/api/roles/${item.id}`",
        "`/api/units/${item.id}`",
        'method: "PUT"',
        'method: id ? "PUT" : "POST"',
        'if (page === "settings") await initSettings()',
    ):
        assert contract in source
