def login(client, username, password):
    response = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    if response.status_code == 200:
        client.environ_base["HTTP_X_CSRF_TOKEN"] = response.json["csrf_token"]
    return response


def test_auth_csrf_and_role_guards(app, anonymous_client):
    assert anonymous_client.get("/api/products").status_code == 401
    assert anonymous_client.post("/api/outbound-orders", json={}).status_code == 401
    assert login(anonymous_client, "cs", "wrong").status_code == 401
    assert login(anonymous_client, "cs", "CS@12345").status_code == 200
    anonymous_client.environ_base.pop("HTTP_X_CSRF_TOKEN")
    assert anonymous_client.post(
        "/api/customers", json={"code": "X", "name": "X", "contract_emails": ["x@y.vn"]}
    ).status_code == 403
    token = anonymous_client.get("/api/auth/me").json["csrf_token"]
    anonymous_client.environ_base["HTTP_X_CSRF_TOKEN"] = token
    retired = anonymous_client.post("/api/outbound-orders", json={})
    assert retired.status_code == 410
    assert retired.json["error"]["code"] == "USE_COMPLIANT_WORKFLOW"
    customer = anonymous_client.post(
        "/api/customers", json={"code": "X", "name": "X", "contract_emails": ["x@y.vn"]}
    )
    assert customer.status_code == 201
    customer_id = customer.json["id"]
    assert anonymous_client.put(
        f"/api/customers/{customer_id}",
        json={"code": "X", "name": "X mới", "contract_emails": ["new@x.vn"]},
    ).status_code == 200
    assert anonymous_client.delete(f"/api/customers/{customer_id}").status_code == 200
    assert anonymous_client.post(
        "/api/categories", json={"code": "X", "name": "X"}
    ).status_code == 403


def test_role_matrix_blocks_cross_function_mutations(app):
    cs = app.test_client()
    warehouse = app.test_client()
    login(cs, "cs", "CS@12345")
    login(warehouse, "warehouse", "Kho@12345")

    assert warehouse.post("/api/customers", json={
        "code": "NOPE",
        "name": "Không được tạo",
        "contract_emails": ["nope@example.com"],
    }).status_code == 403
    assert warehouse.post("/api/inbound-receipts", json={}).status_code == 403
    assert cs.post("/api/inbound-receipts/999/confirm").status_code == 403
    assert cs.post("/api/outbound-receipts/999/confirm").status_code == 403
    assert cs.post("/api/stocktakes", json={}).status_code == 403
    assert warehouse.get("/api/inventory").status_code == 200


def test_contract_email_validation_rolls_back_create_and_update(app):
    cs = app.test_client()
    login(cs, "cs", "CS@12345")

    invalid = cs.post("/api/customers", json={
        "code": "KH-ROLLBACK",
        "name": "Không được lưu",
        "contract_emails": ["khong-hop-le"],
    })
    assert invalid.status_code == 422
    assert not any(
        row["code"] == "KH-ROLLBACK"
        for row in cs.get("/api/customers").json["data"]
    )

    created = cs.post("/api/customers", json={
        "code": "KH-ROLLBACK",
        "name": "Tên ban đầu",
        "contract_emails": ["hopdong@rollback.vn"],
    })
    assert created.status_code == 201
    customer_id = created.json["id"]
    invalid_update = cs.put(f"/api/customers/{customer_id}", json={
        "code": "KH-ROLLBACK",
        "name": "Tên không được lưu",
        "contract_emails": ["van-khong-hop-le"],
    })
    assert invalid_update.status_code == 422
    saved = next(
        row for row in cs.get("/api/customers").json["data"]
        if row["id"] == customer_id
    )
    assert saved["name"] == "Tên ban đầu"
    assert saved["contract_emails"] == ["hopdong@rollback.vn"]


