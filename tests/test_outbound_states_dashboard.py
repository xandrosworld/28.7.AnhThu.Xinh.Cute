from conftest import login


def headers(csrf):
    return {"X-CSRF-Token": csrf}


def test_dashboard_exposes_inventory_and_receipt_kpis(client, admin_login):
    response = client.get("/api/dashboard")

    assert response.status_code == 200
    summary = response.get_json()["summary"]
    assert {
        "products",
        "total_quantity",
        "low_stock",
        "out_of_stock",
        "inbound_today",
        "outbound_today",
        "awaiting_processing",
    } <= set(summary)
    assert all(summary[key] >= 0 for key in summary)


def test_outbound_picking_and_rejection_state_machine(
    client, manager_login
):
    _, csrf = manager_login
    before = client.get("/api/inventory/1").get_json()["item"]["quantity"]

    started = client.post(
        "/api/outbound-receipts/2/start-picking",
        headers=headers(csrf),
    )
    assert started.status_code == 200
    assert client.get("/api/outbound-receipts/2").get_json()["item"]["status"] == "picking"

    repeated = client.post(
        "/api/outbound-receipts/2/start-picking",
        headers=headers(csrf),
    )
    assert repeated.status_code == 200
    assert repeated.get_json()["already_picking"] is True

    missing_reason = client.post(
        "/api/outbound-receipts/2/reject",
        json={},
        headers=headers(csrf),
    )
    assert missing_reason.status_code == 422
    assert "reason" in missing_reason.get_json()["error"]["fields"]

    rejected = client.post(
        "/api/outbound-receipts/2/reject",
        json={"reason": "Chứng từ giao hàng chưa hợp lệ"},
        headers=headers(csrf),
    )
    assert rejected.status_code == 200
    detail = client.get("/api/outbound-receipts/2").get_json()["item"]
    assert detail["status"] == "rejected"
    assert "chưa hợp lệ" in detail["note"].lower()

    cannot_confirm = client.post(
        "/api/outbound-receipts/2/confirm",
        headers=headers(csrf),
    )
    assert cannot_confirm.status_code == 409
    after = client.get("/api/inventory/1").get_json()["item"]["quantity"]
    assert after == before


def test_cs_cannot_start_or_reject_picking(client):
    _, csrf = login(client, "cs", "Cs@123456")

    assert client.post(
        "/api/outbound-receipts/2/start-picking",
        headers=headers(csrf),
    ).status_code == 403
    assert client.post(
        "/api/outbound-receipts/2/reject",
        json={"reason": "Không thuộc quyền CS"},
        headers=headers(csrf),
    ).status_code == 403
