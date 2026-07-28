from conftest import login


def headers(csrf):
    return {"X-CSRF-Token": csrf}


def test_extended_pages_render_for_authenticated_user(client, admin_login):
    for path in (
        "/products",
        "/customers",
        "/suppliers",
        "/warehouses",
        "/inbound-receipts",
        "/outbound-receipts",
        "/stocktakes",
        "/reports",
    ):
        response = client.get(path)
        assert response.status_code == 200
        assert b"DNP WMS" in response.data


def test_inbound_confirmation_is_atomic_and_idempotent(client, manager_login):
    _, csrf = manager_login
    before = client.get("/api/inventory/1").get_json()["item"]["quantity"]
    created = client.post(
        "/api/inbound-receipts",
        json={
            "code": "PN-TEST-001",
            "partner_id": 1,
            "warehouse_id": 1,
            "status": "pending",
            "container_no": "CONT-01",
            "seal_no": "SEAL-01",
            "items": [
                {
                    "inventory_id": 1,
                    "quantity": 15,
                    "pallet_id": "PLT-TEST-001",
                    "barcode": "893TEST001",
                }
            ],
        },
        headers=headers(csrf),
    )
    assert created.status_code == 201
    receipt_id = created.get_json()["id"]

    confirmed = client.post(
        f"/api/inbound-receipts/{receipt_id}/confirm", headers=headers(csrf)
    )
    assert confirmed.status_code == 200
    assert client.get("/api/inventory/1").get_json()["item"]["quantity"] == before + 15

    repeated = client.post(
        f"/api/inbound-receipts/{receipt_id}/confirm", headers=headers(csrf)
    )
    assert repeated.status_code == 200
    assert repeated.get_json()["already_completed"] is True
    assert client.get("/api/inventory/1").get_json()["item"]["quantity"] == before + 15


def test_outbound_requires_contract_email_and_available_stock(client, manager_login):
    _, csrf = manager_login
    base_payload = {
        "code": "PX-TEST-001",
        "partner_id": 1,
        "warehouse_id": 1,
        "status": "pending",
        "items": [{"inventory_id": 1, "quantity": 10, "pallet_id": "PLT-OUT-01"}],
    }
    invalid_email = client.post(
        "/api/outbound-receipts",
        json={**base_payload, "request_email": "nguoi-la@example.com"},
        headers=headers(csrf),
    )
    assert invalid_email.status_code == 422
    assert "request_email" in invalid_email.get_json()["errors"]

    insufficient = client.post(
        "/api/outbound-receipts",
        json={
            **base_payload,
            "request_email": "kho@minhphat.vn",
            "items": [{"inventory_id": 1, "quantity": 999999}],
        },
        headers=headers(csrf),
    )
    assert insufficient.status_code == 422
    assert "items" in insufficient.get_json()["errors"]


def test_outbound_confirmation_decrements_stock_and_feeds_reports(client, manager_login):
    _, csrf = manager_login
    before = client.get("/api/inventory/1").get_json()["item"]["quantity"]
    created = client.post(
        "/api/outbound-receipts",
        json={
            "code": "PX-TEST-SUCCESS",
            "partner_id": 1,
            "warehouse_id": 1,
            "request_email": "dieuphoi@minhphat.vn",
            "status": "pending",
            "items": [
                {
                    "inventory_id": 1,
                    "quantity": 7,
                    "pallet_id": "PLT-OUT-SUCCESS",
                }
            ],
        },
        headers=headers(csrf),
    )
    assert created.status_code == 201
    confirmed = client.post(
        f"/api/outbound-receipts/{created.get_json()['id']}/confirm",
        headers=headers(csrf),
    )
    assert confirmed.status_code == 200
    assert client.get("/api/inventory/1").get_json()["item"]["quantity"] == before - 7

    report = client.get("/api/reports/summary")
    assert report.status_code == 200
    assert any(
        item["reference_code"] == "PX-TEST-SUCCESS"
        for item in report.get_json()["movements"]
    )
    exported = client.get("/api/reports/export.csv")
    assert exported.status_code == 200
    assert "text/csv" in exported.content_type