def test_invalid_numbers_dates_and_unknown_api_are_json(app):
    cs = app.test_client()
    login(cs, "cs", "CS@12345")
    base = {
        "customer_id": 1,
        "request_email": "muahang@khachhang.vn",
        "warehouse_id": 1,
        "items": [{"product_id": 1, "quantity": 1}],
    }

    invalid_id = cs.post(
        "/api/outbound-receipts",
        json={**base, "warehouse_id": "không-phải-số"},
    )
    assert invalid_id.status_code == 422
    assert invalid_id.is_json
    invalid_date = cs.post(
        "/api/outbound-receipts",
        json={**base, "outbound_date": "2026-99-99"},
    )
    assert invalid_date.status_code == 422
    invalid_number = cs.post(
        "/api/outbound-receipts",
        json={**base, "items": [{"product_id": 1, "quantity": float("nan")}]},
    )
    assert invalid_number.status_code == 422
    assert invalid_number.json["error"]["code"] == "VALIDATION_ERROR"

    missing = cs.get("/api/khong-ton-tai")
    assert missing.status_code == 404
    assert missing.is_json
    assert missing.json["error"]["code"] == "HTTP_404"


def _inbound_payload():
    return {
        "supplier_id": 1,
        "warehouse_id": 1,
        "expected_date": "2026-07-28",
        "items": [{
            "product_id": 1,
            "quantity": 10,
            "unit": "Cái",
            "pallet_id": "PALLET-TEST-01",
            "barcode": "LOT-TEST-01",
            "location": "A-01",
            "expiry_date": "2027-01-01",
        }],
    }


def test_inbound_reference_line_validation_is_atomic(app):
    cs = app.test_client()
    login(cs, "cs", "CS@12345")
    with app.app_context():
        before = app.get_db().execute(
            "SELECT COUNT(*) FROM inbound_receipts"
        ).fetchone()[0]

    invalid_warehouse = _inbound_payload()
    invalid_warehouse["warehouse_id"] = 999
    assert cs.post("/api/inbound-receipts", json=invalid_warehouse).status_code == 404
    invalid_supplier = _inbound_payload()
    invalid_supplier["supplier_id"] = 999
    assert cs.post("/api/inbound-receipts", json=invalid_supplier).status_code == 404

    duplicate = _inbound_payload()
    duplicate["items"].append({
        **duplicate["items"][0],
        "product_id": 2,
        "barcode": "LOT-TEST-DUPLICATE",
    })
    duplicate_response = cs.post("/api/inbound-receipts", json=duplicate)
    assert duplicate_response.status_code == 409
    assert duplicate_response.json["error"]["code"] == "DUPLICATE_LINE"

    wrong_unit = _inbound_payload()
    wrong_unit["items"][0]["unit"] = "Kg"
    unit_response = cs.post("/api/inbound-receipts", json=wrong_unit)
    assert unit_response.status_code == 409
    assert unit_response.json["error"]["code"] == "UNIT_MISMATCH"
    with app.app_context():
        assert app.get_db().execute(
            "SELECT COUNT(*) FROM inbound_receipts"
        ).fetchone()[0] == before


