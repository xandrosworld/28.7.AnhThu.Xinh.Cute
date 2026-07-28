from datetime import date, timedelta


def _headers(csrf):
    return {"X-CSRF-Token": csrf}


def test_stock_movements_requires_authentication(client):
    response = client.get("/api/stock-movements")
    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "unauthorized"


def test_stock_movements_filters_and_paginates(client, manager_login, db):
    _, csrf = manager_login
    for index in range(3):
        db.execute(
            """INSERT INTO stock_movements
               (inventory_id,movement_type,reference_code,quantity_change,
                balance_after,pallet_id,reason,created_by)
               VALUES (1,'adjustment',?,?,?,?,?,1)""",
            (
                f"QA-MOVE-{index}",
                index + 1,
                1251 + index,
                f"QA-PALLET-{index}",
                "Kiểm thử API",
            ),
        )
    db.commit()

    response = client.get(
        "/api/stock-movements?search=QA-MOVE&movement_type=adjustment"
        "&product_id=1&warehouse_id=1&page=2&per_page=1"
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["pagination"] == {
        "page": 2,
        "per_page": 1,
        "total": 3,
        "pages": 3,
    }
    assert payload["meta"] == payload["pagination"]
    assert len(payload["data"]) == 1
    assert payload["items"][0]["reference_code"].startswith("QA-MOVE-")
    assert payload["items"][0]["warehouse_name"]
    assert client.get(
        "/api/stock-movements?inventory_id=1&movement_type=adjustment"
    ).status_code == 200
    referenced = client.get(
        "/api/stock-movements?reference=QA-MOVE-1"
    ).get_json()
    assert [item["reference_code"] for item in referenced["items"]] == ["QA-MOVE-1"]


def test_stock_movement_filters_reject_invalid_values(client, admin_login):
    for query in (
        "movement_type=unknown",
        "product_id=abc",
        "inventory_id=abc",
        "product_id=1&inventory_id=2",
        "from=2026-02-30",
        "from=2026-12-31&to=2026-01-01",
    ):
        response = client.get(f"/api/stock-movements?{query}")
        assert response.status_code == 422
        assert response.get_json()["error"]["fields"]


def test_inventory_detail_has_movements_and_true_available_quantity(
    client, manager_login, db
):
    _, csrf = manager_login
    adjusted = client.post(
        "/api/inventory/1/adjustments",
        json={
            "new_quantity": 1251,
            "reason": "Kiểm kê định kỳ",
            "note": "Tạo lịch sử biến động",
        },
        headers=_headers(csrf),
    )
    assert adjusted.status_code == 200

    expired_quantity = 5
    db.execute(
        """INSERT INTO inventory_lots
           (product_id,warehouse_id,unit,pallet_id,quantity,expiry_date,status)
           VALUES (1,1,'Cây','QA-EXPIRED-AVAILABLE',?,?,'active')""",
        (
            expired_quantity,
            (date.today() - timedelta(days=1)).isoformat(),
        ),
    )
    db.execute(
        "UPDATE inventory SET quantity=quantity+? WHERE id=1",
        (expired_quantity,),
    )
    db.commit()

    response = client.get("/api/inventory/1")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["movements"]
    movement = payload["movements"][0]
    assert {
        "movement_type",
        "reference_code",
        "quantity_change",
        "balance_after",
        "created_at",
    } <= set(movement)
    assert payload["item"]["quantity"] == 1256
    assert payload["item"]["available_quantity"] == 1251

    listed = client.get("/api/inventory?search=SKU-1001").get_json()["items"][0]
    assert listed["quantity"] == 1256
    assert listed["available_quantity"] == 1251
