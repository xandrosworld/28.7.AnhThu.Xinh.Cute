from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from app.extensions import db
from app.models import InventoryLot, Product, Receipt, ReceiptItem
from app.services import (
    DomainError,
    assert_stock_invariant,
    picking_list,
    quantity,
)


def _outbound(product, amount, code):
    receipt = Receipt(
        code=code,
        receipt_type="outbound",
        partner_id=1,
        customer_id=1,
        partner_name="Công ty Minh Phát",
        warehouse_id=product.warehouse_id,
        request_email="kho@minhphat.vn",
        status="pending",
        created_by=1,
    )
    db.session.add(receipt)
    db.session.flush()
    db.session.add(
        ReceiptItem(
            receipt_id=receipt.id,
            inventory_id=product.id,
            quantity=Decimal(str(amount)),
            accepted_quantity=Decimal("0"),
            pallet_id="",
            barcode="",
        )
    )
    db.session.flush()
    return receipt


def test_decimal_quantity_rejects_non_finite_and_rounds_consistently(app):
    assert quantity("1.2344") == Decimal("1.234")
    assert quantity("1.2346") == Decimal("1.235")
    for value in ("NaN", "Infinity", "-Infinity", None, "not-a-number"):
        with pytest.raises(DomainError):
            quantity(value)


def test_picking_is_fefo_then_fifo_and_excludes_expired_lots(app):
    with app.app_context():
        product = db.session.get(Product, 1)
        now = datetime(2024, 1, 1, 8, 0, 0)
        lots = [
            InventoryLot(
                product_id=product.id,
                warehouse_id=product.warehouse_id,
                unit=product.unit,
                pallet_id="EXPIRED-QA",
                quantity=Decimal("20"),
                expiry_date=date.today() - timedelta(days=1),
                received_at=now,
                status="active",
            ),
            InventoryLot(
                product_id=product.id,
                warehouse_id=product.warehouse_id,
                unit=product.unit,
                pallet_id="FEFO-FIRST-QA",
                quantity=Decimal("3"),
                expiry_date=date.today() + timedelta(days=5),
                received_at=now + timedelta(days=2),
                status="active",
            ),
            InventoryLot(
                product_id=product.id,
                warehouse_id=product.warehouse_id,
                unit=product.unit,
                pallet_id="FEFO-SECOND-QA",
                quantity=Decimal("10"),
                expiry_date=date.today() + timedelta(days=30),
                received_at=now,
                status="active",
            ),
        ]
        db.session.add_all(lots)
        product.quantity += Decimal("33")
        receipt = _outbound(product, 6, "PX-FEFO-QA")
        db.session.commit()

        _, picks = picking_list(receipt.id)
        assert [item["pallet_id"] for item in picks] == [
            "FEFO-FIRST-QA",
            "FEFO-SECOND-QA",
        ]
        assert [item["quantity"] for item in picks] == [3, 3]
        assert "EXPIRED-QA" not in {item["pallet_id"] for item in picks}


def test_picking_uses_received_order_for_lots_without_expiry(app):
    with app.app_context():
        product = db.session.get(Product, 3)
        lots = [
            InventoryLot(
                product_id=product.id,
                warehouse_id=product.warehouse_id,
                unit=product.unit,
                pallet_id="FIFO-OLDER-QA",
                quantity=Decimal("2"),
                received_at=datetime(2020, 1, 1),
                status="active",
            ),
            InventoryLot(
                product_id=product.id,
                warehouse_id=product.warehouse_id,
                unit=product.unit,
                pallet_id="FIFO-NEWER-QA",
                quantity=Decimal("4"),
                received_at=datetime(2021, 1, 1),
                status="active",
            ),
        ]
        db.session.add_all(lots)
        product.quantity = Decimal("6")
        receipt = _outbound(product, 3, "PX-FIFO-QA")
        db.session.commit()

        _, picks = picking_list(receipt.id)
        assert [item["pallet_id"] for item in picks] == [
            "FIFO-OLDER-QA",
            "FIFO-NEWER-QA",
        ]
        assert [item["quantity"] for item in picks] == [2, 1]


def test_expired_quantity_is_not_available_for_outbound(app):
    with app.app_context():
        product = db.session.get(Product, 3)
        db.session.add(
            InventoryLot(
                product_id=product.id,
                warehouse_id=product.warehouse_id,
                unit=product.unit,
                pallet_id="ONLY-EXPIRED-QA",
                quantity=Decimal("5"),
                expiry_date=date.today() - timedelta(days=1),
                status="active",
            )
        )
        product.quantity = Decimal("5")
        receipt = _outbound(product, 1, "PX-EXPIRED-QA")
        db.session.commit()

        with pytest.raises(DomainError, match="không đủ tồn"):
            picking_list(receipt.id)


def test_lot_aggregate_invariant_detects_drift(app):
    with app.app_context():
        product = db.session.get(Product, 1)
        assert_stock_invariant(product.id)
        product.quantity += Decimal("0.001")
        db.session.flush()
        with pytest.raises(DomainError, match="không khớp"):
            assert_stock_invariant(product.id)
