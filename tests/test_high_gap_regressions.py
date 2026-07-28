from decimal import Decimal

from conftest import login


def _headers(csrf):
    return {"X-CSRF-Token": csrf}


def test_decimal_adjustment_and_stocktake_are_end_to_end(client, manager_login, db):
    _, csrf = manager_login
    current = Decimal(
        str(client.get("/api/inventory/1").get_json()["item"]["quantity"])
    )
    adjusted = current + Decimal("0.125")
    response = client.post(
        "/api/inventory/1/adjustments",
        json={
            "new_quantity": str(adjusted),
            "reason": "Kiểm kê định kỳ",
            "note": "Cân lại tồn lẻ",
        },
        headers=_headers(csrf),
    )
    assert response.status_code == 200
    assert Decimal(
        str(client.get("/api/inventory/1").get_json()["item"]["quantity"])
    ) == adjusted

    counted = adjusted + Decimal("0.375")
    stocktake = client.post(
        "/api/stocktakes",
        json={
            "code": "KK-DECIMAL-001",
            "warehouse_id": 1,
            "items": [
                {
                    "inventory_id": 1,
                    "counted_quantity": str(counted),
                    "reason": "Cân thực tế theo kilogram",
                }
            ],
        },
        headers=_headers(csrf),
    )
    assert stocktake.status_code == 201
    confirmed = client.post(
        f"/api/stocktakes/{stocktake.get_json()['id']}/confirm",
        headers=_headers(csrf),
    )
    assert confirmed.status_code == 200
    stored = db.execute(
        "SELECT counted_quantity FROM stocktake_items WHERE stocktake_id=?",
        (stocktake.get_json()["id"],),
    ).fetchone()[0]
    assert Decimal(str(stored)) == counted


def test_role_and_unit_foreign_keys_are_populated(client, admin_login, db):
    _, csrf = admin_login
    assert db.execute(
        "SELECT COUNT(*) FROM users WHERE role_id IS NULL"
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT COUNT(*) FROM inventory WHERE unit_id IS NULL"
    ).fetchone()[0] == 0

    created_user = client.post(
        "/api/users",
        json={
            "username": "fkuser",
            "password": "Strong@123",
            "full_name": "Người dùng FK",
            "email": "fkuser@example.com",
            "phone": "",
            "role": "warehouse",
            "status": "active",
        },
        headers=_headers(csrf),
    )
    assert created_user.status_code == 201
    user = db.execute(
        "SELECT role,role_id FROM users WHERE id=?", (created_user.get_json()["id"],)
    ).fetchone()
    role = db.execute("SELECT code FROM roles WHERE id=?", (user["role_id"],)).fetchone()
    assert role["code"] == user["role"].upper()

    unit = db.execute(
        "SELECT id,name FROM units WHERE code='THUNG'"
    ).fetchone()
    product = client.post(
        "/api/products",
        json={
            "sku": "SKU-FK-UNIT",
            "name": "Hàng kiểm thử khóa ngoại",
            "category_id": 1,
            "warehouse_id": 1,
            "unit_id": unit["id"],
            "unit": "giá trị alias không tin cậy",
            "min_quantity": "1.250",
        },
        headers=_headers(csrf),
    )
    assert product.status_code == 201
    stored_product = db.execute(
        "SELECT unit,unit_id,min_quantity FROM inventory WHERE id=?",
        (product.get_json()["id"],),
    ).fetchone()
    assert stored_product["unit_id"] == unit["id"]
    assert stored_product["unit"] == unit["name"]
    assert Decimal(str(stored_product["min_quantity"])) == Decimal("1.250")


def test_inbound_cannot_bypass_inspection(client):
    _, csrf = login(client, "quanlykho", "Kho@12345")
    created = client.post(
        "/api/inbound-receipts",
        json={
            "code": "PN-INSPECTION-REQUIRED",
            "partner_id": 1,
            "warehouse_id": 1,
            "status": "pending",
            "items": [{"inventory_id": 1, "quantity": "1.500"}],
        },
        headers=_headers(csrf),
    )
    assert created.status_code == 201
    receipt_id = created.get_json()["id"]
    detail = client.get(
        f"/api/inbound-receipts/{receipt_id}"
    ).get_json()["item"]
    assert detail["items"][0]["accepted_quantity"] == 0

    rejected = client.post(
        f"/api/inbound-receipts/{receipt_id}/confirm",
        headers=_headers(csrf),
    )
    assert rejected.status_code == 409
    assert "kiểm nhận" in rejected.get_json()["message"].lower()


def test_role_and_unit_configuration_lifecycle(client, admin_login, db):
    _, csrf = admin_login
    role = client.post(
        "/api/roles",
        json={
            "code": "AUDITOR",
            "name": "Kiểm toán viên",
            "description": "Vai trò cấu hình dự phòng",
        },
        headers=_headers(csrf),
    )
    assert role.status_code == 201
    assert client.put(
        f"/api/roles/{role.get_json()['id']}",
        json={"code": "AUDITOR", "name": "Kiểm toán kho", "status": "inactive"},
        headers=_headers(csrf),
    ).status_code == 200

    unit = client.post(
        "/api/units",
        json={"code": "KG", "name": "Kilogram", "allow_break_pack": True},
        headers=_headers(csrf),
    )
    assert unit.status_code == 201
    unit_id = unit.get_json()["id"]
    assert client.put(
        f"/api/units/{unit_id}",
        json={"code": "KG", "name": "Kilôgam", "status": "inactive"},
        headers=_headers(csrf),
    ).status_code == 200

    # A role/unit in active use may not be disabled.
    warehouse_role = db.execute(
        "SELECT id FROM roles WHERE code='WAREHOUSE'"
    ).fetchone()["id"]
    assert client.put(
        f"/api/roles/{warehouse_role}",
        json={"code": "WAREHOUSE", "name": "Nhân viên kho", "status": "inactive"},
        headers=_headers(csrf),
    ).status_code == 422
    thung = db.execute("SELECT id FROM units WHERE code='THUNG'").fetchone()["id"]
    assert client.put(
        f"/api/units/{thung}",
        json={"code": "THUNG", "name": "Thùng", "status": "inactive"},
        headers=_headers(csrf),
    ).status_code == 422
