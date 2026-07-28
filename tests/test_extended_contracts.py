from sqlalchemy import select

from app.extensions import db as orm
from app.models import CustomerContractEmail
from conftest import login


def headers(csrf):
    return {"X-CSRF-Token": csrf}


def test_roles_units_and_master_lifecycle_contracts(app, client, admin_login):
    _, csrf = admin_login
    auth = headers(csrf)

    roles = client.get("/api/roles")
    assert roles.status_code == 200
    assert {"ADMIN", "CS", "WAREHOUSE"} <= {
        item["code"] for item in roles.get_json()["items"]
    }
    units = client.get("/api/units")
    assert units.status_code == 200
    assert units.get_json()["items"]

    category_delete = client.delete("/api/categories/1", headers=auth)
    assert category_delete.status_code == 409
    category_inactive = client.put(
        "/api/categories/1",
        json={
            "code": "NVL",
            "name": "Nguyên vật liệu",
            "description": "Đã phát sinh nên chỉ ngừng hoạt động",
            "status": "inactive",
        },
        headers=auth,
    )
    assert category_inactive.status_code == 200

    product = client.post(
        "/api/products",
        json={
            "sku": "SKU-LIFECYCLE",
            "barcode": "893LIFECYCLE",
            "name": "Sản phẩm vòng đời",
            "category_id": 2,
            "warehouse_id": 1,
            "unit": "Thùng",
            "min_quantity": 2,
            "location": "QA-01",
            "status": "active",
        },
        headers=auth,
    )
    assert product.status_code == 201
    product_id = product.get_json()["id"]
    updated = client.put(
        f"/api/products/{product_id}",
        json={
            "name": "Sản phẩm đã cập nhật",
            "description": "Không xóa cứng",
            "status": "inactive",
        },
        headers=auth,
    )
    assert updated.status_code == 200
    stored = client.get("/api/products?search=SKU-LIFECYCLE").get_json()["items"]
    assert len(stored) == 1
    assert stored[0]["status"] == "inactive"
    lookups = client.get("/api/operations/lookups").get_json()
    assert product_id not in {item["id"] for item in lookups["products"]}

    warehouse = client.post(
        "/api/warehouses",
        json={
            "code": "QAWH",
            "name": "Kho kiểm thử",
            "address": "TP.HCM",
            "status": "active",
        },
        headers=auth,
    )
    assert warehouse.status_code == 201
    warehouse_id = warehouse.get_json()["id"]
    assert client.put(
        f"/api/warehouses/{warehouse_id}",
        json={"status": "inactive"},
        headers=auth,
    ).status_code == 200
    assert warehouse_id not in {
        item["id"]
        for item in client.get("/api/operations/lookups").get_json()["warehouses"]
    }


def test_partner_update_normalizes_contract_emails_and_controls_outbound(
    app, client
):
    _, csrf = login(client, "cs", "Cs@123456")
    auth = headers(csrf)
    customer = client.post(
        "/api/customers",
        json={
            "code": "KH-LIFECYCLE",
            "name": "Khách hàng vòng đời",
            "email": "contact@example.com",
            "contract_emails": " First@Example.com,second@example.com ",
            "status": "active",
        },
        headers=auth,
    )
    assert customer.status_code == 201
    customer_id = customer.get_json()["id"]

    updated = client.put(
        f"/api/customers/{customer_id}",
        json={
            "contract_emails": "NEW@example.com,new@example.com,other@example.com",
            "status": "active",
        },
        headers=auth,
    )
    assert updated.status_code == 200
    with app.app_context():
        emails = list(
            orm.session.scalars(
                select(CustomerContractEmail.normalized_email)
                .where(CustomerContractEmail.customer_id == customer_id)
                .order_by(CustomerContractEmail.normalized_email)
            )
        )
        assert emails == ["new@example.com", "other@example.com"]

    rejected = client.post(
        "/api/outbound-receipts",
        json={
            "code": "PX-OLD-EMAIL",
            "partner_id": customer_id,
            "warehouse_id": 1,
            "request_email": "first@example.com",
            "items": [{"inventory_id": 1, "quantity": 1}],
        },
        headers=auth,
    )
    assert rejected.status_code == 422
    accepted = client.post(
        "/api/outbound-receipts",
        json={
            "code": "PX-NEW-EMAIL",
            "partner_id": customer_id,
            "warehouse_id": 1,
            "request_email": "NEW@EXAMPLE.COM",
            "items": [{"inventory_id": 1, "quantity": 1}],
        },
        headers=auth,
    )
    assert accepted.status_code == 201

    assert client.put(
        f"/api/customers/{customer_id}",
        json={"status": "inactive"},
        headers=auth,
    ).status_code == 200
    blocked = client.post(
        "/api/outbound-receipts",
        json={
            "code": "PX-INACTIVE-CUSTOMER",
            "partner_id": customer_id,
            "warehouse_id": 1,
            "request_email": "new@example.com",
            "items": [{"inventory_id": 1, "quantity": 1}],
        },
        headers=auth,
    )
    assert blocked.status_code == 422


