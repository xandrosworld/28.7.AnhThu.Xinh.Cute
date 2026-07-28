"""Stable HTTP/DOM checks that do not require browser downloads.

Cross-browser and camera behaviour remains a documented manual acceptance
check.  These tests catch missing routes, broken asset links and inaccessible
shell regressions without introducing an external Playwright dependency.
"""

from html.parser import HTMLParser


class PageContractParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.lang = ""
        self.has_viewport = False
        self.has_main = False
        self.has_skip_link = False
        self.stylesheets = []
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "html":
            self.lang = values.get("lang", "")
        elif tag == "meta" and values.get("name") == "viewport":
            self.has_viewport = True
        elif tag == "main" and values.get("id") == "main-content":
            self.has_main = True
        elif tag == "a" and values.get("href") == "#main-content":
            self.has_skip_link = True
        elif tag == "link" and values.get("rel") == "stylesheet":
            self.stylesheets.append(values.get("href", ""))
        elif tag == "script" and values.get("src"):
            self.scripts.append(values["src"])


def _assert_page_contract(response, client):
    assert response.status_code == 200
    assert response.content_type.startswith("text/html")
    parser = PageContractParser()
    parser.feed(response.get_data(as_text=True))
    assert parser.lang == "vi"
    assert parser.has_viewport
    assert parser.has_main
    assert parser.has_skip_link
    assert parser.stylesheets
    assert parser.scripts
    for path in parser.stylesheets + parser.scripts:
        assert path.startswith("/static/")
        asset = client.get(path)
        assert asset.status_code == 200, path
        assert asset.data, path


def test_authenticated_route_and_local_asset_matrix(client, admin_login):
    for path in (
        "/dashboard",
        "/inventory",
        "/products",
        "/categories",
        "/customers",
        "/suppliers",
        "/warehouses",
        "/inbound-receipts",
        "/outbound-receipts",
        "/stocktakes",
        "/reports",
        "/users",
        "/audit-logs",
        "/profile",
    ):
        _assert_page_contract(client.get(path), client)


def test_api_security_headers_and_success_envelope(client, admin_login):
    response = client.get("/api/inventory")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]

    payload = response.get_json()
    assert "data" in payload
    assert "meta" in payload


def test_inventory_detail_exposes_and_renders_complete_movement_history(
    client, admin_login
):
    _, csrf = admin_login
    before = client.get("/api/inventory/1").get_json()["item"]["quantity"]
    adjusted = client.post(
        "/api/inventory/1/adjustments",
        json={
            "new_quantity": before + 1,
            "reason": "Kiểm kê định kỳ",
            "note": "Tạo lịch sử để kiểm tra giao diện chi tiết",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert adjusted.status_code == 200

    response = client.get("/api/inventory/1")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["movements"]
    assert {
        "movement_type",
        "reference_code",
        "quantity_change",
        "balance_after",
        "created_at",
    } <= set(payload["movements"][0])

    script = client.get("/static/app.js").get_data(as_text=True)
    for contract in (
        "Array.isArray(data.movements)",
        "Array.isArray(data.adjustments)",
        "entry.movement_type",
        "entry.reference_code",
        "entry.quantity_change",
        "entry.balance_after",
        "entry.created_at",
        'aria-labelledby="inventory-history-title"',
        'role="status"',
    ):
        assert contract in script


def test_unknown_page_and_api_have_distinct_contracts(client, admin_login):
    page = client.get("/page-does-not-exist")
    assert page.status_code == 404
    assert page.content_type.startswith("text/html")

    api = client.get("/api/does-not-exist")
    assert api.status_code == 404
    payload = api.get_json()
    assert payload["error"] == {
        "code": "not_found",
        "message": "Không tìm thấy tài nguyên.",
        "fields": {},
    }