def test_stocktake_requires_reason_then_updates_inventory(client, manager_login):
    _, csrf = manager_login
    current = client.get("/api/inventory/1").get_json()["item"]["quantity"]
    invalid = client.post(
        "/api/stocktakes",
        json={
            "code": "KK-TEST-INVALID",
            "warehouse_id": 1,
            "items": [
                {
                    "inventory_id": 1,
                    "counted_quantity": current + 2,
                    "reason": "",
                }
            ],
        },
        headers=headers(csrf),
    )
    assert invalid.status_code == 422

    created = client.post(
        "/api/stocktakes",
        json={
            "code": "KK-TEST-001",
            "warehouse_id": 1,
            "items": [
                {
                    "inventory_id": 1,
                    "counted_quantity": current + 2,
                    "reason": "Lệch khi kiểm đếm cuối ca",
                }
            ],
        },
        headers=headers(csrf),
    )
    assert created.status_code == 201
    confirmed = client.post(
        f"/api/stocktakes/{created.get_json()['id']}/confirm",
        headers=headers(csrf),
    )
    assert confirmed.status_code == 200
    assert client.get("/api/inventory/1").get_json()["item"]["quantity"] == current + 2


def test_staff_can_view_but_cannot_create_receipt(client):
    _, csrf = login(client, "nhanvien", "NV@123456")
    assert client.get("/api/inbound-receipts").status_code == 200
    response = client.post(
        "/api/inbound-receipts",
        json={},
        headers=headers(csrf),
    )
    assert response.status_code == 403


def test_master_data_endpoints_create_validate_and_filter(client):
    _, csrf = login(client, "cs", "Cs@123456")
    auth = headers(csrf)

    lookups = client.get("/api/operations/lookups")
    assert lookups.status_code == 200
    assert {"products", "warehouses", "customers", "suppliers"} <= set(
        lookups.get_json()
    )

    invalid_product = client.post("/api/products", json={}, headers=auth)
    assert invalid_product.status_code == 422
    assert {"sku", "name", "unit", "category_id", "warehouse_id"} <= set(
        invalid_product.get_json()["errors"]
    )

    product = client.post(
        "/api/products",
        json={
            "sku": "SKU-NEW-01",
            "barcode": "8930000999001",
            "name": "Hàng kiểm thử giao diện",
            "category_id": 1,
            "warehouse_id": 1,
            "unit": "Thùng",
            "min_quantity": 5,
            "location": "QA-01",
            "status": "active",
        },
        headers=auth,
    )
    assert product.status_code == 201
    filtered = client.get("/api/products?search=8930000999001&warehouse_id=1")
    assert filtered.status_code == 200
    assert [item["sku"] for item in filtered.get_json()["items"]] == ["SKU-NEW-01"]
    assert client.post(
        "/api/products",
        json={
            "sku": "SKU-NEW-01",
            "name": "Trùng mã",
            "category_id": 1,
            "warehouse_id": 1,
            "unit": "Thùng",
        },
        headers=auth,
    ).status_code == 409

    assert client.post("/api/customers", json={}, headers=auth).status_code == 422
    customer = client.post(
        "/api/customers",
        json={
            "code": "KH-QA",
            "name": "Khách hàng QA",
            "email": "qa@example.com",
            "contract_emails": "qa@example.com,warehouse@example.com",
            "status": "active",
        },
        headers=auth,
    )
    assert customer.status_code == 201
    assert client.get("/api/customers?search=KH-QA").get_json()["items"][0][
        "name"
    ] == "Khách hàng QA"
    assert client.post(
        "/api/customers",
        json={
            "code": "KH-QA",
            "name": "Tên trùng",
            "contract_emails": "qa@example.com",
        },
        headers=auth,
    ).status_code == 409

    supplier = client.post(
        "/api/suppliers",
        json={
            "code": "NCC-QA",
            "name": "Nhà cung cấp QA",
            "email": "ncc@example.com",
            "address": "TP.HCM",
            "status": "active",
        },
        headers=auth,
    )
    assert supplier.status_code == 201
    assert client.get("/api/suppliers?search=NCC-QA").status_code == 200
    warehouses = client.get("/api/warehouses").get_json()["items"]
    assert warehouses and "total_quantity" in warehouses[0]


def test_receipt_detail_not_found_and_report_filters(client, manager_login):
    assert client.get("/api/inbound-receipts/1").status_code == 200
    assert client.get("/api/outbound-receipts/2").status_code == 200
    assert client.get("/api/inbound-receipts/99999").status_code == 404
    assert client.get("/api/outbound-receipts/1").status_code == 404

    report = client.get(
        "/api/reports/summary?from=2026-01-01&to=2026-12-31&warehouse_id=1"
    )
    assert report.status_code == 200
    payload = report.get_json()
    assert {"summary", "receipt_counts", "movement_totals", "movements", "alerts"} <= set(
        payload
    )


