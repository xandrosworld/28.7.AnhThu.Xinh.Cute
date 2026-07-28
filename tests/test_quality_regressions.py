import os
import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.extensions import db as orm
from app.models import InventoryLot, Product, ReceiptItem, StockMovement
from app.services import assert_stock_invariant
from conftest import login


def auth_headers(csrf):
    return {"X-CSRF-Token": csrf}


def _switch_user(client, csrf, username, password):
    client.post("/api/auth/logout", headers=auth_headers(csrf))
    return login(client, username, password)[1]


def test_inbound_rejection_requires_reason_and_only_accepted_quantity_enters_stock(
    app, client
):
    _, csrf = login(client, "cs", "Cs@123456")
    created = client.post(
        "/api/inbound-receipts",
        json={
            "code": "PN-INSPECTION-QA",
            "partner_id": 1,
            "warehouse_id": 1,
            "status": "pending",
            "items": [
                {
                    "inventory_id": 1,
                    "quantity": 5,
                    "pallet_id": "PALLET-INSPECTION-QA",
                    "barcode": "893QAINSPECTION",
                }
            ],
        },
        headers=auth_headers(csrf),
    )
    assert created.status_code == 201
    receipt_id = created.get_json()["id"]
    detail = client.get(f"/api/inbound-receipts/{receipt_id}").get_json()
    line_id = detail["item"]["items"][0]["id"]

    csrf = _switch_user(client, csrf, "warehouse", "Kho@12345")
    missing_reason = client.post(
        f"/api/inbound-receipts/{receipt_id}/inspect",
        json={"items": [{"id": line_id, "accepted_quantity": 4, "issue_note": ""}]},
        headers=auth_headers(csrf),
    )
    assert missing_reason.status_code == 422
    assert "items" in missing_reason.get_json()["error"]["fields"]

    inspected = client.post(
        f"/api/inbound-receipts/{receipt_id}/inspect",
        json={
            "items": [
                {
                    "id": line_id,
                    "accepted_quantity": 4,
                    "issue_note": "Thiếu một kiện khi dỡ container",
                }
            ]
        },
        headers=auth_headers(csrf),
    )
    assert inspected.status_code == 200

    with app.app_context():
        before = orm.session.get(Product, 1).quantity
    confirmed = client.post(
        f"/api/inbound-receipts/{receipt_id}/confirm",
        headers=auth_headers(csrf),
    )
    assert confirmed.status_code == 200

    with app.app_context():
        assert orm.session.get(Product, 1).quantity == before + Decimal("4")
        line = orm.session.get(ReceiptItem, line_id)
        assert line.accepted_quantity == Decimal("4")
        assert line.rejected_quantity == Decimal("1")
        assert line.issue_note == "Thiếu một kiện khi dỡ container"
        lot = orm.session.scalar(
            select(InventoryLot).where(
                InventoryLot.pallet_id == "PALLET-INSPECTION-QA"
            )
        )
        assert lot.quantity == Decimal("4")
        movement_count = select(func.count()).select_from(StockMovement).where(
            StockMovement.reference_code == "PN-INSPECTION-QA",
            StockMovement.movement_type == "inbound",
        )
        assert orm.session.scalar(movement_count) == 1

    repeated = client.post(
        f"/api/inbound-receipts/{receipt_id}/confirm",
        headers=auth_headers(csrf),
    )
    assert repeated.status_code == 200
    assert repeated.get_json()["already_completed"] is True
    with app.app_context():
        assert orm.session.scalar(
            select(func.count()).select_from(StockMovement).where(
                StockMovement.reference_code == "PN-INSPECTION-QA",
                StockMovement.movement_type == "inbound",
            )
        ) == 1


