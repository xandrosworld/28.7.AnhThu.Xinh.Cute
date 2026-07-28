def test_inventory_filters_and_detail(client, staff_login):
    response = client.get("/api/inventory?status=out&search=SKU")
    assert response.status_code == 200
    items = response.get_json()["items"]
    assert items
    assert all(item["quantity"] == 0 for item in items)

    detail = client.get(f"/api/inventory/{items[0]['id']}")
    assert detail.status_code == 200
    assert detail.get_json()["item"]["status"] == "out"


def test_manager_can_adjust_stock_atomically(client, manager_login, db):
    _, csrf = manager_login
    before = client.get("/api/inventory/1").get_json()["item"]["quantity"]
    response = client.post(
        "/api/inventory/1/adjustments",
        json={
            "new_quantity": before + 25,
            "reason": "Kiểm kê định kỳ",
            "note": "Kiểm đếm cuối ca",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200

    detail = client.get("/api/inventory/1").get_json()
    assert detail["item"]["quantity"] == before + 25
    assert detail["adjustments"][0]["difference"] == 25
    assert db.execute(
        "SELECT COUNT(*) FROM audit_logs WHERE action = 'ADJUST_STOCK'"
    ).fetchone()[0] == 1


def test_staff_cannot_adjust_stock(client, staff_login):
    _, csrf = staff_login
    response = client.post(
        "/api/inventory/1/adjustments",
        json={"new_quantity": 10, "reason": "Kiểm kê định kỳ", "note": ""},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 403


def test_adjustment_validation(client, manager_login):
    _, csrf = manager_login
    response = client.post(
        "/api/inventory/1/adjustments",
        json={"new_quantity": -1, "reason": "Điều chỉnh khác", "note": ""},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 422
    assert {"new_quantity", "note"} <= set(response.get_json()["errors"])


def test_dashboard_has_database_summary(client, admin_login):
    response = client.get("/api/dashboard")
    assert response.status_code == 200
    data = response.get_json()
    assert data["summary"]["products"] == 12
    assert len(data["category_distribution"]) == 6