def test_draft_receipt_edit_detail_and_lock_after_submission(client):
    _, csrf = login(client, "cs", "Cs@123456")
    auth = headers(csrf)
    created = client.post(
        "/api/inbound-receipts",
        json={
            "code": "PN-DRAFT-EDIT",
            "partner_id": 1,
            "warehouse_id": 1,
            "status": "draft",
            "container_no": "CONT-OLD",
            "items": [{"inventory_id": 1, "quantity": 2}],
        },
        headers=auth,
    )
    assert created.status_code == 201
    receipt_id = created.get_json()["id"]

    edited = client.put(
        f"/api/inbound-receipts/{receipt_id}",
        json={
            "code": "PN-DRAFT-EDITED",
            "partner_id": 1,
            "warehouse_id": 1,
            "status": "pending",
            "container_no": "CONT-NEW",
            "seal_no": "SEAL-QA",
            "items": [
                {
                    "inventory_id": 1,
                    "quantity": 2,
                    "pallet_id": "PALLET-DRAFT-QA",
                    "expiry_date": "2027-01-01",
                }
            ],
        },
        headers=auth,
    )
    assert edited.status_code == 200
    detail = client.get(f"/api/inbound-receipts/{receipt_id}")
    assert detail.status_code == 200
    assert detail.get_json()["item"]["code"] == "PN-DRAFT-EDITED"
    assert detail.get_json()["item"]["container_no"] == "CONT-NEW"

    locked = client.put(
        f"/api/inbound-receipts/{receipt_id}",
        json={"note": "Không được sửa phiếu đã gửi"},
        headers=auth,
    )
    assert locked.status_code == 409


def test_stocktake_detail_cancel_and_state_guards(client, manager_login):
    _, csrf = manager_login
    auth = headers(csrf)
    current = client.get("/api/inventory/1").get_json()["item"]["quantity"]
    created = client.post(
        "/api/stocktakes",
        json={
            "code": "KK-CANCEL-QA",
            "warehouse_id": 1,
            "items": [
                {
                    "inventory_id": 1,
                    "counted_quantity": current,
                    "reason": "",
                }
            ],
        },
        headers=auth,
    )
    assert created.status_code == 201
    stocktake_id = created.get_json()["id"]
    detail = client.get(f"/api/stocktakes/{stocktake_id}")
    assert detail.status_code == 200
    assert detail.get_json()["item"]["items"][0]["difference"] == 0

    cancelled = client.post(
        f"/api/stocktakes/{stocktake_id}/cancel", headers=auth
    )
    assert cancelled.status_code == 200
    repeated = client.post(
        f"/api/stocktakes/{stocktake_id}/cancel", headers=auth
    )
    assert repeated.status_code == 200
    assert repeated.get_json()["already_cancelled"] is True
    assert client.post(
        f"/api/stocktakes/{stocktake_id}/confirm", headers=auth
    ).status_code == 409


def test_new_mutation_routes_enforce_csrf_and_role(client):
    _, csrf = login(client, "warehouse", "Kho@12345")
    assert client.post(
        "/api/warehouses",
        json={"code": "NOPE", "name": "Không được phép"},
        headers=headers(csrf),
    ).status_code == 403
    assert client.put(
        "/api/products/1",
        json={"status": "inactive"},
        headers=headers(csrf),
    ).status_code == 403

    client.post("/api/auth/logout", headers=headers(csrf))
    _, csrf = login(client, "admin", "Admin@123")
    assert client.put(
        "/api/products/1", json={"status": "inactive"}
    ).status_code == 403


def test_new_routes_return_structured_not_found(client, admin_login):
    _, csrf = admin_login
    auth = headers(csrf)
    requests = (
        client.put("/api/products/99999", json={}, headers=auth),
        client.put("/api/customers/99999", json={}, headers=auth),
        client.put("/api/suppliers/99999", json={}, headers=auth),
        client.put("/api/warehouses/99999", json={}, headers=auth),
        client.put("/api/inbound-receipts/99999", json={}, headers=auth),
        client.put("/api/outbound-receipts/99999", json={}, headers=auth),
        client.get("/api/stocktakes/99999"),
        client.post("/api/stocktakes/99999/cancel", headers=auth),
    )
    for response in requests:
        assert response.status_code == 404
        assert response.get_json()["error"]["code"] == "not_found"


def test_supplier_update_and_outbound_draft_edit(client):
    _, csrf = login(client, "cs", "Cs@123456")
    auth = headers(csrf)
    supplier = client.post(
        "/api/suppliers",
        json={
            "code": "NCC-EDIT-QA",
            "name": "Nhà cung cấp cần sửa",
            "email": "old@supplier.test",
            "address": "Địa chỉ cũ",
            "status": "active",
        },
        headers=auth,
    )
    assert supplier.status_code == 201
    supplier_id = supplier.get_json()["id"]
    changed = client.put(
        f"/api/suppliers/{supplier_id}",
        json={
            "name": "Nhà cung cấp đã sửa",
            "email": "new@supplier.test",
            "address": "Địa chỉ mới",
            "status": "inactive",
        },
        headers=auth,
    )
    assert changed.status_code == 200
    stored = client.get("/api/suppliers?search=NCC-EDIT-QA").get_json()["items"][0]
    assert stored["email"] == "new@supplier.test"
    assert stored["status"] == "inactive"

    outbound = client.post(
        "/api/outbound-receipts",
        json={
            "code": "PX-DRAFT-EDIT",
            "partner_id": 1,
            "warehouse_id": 1,
            "request_email": "kho@minhphat.vn",
            "status": "draft",
            "items": [{"inventory_id": 1, "quantity": 2}],
        },
        headers=auth,
    )
    assert outbound.status_code == 201
    receipt_id = outbound.get_json()["id"]
    invalid = client.put(
        f"/api/outbound-receipts/{receipt_id}",
        json={
            "request_email": "not-in-contract@example.com",
            "status": "pending",
        },
        headers=auth,
    )
    assert invalid.status_code == 422
    submitted = client.put(
        f"/api/outbound-receipts/{receipt_id}",
        json={
            "request_email": "dieuphoi@minhphat.vn",
            "status": "pending",
            "items": [{"inventory_id": 1, "quantity": 3}],
        },
        headers=auth,
    )
    assert submitted.status_code == 200
    assert client.put(
        f"/api/outbound-receipts/{receipt_id}",
        json={"note": "Không sửa sau khi gửi"},
        headers=auth,
    ).status_code == 409