def test_inbound_inspection_failures_and_cancel_are_atomic(app):
    cs = app.test_client()
    login(cs, "cs", "CS@12345")
    receipt_id = cs.post(
        "/api/inbound-receipts", json=_inbound_payload()
    ).json["id"]
    warehouse = app.test_client()
    login(warehouse, "warehouse", "Kho@12345")
    item_id = warehouse.get(
        f"/api/inbound-receipts/{receipt_id}"
    ).json["data"]["items"][0]["id"]

    assert warehouse.post(
        f"/api/inbound-receipts/{receipt_id}/confirm"
    ).status_code == 409
    incomplete = warehouse.post(
        f"/api/inbound-receipts/{receipt_id}/inspect", json={"items": []}
    )
    assert incomplete.status_code == 409
    assert incomplete.json["error"]["code"] == "ITEM_SET_MISMATCH"
    mismatch = warehouse.post(
        f"/api/inbound-receipts/{receipt_id}/inspect",
        json={"items": [{
            "item_id": item_id,
            "accepted_quantity": 8,
            "rejected_quantity": 1,
            "reject_reason": "Thiếu",
        }]},
    )
    assert mismatch.status_code == 409
    no_reason = warehouse.post(
        f"/api/inbound-receipts/{receipt_id}/inspect",
        json={"items": [{
            "item_id": item_id,
            "accepted_quantity": 8,
            "rejected_quantity": 2,
        }]},
    )
    assert no_reason.status_code == 422
    with app.app_context():
        db = app.get_db()
        assert db.execute(
            "SELECT status FROM inbound_receipts WHERE id=?", (receipt_id,)
        ).fetchone()[0] == "DRAFT"
        item = db.execute(
            "SELECT accepted_quantity,rejected_quantity FROM inbound_items WHERE id=?",
            (item_id,),
        ).fetchone()
        assert item["accepted_quantity"] is None
        assert item["rejected_quantity"] is None

    cancelled = warehouse.post(f"/api/inbound-receipts/{receipt_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json["data"]["status"] == "CANCELLED"
    assert warehouse.post(
        f"/api/inbound-receipts/{receipt_id}/cancel"
    ).status_code == 409
    assert warehouse.post(
        f"/api/inbound-receipts/{receipt_id}/confirm"
    ).status_code == 409
    assert warehouse.post("/api/inbound-receipts/999/cancel").status_code == 404


def test_inbound_transaction_and_idempotency(app):
    cs = app.test_client()
    login(cs, "cs", "CS@12345")
    created = cs.post("/api/inbound-receipts", json=_inbound_payload())
    assert created.status_code == 201
    receipt_id = created.json["id"]

    warehouse = app.test_client()
    login(warehouse, "warehouse", "Kho@12345")
    inspected = warehouse.post(
        f"/api/inbound-receipts/{receipt_id}/inspect",
        json={"items": [{
            "item_id": 1,
            "accepted_quantity": 8,
            "rejected_quantity": 2,
            "reject_reason": "Móp bao bì",
        }]},
    )
    assert inspected.status_code == 200
    confirmed = warehouse.post(f"/api/inbound-receipts/{receipt_id}/confirm")
    assert confirmed.status_code == 200
    repeated = warehouse.post(f"/api/inbound-receipts/{receipt_id}/confirm")
    assert repeated.status_code == 200
    assert repeated.json["data"]["idempotent"] is True
    with app.app_context():
        db = app.get_db()
        assert db.execute(
            "SELECT COUNT(*) FROM wms_movements WHERE reference_type='INBOUND_RECEIPT' AND reference_id=?",
            (receipt_id,),
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT quantity FROM inventory_lots WHERE pallet_id='PALLET-TEST-01'"
        ).fetchone()[0] == 8


def test_inbound_capacity_error_is_atomic(app):
    with app.app_context():
        product = app.get_db().execute(
            "SELECT quantity,max_stock FROM products WHERE id=1"
        ).fetchone()
        quantity = product["max_stock"] - product["quantity"] + 1
    payload = _inbound_payload()
    payload["items"][0]["quantity"] = quantity
    payload["items"][0]["pallet_id"] = "PALLET-OVER-CAPACITY"
    payload["items"][0]["barcode"] = "LOT-OVER-CAPACITY"

    cs = app.test_client()
    login(cs, "cs", "CS@12345")
    created = cs.post("/api/inbound-receipts", json=payload)
    warehouse = app.test_client()
    login(warehouse, "warehouse", "Kho@12345")
    receipt_id = created.json["id"]
    item_id = warehouse.get(
        f"/api/inbound-receipts/{receipt_id}"
    ).json["data"]["items"][0]["id"]
    inspected = warehouse.post(
        f"/api/inbound-receipts/{receipt_id}/inspect",
        json={"items": [{
            "item_id": item_id,
            "accepted_quantity": quantity,
            "rejected_quantity": 0,
        }]},
    )
    assert inspected.status_code == 200
    confirmed = warehouse.post(f"/api/inbound-receipts/{receipt_id}/confirm")
    assert confirmed.status_code == 409
    assert confirmed.json["error"]["code"] == "STOCK_CAPACITY_EXCEEDED"
    with app.app_context():
        db = app.get_db()
        assert db.execute(
            "SELECT COUNT(*) FROM inventory_lots WHERE pallet_id='PALLET-OVER-CAPACITY'"
        ).fetchone()[0] == 0
        assert db.execute(
            "SELECT status FROM inbound_receipts WHERE id=?", (receipt_id,)
        ).fetchone()[0] == "INSPECTED"


def _create_customer_and_outbound(app, quantity=5):
    admin = app.test_client()
    login(admin, "admin", "Admin@123")
    customer = admin.post("/api/customers", json={
        "code": "KH-TST",
        "name": "Khách kiểm thử",
        "tax_code": "0400123999",
        "phone": "0905000999",
        "address": "Đà Nẵng",
        "contract_emails": ["hopdong@khach.vn"],
    })
    customer_id = customer.json["id"]
    cs = app.test_client()
    login(cs, "cs", "CS@12345")
    rejected = cs.post("/api/outbound-receipts", json={
        "customer_id": customer_id,
        "request_email": "gia@mao.vn",
        "warehouse_id": 1,
        "items": [{"product_id": 1, "quantity": quantity, "unit": "Cái"}],
    })
    assert rejected.status_code == 422
    accepted = cs.post("/api/outbound-receipts", json={
        "customer_id": customer_id,
        "request_email": "hopdong@khach.vn",
        "warehouse_id": 1,
        "items": [{"product_id": 1, "quantity": quantity, "unit": "Cái"}],
    })
    assert accepted.status_code == 201
    return accepted.json["id"]


def test_outbound_contract_email_fefo_and_idempotency(app):
    with app.app_context():
        db = app.get_db()
        db.executemany(
            """INSERT INTO inventory_lots
               (pallet_id,barcode,product_id,warehouse_id,unit,location,received_at,expiry_date,quantity)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            [
                ("FEFO-LATE", "FEFO-LATE-BC", 1, 1, "Cái", "F-02", "2026-02-01", "2027-12-31", 3),
                ("FEFO-EARLY", "FEFO-EARLY-BC", 1, 1, "Cái", "F-01", "2026-03-01", "2026-12-31", 3),
            ],
        )
        db.execute("UPDATE products SET quantity=quantity+6 WHERE id=1")
        db.commit()
    order_id = _create_customer_and_outbound(app, 5)
    warehouse = app.test_client()
    login(warehouse, "warehouse", "Kho@12345")
    picks = warehouse.get(f"/api/outbound-receipts/{order_id}/picking-list")
    assert picks.status_code == 200
    assert picks.json["meta"]["strategy"] == "FEFO_THEN_FIFO"
    assert picks.json["data"][0]["pallet_id"] == "FEFO-EARLY"
    before = sum(float(item["quantity"]) for item in picks.json["data"])
    assert before == 5
    confirmed = warehouse.post(f"/api/outbound-receipts/{order_id}/confirm")
    assert confirmed.status_code == 200
    repeated = warehouse.post(f"/api/outbound-receipts/{order_id}/confirm")
    assert repeated.status_code == 200
    assert repeated.json["data"]["idempotent"] is True
    with app.app_context():
        assert app.get_db().execute(
            "SELECT COUNT(*) FROM wms_movements WHERE reference_type='OUTBOUND_RECEIPT' AND reference_id=?",
            (order_id,),
        ).fetchone()[0] == 2


def test_outbound_validation_and_cancel_preserve_stock(app):
    cs = app.test_client()
    login(cs, "cs", "CS@12345")
    missing_customer = cs.post("/api/outbound-receipts", json={
        "customer_id": 999,
        "request_email": "nobody@example.com",
        "warehouse_id": 1,
        "items": [{"product_id": 1, "quantity": 1}],
    })
    assert missing_customer.status_code == 404

    order_id = _create_customer_and_outbound(app, 2)
    with app.app_context():
        before = app.get_db().execute(
            "SELECT quantity FROM products WHERE id=1"
        ).fetchone()[0]
    warehouse = app.test_client()
    login(warehouse, "warehouse", "Kho@12345")
    cancelled = warehouse.post(f"/api/outbound-receipts/{order_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json["data"]["status"] == "cancelled"
    assert warehouse.post(
        f"/api/outbound-receipts/{order_id}/confirm"
    ).status_code == 409
    assert warehouse.post(
        f"/api/outbound-receipts/{order_id}/cancel"
    ).status_code == 409
    assert warehouse.post("/api/outbound-receipts/999/cancel").status_code == 404
    with app.app_context():
        db = app.get_db()
        assert db.execute(
            "SELECT quantity FROM products WHERE id=1"
        ).fetchone()[0] == before
        assert db.execute(
            "SELECT COUNT(*) FROM outbound_allocations WHERE order_id=?", (order_id,)
        ).fetchone()[0] == 0
        assert db.execute(
            "SELECT COUNT(*) FROM wms_movements WHERE reference_type='OUTBOUND_RECEIPT' AND reference_id=?",
            (order_id,),
        ).fetchone()[0] == 0


def test_expired_lots_are_not_pickable(app):
    with app.app_context():
        db = app.get_db()
        db.execute(
            """INSERT INTO inventory_lots
               (pallet_id,barcode,product_id,warehouse_id,unit,location,received_at,expiry_date,quantity)
               VALUES('EXPIRED-LOT','EXPIRED-BC',1,1,'Cái','X-01','2020-01-01','2020-12-31',2)"""
        )
        db.execute("UPDATE products SET quantity=quantity+2 WHERE id=1")
        db.commit()
    order_id = _create_customer_and_outbound(app, 1)
    warehouse = app.test_client()
    login(warehouse, "warehouse", "Kho@12345")

    picks = warehouse.get(f"/api/outbound-receipts/{order_id}/picking-list")
    assert picks.status_code == 200
    assert all(row["pallet_id"] != "EXPIRED-LOT" for row in picks.json["data"])
    assert warehouse.post(f"/api/outbound-receipts/{order_id}/confirm").status_code == 200
    with app.app_context():
        assert app.get_db().execute(
            "SELECT quantity FROM inventory_lots WHERE pallet_id='EXPIRED-LOT'"
        ).fetchone()[0] == 2


def test_outbound_rejects_aggregate_lot_stock_mismatch(app):
    order_id = _create_customer_and_outbound(app, 1)
    with app.app_context():
        db = app.get_db()
        lot_before = db.execute(
            "SELECT SUM(quantity) FROM inventory_lots WHERE product_id=1 AND active=1"
        ).fetchone()[0]
        db.execute("UPDATE products SET quantity=0 WHERE id=1")
        db.commit()
    warehouse = app.test_client()
    login(warehouse, "warehouse", "Kho@12345")

    response = warehouse.post(f"/api/outbound-receipts/{order_id}/confirm")
    assert response.status_code == 409
    assert response.json["error"]["code"] == "STOCK_INVARIANT_BROKEN"
    with app.app_context():
        db = app.get_db()
        assert db.execute(
            "SELECT SUM(quantity) FROM inventory_lots WHERE product_id=1 AND active=1"
        ).fetchone()[0] == lot_before
        assert db.execute(
            "SELECT status FROM outbound_orders WHERE id=?", (order_id,)
        ).fetchone()[0] == "pending"


def test_insufficient_outbound_rolls_back(app):
    order_id = _create_customer_and_outbound(app, 999999)
    warehouse = app.test_client()
    login(warehouse, "warehouse", "Kho@12345")
    with app.app_context():
        before = app.get_db().execute("SELECT quantity FROM products WHERE id=1").fetchone()[0]
    response = warehouse.post(f"/api/outbound-receipts/{order_id}/confirm")
    assert response.status_code == 409
    with app.app_context():
        db = app.get_db()
        assert db.execute("SELECT quantity FROM products WHERE id=1").fetchone()[0] == before
        assert db.execute(
            "SELECT COUNT(*) FROM wms_movements WHERE reference_type='OUTBOUND_RECEIPT' AND reference_id=?",
            (order_id,),
        ).fetchone()[0] == 0


def test_stocktake_rechecks_snapshot_and_report_csv(app):
    warehouse = app.test_client()
    login(warehouse, "warehouse", "Kho@12345")
    inventory = warehouse.get("/api/inventory").json["data"]
    lot = inventory[0]
    created = warehouse.post("/api/stocktakes", json={
        "warehouse_id": lot["warehouse_id"],
        "reason": "Kiểm kê cuối kỳ",
        "items": [{"lot_id": lot["id"], "actual_quantity": lot["quantity"] + 1}],
    })
    assert created.status_code == 201
    confirmed = warehouse.post(f"/api/stocktakes/{created.json['id']}/confirm")
    assert confirmed.status_code == 200
    repeated = warehouse.post(f"/api/stocktakes/{created.json['id']}/confirm")
    assert repeated.status_code == 200
    assert repeated.json["data"]["idempotent"] is True
    assert warehouse.get("/api/reports/summary").status_code == 200
    csv_response = warehouse.get("/api/reports/movements.csv")
    assert csv_response.status_code == 200
    assert csv_response.mimetype == "text/csv"


def test_stocktake_stale_snapshot_rolls_back_adjustment(app):
    warehouse = app.test_client()
    login(warehouse, "warehouse", "Kho@12345")
    lot = warehouse.get("/api/inventory").json["data"][0]
    created = warehouse.post("/api/stocktakes", json={
        "warehouse_id": lot["warehouse_id"],
        "reason": "Kiểm tra snapshot cũ",
        "items": [{"lot_id": lot["id"], "actual_quantity": lot["quantity"] + 5}],
    })
    stocktake_id = created.json["id"]
    with app.app_context():
        db = app.get_db()
        db.execute(
            "UPDATE inventory_lots SET quantity=quantity+1 WHERE id=?", (lot["id"],)
        )
        db.execute(
            "UPDATE products SET quantity=quantity+1 WHERE id=?", (lot["product_id"],)
        )
        db.commit()
        changed_quantity = db.execute(
            "SELECT quantity FROM inventory_lots WHERE id=?", (lot["id"],)
        ).fetchone()[0]

    stale = warehouse.post(f"/api/stocktakes/{stocktake_id}/confirm")
    assert stale.status_code == 409
    assert stale.json["error"]["code"] == "CONCURRENT_STOCK_CHANGE"
    with app.app_context():
        db = app.get_db()
        assert db.execute(
            "SELECT quantity FROM inventory_lots WHERE id=?", (lot["id"],)
        ).fetchone()[0] == changed_quantity
        assert db.execute(
            "SELECT status FROM stocktake_headers WHERE id=?", (stocktake_id,)
        ).fetchone()[0] == "DRAFT"
        assert db.execute(
            "SELECT COUNT(*) FROM wms_movements WHERE reference_type='STOCKTAKE' AND reference_id=?",
            (stocktake_id,),
        ).fetchone()[0] == 0


def test_restore_requires_explicit_confirmation(app):
    result = app.test_cli_runner().invoke(args=["restore-db", "missing.sqlite3"])
    assert result.exit_code != 0


def test_backup_cli_creates_consistent_database(app):
    from pathlib import Path
    from uuid import uuid4

    directory = Path.cwd() / ".pytest-tmp"
    directory.mkdir(exist_ok=True)
    destination = directory / f"backup-{uuid4().hex}.sqlite3"
    result = app.test_cli_runner().invoke(args=["backup-db", str(destination)])
    assert result.exit_code == 0
    assert destination.exists()
    destination.unlink()


def test_restore_rejects_corrupt_database_without_replacing_live_db(tmp_path):
    from app import create_app

    database = tmp_path / "live.sqlite3"
    application = create_app({"TESTING": True, "DATABASE": str(database)})
    corrupt = tmp_path / "corrupt.sqlite3"
    corrupt.write_text("not a sqlite database", encoding="utf-8")

    result = application.test_cli_runner().invoke(
        args=["restore-db", str(corrupt), "--yes"]
    )
    assert result.exit_code != 0
    with application.app_context():
        assert application.get_db().execute(
            "SELECT COUNT(*) FROM products"
        ).fetchone()[0] > 0


def test_backup_restore_round_trip(tmp_path):
    from app import create_app

    database = tmp_path / "live.sqlite3"
    backup = tmp_path / "backup.sqlite3"
    application = create_app({"TESTING": True, "DATABASE": str(database)})
    runner = application.test_cli_runner()
    assert runner.invoke(args=["backup-db", str(backup)]).exit_code == 0
    with application.app_context():
        db = application.get_db()
        original = db.execute("SELECT name FROM products WHERE id=1").fetchone()[0]
        db.execute("UPDATE products SET name='Đã sửa sau backup' WHERE id=1")
        db.commit()

    restored = runner.invoke(args=["restore-db", str(backup), "--yes"])
    assert restored.exit_code == 0
    with application.app_context():
        assert application.get_db().execute(
            "SELECT name FROM products WHERE id=1"
        ).fetchone()[0] == original