def test_receipt_accepts_decimal_quantity_and_rejects_bad_date_and_nonfinite(
    client, manager_login
):
    _, csrf = manager_login
    headers = auth_headers(csrf)
    valid = client.post(
        "/api/inbound-receipts",
        json={
            "code": "PN-DECIMAL-QA",
            "partner_id": 1,
            "warehouse_id": 1,
            "items": [
                {
                    "inventory_id": 1,
                    "quantity": 1.5,
                    "pallet_id": "PALLET-DECIMAL-QA",
                    "expiry_date": "2027-12-31",
                }
            ],
        },
        headers=headers,
    )
    assert valid.status_code == 201
    assert (
        client.get(
            f"/api/inbound-receipts/{valid.get_json()['id']}"
        ).get_json()["item"]["items"][0]["quantity"]
        == 1.5
    )

    for code, quantity, expiry in (
        ("PN-NAN-QA", "NaN", "2027-12-31"),
        ("PN-INF-QA", "Infinity", "2027-12-31"),
        ("PN-BAD-DATE-QA", 1, "2027-99-99"),
    ):
        response = client.post(
            "/api/inbound-receipts",
            json={
                "code": code,
                "partner_id": 1,
                "warehouse_id": 1,
                "items": [
                    {
                        "inventory_id": 1,
                        "quantity": quantity,
                        "pallet_id": f"PALLET-{code}",
                        "expiry_date": expiry,
                    }
                ],
            },
            headers=headers,
        )
        assert response.status_code == 422
        assert response.get_json()["error"]["code"] == "validation_error"


def test_malformed_json_returns_structured_validation_error(client, admin_login):
    _, csrf = admin_login
    response = client.post(
        "/api/products",
        data="{",
        content_type="application/json",
        headers=auth_headers(csrf),
    )
    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "validation_error"
    assert response.get_json()["error"]["fields"]


def test_report_ui_and_csv_share_all_movement_filters(app, client, manager_login):
    with app.app_context():
        orm.session.add(
            StockMovement(
                inventory_id=1,
                movement_type="outbound",
                reference_code="PX-2026-001",
                quantity_change=Decimal("-1"),
                balance_after=Decimal("1249"),
                pallet_id="REPORT-FILTER-QA",
                reason="Regression test bộ lọc báo cáo",
                created_by=1,
            )
        )
        orm.session.commit()

    page = client.get("/reports")
    assert page.status_code == 200
    assert b'name="product_id"' in page.data
    assert b'name="customer_id"' in page.data
    script = client.get("/static/app.js").get_data(as_text=True)
    assert "elements.product_id" in script
    assert "elements.customer_id" in script

    matching_query = (
        "from=2000-01-01&to=2100-01-01&warehouse_id=1"
        "&product_id=1&customer_id=1"
    )
    summary = client.get(f"/api/reports/summary?{matching_query}")
    assert summary.status_code == 200
    assert [item["reference_code"] for item in summary.get_json()["movements"]] == [
        "PX-2026-001"
    ]
    exported = client.get(f"/api/reports/export.csv?{matching_query}")
    assert exported.status_code == 200
    assert "PX-2026-001" in exported.get_data(as_text=True)

    for exclusion in (
        "from=2100-01-01",
        "to=2000-01-01",
        "warehouse_id=999999",
        "product_id=999999",
        "customer_id=999999",
    ):
        content = client.get(
            f"/api/reports/export.csv?{exclusion}"
        ).get_data(as_text=True)
        assert "PX-2026-001" not in content


@pytest.mark.sqlserver
def test_concurrent_outbound_confirmations_cannot_oversell(app, client):
    if not os.environ.get("TEST_DATABASE_URL", "").startswith("mssql"):
        pytest.skip("Row-lock concurrency contract is verified by the SQL Server CI job.")

    _, csrf = login(client, "cs", "Cs@123456")
    receipt_ids = []
    for suffix in ("A", "B"):
        response = client.post(
            "/api/outbound-receipts",
            json={
                "code": f"PX-CONCURRENT-{suffix}",
                "partner_id": 1,
                "warehouse_id": 1,
                "request_email": "kho@minhphat.vn",
                "status": "pending",
                "items": [{"inventory_id": 1, "quantity": 800}],
            },
            headers=auth_headers(csrf),
        )
        assert response.status_code == 201
        receipt_ids.append(response.get_json()["id"])

    barrier = threading.Barrier(2)

    def confirm(receipt_id):
        worker = app.test_client()
        _, worker_csrf = login(worker, "warehouse", "Kho@12345")
        barrier.wait(timeout=10)
        return worker.post(
            f"/api/outbound-receipts/{receipt_id}/confirm",
            headers=auth_headers(worker_csrf),
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = sorted(pool.map(confirm, receipt_ids))
    assert statuses == [200, 409]

    with app.app_context():
        product = orm.session.get(Product, 1)
        assert product.quantity == Decimal("450")
        assert product.quantity >= 0
        assert_stock_invariant(product.id)
        assert orm.session.scalar(
            select(func.count()).select_from(StockMovement).where(
                StockMovement.reference_code.in_(
                    ("PX-CONCURRENT-A", "PX-CONCURRENT-B")
                )
            )
        ) == 1
