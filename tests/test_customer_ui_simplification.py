from conftest import login


def headers(csrf):
    return {"X-CSRF-Token": csrf}


def test_sidebar_and_profile_match_customer_scope(client, admin_login):
    dashboard_html = client.get("/dashboard").get_data(as_text=True)
    for hidden_link in (
        "/customers",
        "/suppliers",
        "/warehouses",
        "/settings",
        "/audit-logs",
    ):
        assert f'href="{hidden_link}"' not in dashboard_html

    profile_html = client.get("/profile").get_data(as_text=True)
    assert 'id="profile-form"' in profile_html
    assert 'id="password-form"' not in profile_html
    assert "Đổi mật khẩu" not in profile_html


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