def test_inspection_stock_check_picking_and_cancel_contracts(client, manager_login):
    _, csrf = manager_login
    inspected = client.post(
        "/api/inbound-receipts/1/inspect",
        json={
            "items": [
                {
                    "id": 1,
                    "accepted_quantity": 118,
                    "issue_note": "Thiếu 2 cây khi kiểm nhận",
                }
            ]
        },
        headers=headers(csrf),
    )
    assert inspected.status_code == 200
    assert client.get("/api/inbound-receipts/1").get_json()["item"]["items"][0][
        "accepted_quantity"
    ] == 118

    stock = client.get("/api/outbound-receipts/2/check-stock")
    assert stock.status_code == 200
    assert stock.get_json()["sufficient"] is True
    picking = client.get("/api/outbound-receipts/2/picking-list")
    assert picking.status_code == 200
    assert picking.get_json()["strategy"].startswith("FEFO")

    cancelled = client.post(
        "/api/outbound-receipts/2/cancel", headers=headers(csrf)
    )
    assert cancelled.status_code == 200
    repeated = client.post(
        "/api/outbound-receipts/2/cancel", headers=headers(csrf)
    )
    assert repeated.status_code == 200
    assert repeated.get_json()["already_cancelled"] is True
    assert client.post(
        "/api/outbound-receipts/2/confirm", headers=headers(csrf)
    ).status_code == 409


def test_warehouse_role_can_confirm_but_cs_cannot(client):
    _, csrf = login(client, "warehouse", "Kho@12345")
    response = client.post(
        "/api/inbound-receipts/1/confirm", headers=headers(csrf)
    )
    assert response.status_code == 200

    client.post("/api/auth/logout", headers=headers(csrf))
    _, csrf = login(client, "cs", "Cs@123456")
    assert client.post(
        "/api/stocktakes",
        json={},
        headers=headers(csrf),
    ).status_code == 403


def test_stocktake_confirmation_is_idempotent(client, manager_login):
    _, csrf = manager_login
    created = client.post(
        "/api/stocktakes",
        json={
            "code": "KK-IDEMPOTENT",
            "warehouse_id": 1,
            "items": [
                {
                    "inventory_id": 1,
                    "counted_quantity": 1250,
                    "reason": "",
                }
            ],
        },
        headers=headers(csrf),
    )
    assert created.status_code == 201
    stocktake_id = created.get_json()["id"]
    assert client.post(
        f"/api/stocktakes/{stocktake_id}/confirm", headers=headers(csrf)
    ).status_code == 200
    assert client.post(
        f"/api/stocktakes/{stocktake_id}/confirm", headers=headers(csrf)
    ).status_code == 409
    assert client.post(
        "/api/stocktakes",
        json={
            "code": "KK-IDEMPOTENT",
            "warehouse_id": 1,
            "items": [
                {
                    "inventory_id": 1,
                    "counted_quantity": 1250,
                    "reason": "",
                }
            ],
        },
        headers=headers(csrf),
    ).status_code == 409


def test_receipt_rejects_malformed_cross_warehouse_and_aggregate_payloads(
    client, manager_login
):
    _, csrf = manager_login
    auth_headers = headers(csrf)

    assert client.post(
        "/api/inbound-receipts",
        json={"code": "PN-BAD-LINE", "partner_id": 1, "warehouse_id": 1, "items": ["bad"]},
        headers=auth_headers,
    ).status_code == 422
    assert client.post(
        "/api/inbound-receipts",
        json={
            "code": "PN-CROSS-WAREHOUSE",
            "partner_id": 1,
            "warehouse_id": 1,
            "items": [{"inventory_id": 2, "quantity": 1}],
        },
        headers=auth_headers,
    ).status_code == 422
    assert client.post(
        "/api/inbound-receipts",
        json={
            "code": "PN-ILLEGAL-STATE",
            "partner_id": 1,
            "warehouse_id": 1,
            "status": "completed",
            "items": [{"inventory_id": 1, "quantity": 1}],
        },
        headers=auth_headers,
    ).status_code == 422
    assert client.post(
        "/api/outbound-receipts",
        json={
            "code": "PX-AGGREGATE",
            "partner_id": 1,
            "warehouse_id": 1,
            "request_email": "kho@minhphat.vn",
            "items": [
                {"inventory_id": 1, "quantity": 800, "pallet_id": "AGG-1"},
                {"inventory_id": 1, "quantity": 800, "pallet_id": "AGG-2"},
            ],
        },
        headers=auth_headers,
    ).status_code == 422
    assert client.post(
        "/api/customers",
        json={
            "code": "KH-BAD-MAIL",
            "name": "Khách sai email",
            "contract_emails": "not-an-email",
        },
        headers=auth_headers,
    ).status_code == 422


