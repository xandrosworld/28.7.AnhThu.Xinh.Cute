import sqlite3

import pytest

from conftest import login


def headers(csrf):
    return {"X-CSRF-Token": csrf}


def test_sidebar_and_profile_match_customer_scope(client, admin_login):
    dashboard_html = client.get("/dashboard").get_data(as_text=True)
    for hidden_link in (
        "/customers",
        "/suppliers",
        "/warehouses",
        "/stocktakes",
        "/settings",
        "/audit-logs",
    ):
        assert f'href="{hidden_link}"' not in dashboard_html

    profile_html = client.get("/profile").get_data(as_text=True)
    assert 'id="profile-form"' in profile_html
    assert 'id="password-form"' not in profile_html
    assert "Đổi mật khẩu" not in profile_html


@pytest.mark.parametrize(
    ("username", "password"),
    (
        ("admin", "Admin@123"),
        ("cs", "Cs@123456"),
        ("warehouse", "Kho@12345"),
        ("quanlykho", "Kho@12345"),
        ("nhanvien", "NV@123456"),
    ),
)
def test_latest_customer_scope_is_consistent_for_every_active_account(
    client, username, password
):
    response, _ = login(client, username, password)
    assert response.status_code == 200

    dashboard_html = client.get("/dashboard").get_data(as_text=True)
    assert 'href="/stocktakes"' not in dashboard_html
    assert ">Kiểm kê<" not in dashboard_html

    report_html = client.get("/reports").get_data(as_text=True)
    assert "Luân chuyển kho" not in report_html
    assert 'id="movement-chart"' not in report_html

    inventory_html = client.get("/inventory").get_data(as_text=True)
    assert "KIỂM KÊ" not in inventory_html
    assert "Kiểm kê định kỳ" not in inventory_html
    assert "CẬP NHẬT" in inventory_html
    assert "Cập nhật tồn kho" in inventory_html

    products_html = client.get("/products").get_data(as_text=True)
    assert "Chưa có barcode" not in products_html
    assert 'name="barcode"' not in products_html
    assert 'id="product-barcode"' not in products_html
    assert "Barcode" not in products_html


def test_dynamic_ui_matches_latest_customer_wording(client, admin_login):
    script = client.get("/static/app.js").get_data(as_text=True)
    assert ">Cập nhật</button>" in script
    assert "HÀNG HÓA ĐANG CẬP NHẬT" in script
    assert ">Kiểm kê</button>" not in script
    assert "HÀNG HÓA ĐANG KIỂM KÊ" not in script
    assert "Chưa có barcode" not in script
    assert "movement-chart" not in script


def test_inventory_accepts_updated_reason_wording(client, admin_login):
    _, csrf = admin_login
    current = client.get("/api/inventory/1").get_json()["item"]["quantity"]
    response = client.post(
        "/api/inventory/1/adjustments",
        json={
            "new_quantity": current + 1,
            "reason": "Cập nhật định kỳ",
            "note": "",
        },
        headers=headers(csrf),
    )
    assert response.status_code == 200


def test_operational_forms_do_not_ask_for_warehouse_or_location(client, admin_login):
    for route in (
        "/inventory",
        "/products",
        "/inbound-receipts",
        "/outbound-receipts",
        "/stocktakes",
        "/reports",
    ):
        response = client.get(route)
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert 'name="warehouse_id"' not in html
        assert ">Vị trí<" not in html
        assert "Kho / vị trí" not in html


def test_seed_data_uses_only_da_nang_and_has_no_locations(db):
    warehouses = db.execute("SELECT code,name FROM warehouses ORDER BY id").fetchall()
    assert [dict(row) for row in warehouses] == [
        {"code": "DN", "name": "Kho Đà Nẵng"}
    ]
    assert db.execute("SELECT COUNT(*) FROM inventory WHERE location<>''").fetchone()[0] == 0

    for table_name in ("inventory", "inventory_lots", "receipts"):
        other_warehouses = db.execute(
            f"""SELECT COUNT(*) FROM {table_name} item
                JOIN warehouses warehouse ON warehouse.id=item.warehouse_id
                WHERE warehouse.code<>'DN'"""
        ).fetchone()[0]
        assert other_warehouses == 0


def test_forms_can_create_records_without_sending_warehouse(client):
    _, csrf = login(client, "admin", "Admin@123")
    auth = headers(csrf)

    product = client.post(
        "/api/products",
        json={
            "sku": "SKU-DN-ONLY",
            "name": "Hàng hóa một kho",
            "category_id": 1,
            "unit": "Cây",
            "location": "SHOULD-NOT-BE-SAVED",
        },
        headers=auth,
    )
    assert product.status_code == 201
    stored_product = client.get("/api/products?search=SKU-DN-ONLY").get_json()["items"][0]
    assert stored_product["warehouse_name"] == "Kho Đà Nẵng"
    assert stored_product["location"] == ""

    receipt = client.post(
        "/api/inbound-receipts",
        json={
            "code": "PN-DN-ONLY",
            "partner_id": 1,
            "items": [{"inventory_id": stored_product["id"], "quantity": 5}],
        },
        headers=auth,
    )
    assert receipt.status_code == 201

    stocktake = client.post(
        "/api/stocktakes",
        json={
            "code": "KK-DN-ONLY",
            "items": [
                {
                    "inventory_id": stored_product["id"],
                    "counted_quantity": 0,
                }
            ],
        },
        headers=auth,
    )
    assert stocktake.status_code == 201


def test_performance_seed_respects_single_warehouse(tmp_path):
    from app import create_app
    from app.db import init_database
    from app.extensions import db as orm
    from scripts.benchmark import seed_large_dataset

    database_path = tmp_path / "benchmark-single-warehouse.sqlite"
    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "benchmark-regression-test",
            "DATABASE": str(database_path),
            "AUTO_INIT_DB": False,
        }
    )
    with app.app_context():
        init_database()
        orm.session.remove()
        orm.engine.dispose()

    counts, _ = seed_large_dataset(
        database_path, products=2, lots=3, movements=3
    )
    assert counts["benchmark_products"] == 2

    with sqlite3.connect(database_path) as connection:
        warehouse_ids = connection.execute(
            "SELECT DISTINCT warehouse_id FROM inventory"
        ).fetchall()
        assert warehouse_ids == [(1,)]
        assert connection.execute(
            "SELECT COUNT(*) FROM inventory WHERE location<>''"
        ).fetchone()[0] == 0