def test_inspection_duplicate_ids_and_stale_stocktake_are_rejected(
    client, manager_login, db
):
    _, csrf = manager_login
    auth_headers = headers(csrf)
    cursor = db.execute(
        """
        INSERT INTO receipt_items
            (receipt_id, inventory_id, quantity, accepted_quantity, pallet_id)
        VALUES (1, 5, 5, 5, 'INSPECT-SECOND')
        """
    )
    second_line_id = cursor.lastrowid
    db.commit()

    response = client.post(
        "/api/inbound-receipts/1/inspect",
        json={
            "items": [
                {"id": 1, "accepted_quantity": 100},
                {"id": 1, "accepted_quantity": 99},
            ]
        },
        headers=auth_headers,
    )
    assert response.status_code == 422
    assert (
        db.execute(
            "SELECT accepted_quantity FROM receipt_items WHERE id = ?",
            (second_line_id,),
        ).fetchone()[0]
        == 5
    )

    created = client.post(
        "/api/stocktakes",
        json={
            "code": "KK-STALE-SNAPSHOT",
            "warehouse_id": 1,
            "items": [
                {
                    "inventory_id": 1,
                    "counted_quantity": 1200,
                    "reason": "Đếm thực tế",
                }
            ],
        },
        headers=auth_headers,
    )
    assert created.status_code == 201
    db.execute("UPDATE inventory SET quantity = 1300 WHERE id = 1")
    db.commit()

    response = client.post(
        f"/api/stocktakes/{created.get_json()['id']}/confirm",
        headers=auth_headers,
    )
    assert response.status_code == 409
    assert db.execute("SELECT quantity FROM inventory WHERE id = 1").fetchone()[0] == 1300


def test_outbound_confirmation_rolls_back_all_lines_on_late_shortage(
    client, manager_login, db
):
    _, csrf = manager_login
    auth_headers = headers(csrf)
    created = client.post(
        "/api/outbound-receipts",
        json={
            "code": "PX-ROLLBACK-LATE",
            "partner_id": 1,
            "warehouse_id": 1,
            "request_email": "kho@minhphat.vn",
            "items": [
                {"inventory_id": 1, "quantity": 10, "pallet_id": "ROLLBACK-1"},
                {"inventory_id": 5, "quantity": 10, "pallet_id": "ROLLBACK-2"},
            ],
        },
        headers=auth_headers,
    )
    assert created.status_code == 201
    before = db.execute("SELECT quantity FROM inventory WHERE id = 1").fetchone()[0]
    db.execute("UPDATE inventory SET quantity = 0 WHERE id = 5")
    db.commit()

    response = client.post(
        f"/api/outbound-receipts/{created.get_json()['id']}/confirm",
        headers=auth_headers,
    )
    assert response.status_code == 409
    assert db.execute("SELECT quantity FROM inventory WHERE id = 1").fetchone()[0] == before
    assert (
        db.execute(
            "SELECT COUNT(*) FROM stock_movements WHERE reference_code = 'PX-ROLLBACK-LATE'"
        ).fetchone()[0]
        == 0
    )


def test_invalid_json_referenced_user_and_unknown_api_contract(client, admin_login):
    _, csrf = admin_login
    auth_headers = headers(csrf)

    for method, path in (
        ("post", "/api/products"),
        ("post", "/api/customers"),
        ("post", "/api/inbound-receipts"),
        ("post", "/api/stocktakes"),
        ("put", "/api/profile"),
    ):
        response = getattr(client, method)(path, json="bad", headers=auth_headers)
        assert response.status_code == 422
        assert response.is_json
        assert response.get_json()["error"]["code"] == "validation_error"

    client.post("/api/auth/logout", headers=auth_headers)
    _, csrf = login(client, "cs", "Cs@123456")
    response = client.post(
        "/api/inbound-receipts",
        json={
            "code": "PN-CS-HISTORY",
            "partner_id": 1,
            "warehouse_id": 1,
            "items": [{"inventory_id": 1, "quantity": 1, "pallet_id": "CS-HISTORY"}],
        },
        headers=headers(csrf),
    )
    assert response.status_code == 201
    client.post("/api/auth/logout", headers=headers(csrf))
    _, csrf = login(client, "admin", "Admin@123")
    assert client.delete("/api/users/5", headers=headers(csrf)).status_code == 409

    response = client.get("/api/route-does-not-exist")
    assert response.status_code == 404
    assert response.is_json
    assert response.get_json()["error"]["code"] == "not_found"
