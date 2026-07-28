"""Transactional warehouse services shared by HTTP and CLI entry points."""

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import case, func, or_, select

from .extensions import db
from .models import (
    InventoryLot,
    InventoryAdjustment,
    OutboundAllocation,
    Product,
    Receipt,
    ReceiptItem,
    StockMovement,
    Stocktake,
    StocktakeItem,
)


class DomainError(Exception):
    """Expected business-rule conflict (HTTP 409)."""


def _locked(statement, model):
    """Portable row lock; SQL Server ignores FOR UPDATE without this hint."""
    return statement.with_for_update().with_hint(
        model,
        "WITH (UPDLOCK, ROWLOCK, HOLDLOCK)",
        dialect_name="mssql",
    )


def quantity(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise DomainError("Số lượng không hợp lệ.") from exc
    if not result.is_finite():
        raise DomainError("Số lượng phải là một giá trị hữu hạn.")
    return result.quantize(Decimal("0.001"))


def _active_lots(product_id, warehouse_id, lock=False):
    statement = (
        select(InventoryLot)
        .where(
            InventoryLot.product_id == product_id,
            InventoryLot.warehouse_id == warehouse_id,
            InventoryLot.quantity > 0,
            InventoryLot.status == "active",
            or_(InventoryLot.expiry_date.is_(None), InventoryLot.expiry_date >= date.today()),
        )
        .order_by(
            case((InventoryLot.expiry_date.is_(None), 1), else_=0),
            InventoryLot.expiry_date.asc(),
            InventoryLot.received_at.asc(),
            InventoryLot.id.asc(),
        )
    )
    if lock:
        statement = _locked(statement, InventoryLot)
    return list(db.session.scalars(statement))


def assert_stock_invariant(product_id):
    aggregate = db.session.scalar(
        select(func.coalesce(func.sum(InventoryLot.quantity), 0)).where(
            InventoryLot.product_id == product_id
        )
    )
    product = db.session.get(Product, product_id)
    if product is None or quantity(product.quantity) != quantity(aggregate):
        raise DomainError(
            "Tổng tồn theo pallet không khớp tồn hàng hóa; cần đối soát trước khi tiếp tục."
        )


def available_quantity(product_id, warehouse_id):
    value = db.session.scalar(
        select(func.coalesce(func.sum(InventoryLot.quantity), 0)).where(
            InventoryLot.product_id == product_id,
            InventoryLot.warehouse_id == warehouse_id,
            InventoryLot.quantity > 0,
            InventoryLot.status == "active",
            or_(InventoryLot.expiry_date.is_(None), InventoryLot.expiry_date >= date.today()),
        )
    )
    return quantity(value)


def _decrease_lots(product, requested, receipt_item=None):
    remaining = quantity(requested)
    allocations = []
    for lot in _active_lots(product.id, product.warehouse_id, lock=True):
        if remaining <= 0:
            break
        taken = min(quantity(lot.quantity), remaining)
        lot.quantity = quantity(lot.quantity) - taken
        if lot.quantity == 0:
            lot.status = "depleted"
        if receipt_item is not None:
            allocations.append(
                OutboundAllocation(receipt_item_id=receipt_item.id, lot_id=lot.id, quantity=taken)
            )
        remaining -= taken
    if remaining > 0:
        raise DomainError(f"Tồn khả dụng của {product.sku} không đủ để xuất.")
    db.session.add_all(allocations)
    return allocations


def confirm_receipt(receipt_id, expected_type, actor_id):
    """Confirm once, locking document, product and lots in one transaction."""
    receipt = db.session.scalar(
        _locked(
            select(Receipt).where(
                Receipt.id == receipt_id, Receipt.receipt_type == expected_type
            ),
            Receipt,
        )
    )
    if receipt is None:
        raise LookupError
    if receipt.status == "completed":
        return receipt, True
    if receipt.status in {"cancelled", "rejected"}:
        raise DomainError("Không thể xác nhận phiếu đã hủy hoặc từ chối.")

    items = list(
        db.session.scalars(
            _locked(
                select(ReceiptItem)
                .where(ReceiptItem.receipt_id == receipt.id)
                .order_by(ReceiptItem.id),
                ReceiptItem,
            )
        )
    )
    if not items:
        raise DomainError("Phiếu không có dòng hàng.")

    # Lock and pre-validate every product before any mutation. This prevents a
    # later-line shortage from partially changing stock.
    products = {}
    requested = {}
    for item in items:
        product = db.session.scalar(
            _locked(select(Product).where(Product.id == item.inventory_id), Product)
        )
        if product is None or product.warehouse_id != receipt.warehouse_id:
            raise DomainError("Hàng hóa không thuộc kho của phiếu.")
        products[item.inventory_id] = product
        requested[item.inventory_id] = requested.get(item.inventory_id, Decimal("0")) + (
            quantity(item.accepted_quantity) if expected_type == "inbound"
            else quantity(item.quantity)
        )
    if expected_type == "outbound":
        for product_id, amount in requested.items():
            if quantity(products[product_id].quantity) < amount:
                raise DomainError(f"Tồn kho {products[product_id].sku} không đủ để xuất.")

    for item in items:
        product = products[item.inventory_id]
        amount = (
            quantity(item.accepted_quantity)
            if expected_type == "inbound"
            else quantity(item.quantity)
        )
        if expected_type == "inbound":
            if amount > quantity(item.quantity):
                raise DomainError("Số lượng chấp nhận vượt số lượng chứng từ.")
            if amount > 0:
                pallet = item.pallet_id or f"{receipt.code}-{item.id:04d}"
                lot = InventoryLot(
                    product_id=product.id,
                    warehouse_id=receipt.warehouse_id,
                    unit=product.unit,
                    pallet_id=pallet,
                    barcode=item.barcode or None,
                    quantity=amount,
                    expiry_date=item.expiry_date,
                    status="active",
                )
                db.session.add(lot)
                db.session.flush()
                item.pallet_id = pallet
            change = amount
        else:
            _decrease_lots(product, amount, item)
            change = -amount

        product.quantity = quantity(product.quantity) + change
        if product.quantity < 0:
            raise DomainError(f"Tồn kho {product.sku} không đủ để xuất.")
        db.session.flush()
        db.session.add(
            StockMovement(
                inventory_id=product.id,
                movement_type=expected_type,
                reference_code=receipt.code,
                quantity_change=change,
                balance_after=product.quantity,
                pallet_id=item.pallet_id or f"LINE-{item.id}",
                created_by=actor_id,
            )
        )

    receipt.status = "completed"
    receipt.confirmed_by = actor_id
    receipt.confirmed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.session.flush()
    for product_id in products:
        assert_stock_invariant(product_id)
    return receipt, False


def picking_list(receipt_id):
    receipt = db.session.scalar(
        select(Receipt).where(Receipt.id == receipt_id, Receipt.receipt_type == "outbound")
    )
    if receipt is None:
        raise LookupError
    result = []
    for line in db.session.scalars(
        select(ReceiptItem).where(ReceiptItem.receipt_id == receipt.id).order_by(ReceiptItem.id)
    ):
        product = db.session.get(Product, line.inventory_id)
        remaining = quantity(line.quantity)
        for lot in _active_lots(line.inventory_id, receipt.warehouse_id):
            if remaining <= 0:
                break
            take = min(quantity(lot.quantity), remaining)
            result.append(
                {
                    "receipt_item_id": line.id,
                    "inventory_id": line.inventory_id,
                    "sku": product.sku if product else "",
                    "name": product.name if product else "",
                    "location": product.location if product else "",
                    "lot_id": lot.id,
                    "pallet_id": lot.pallet_id,
                    "barcode": lot.barcode or "",
                    "expiry_date": lot.expiry_date.isoformat() if lot.expiry_date else None,
                    "quantity": int(take) if take == take.to_integral_value() else float(take),
                    "unit": lot.unit,
                }
            )
            remaining -= take
        if remaining > 0:
            raise DomainError("Một hoặc nhiều mặt hàng không đủ tồn khả dụng.")
    return receipt, result


def set_stock(product_id, new_quantity, actor_id, reference_code, reason, expected=None):
    product = db.session.scalar(
        _locked(select(Product).where(Product.id == product_id), Product)
    )
    if product is None:
        raise LookupError
    old = quantity(product.quantity)
    target = quantity(new_quantity)
    if target < 0:
        raise DomainError("Tồn kho không được âm.")
    if expected is not None and old != quantity(expected):
        raise DomainError(
            "Tồn kho đã thay đổi sau khi lập phiếu; vui lòng tạo phiếu kiểm kê mới."
        )
    change = target - old
    if change > 0:
        lot = InventoryLot(
            product_id=product.id,
            warehouse_id=product.warehouse_id,
            unit=product.unit,
            pallet_id=f"ADJ-{reference_code}-{product.id}",
            quantity=change,
            status="active",
        )
        db.session.add(lot)
    elif change < 0:
        _decrease_lots(product, -change)
    product.quantity = target
    db.session.flush()
    db.session.add(
        StockMovement(
            inventory_id=product.id,
            movement_type="stocktake" if reference_code.startswith("KK-") else "adjustment",
            reference_code=reference_code,
            quantity_change=change,
            balance_after=target,
            pallet_id=f"ADJ-{product.id}",
            reason=reason,
            created_by=actor_id,
        )
    )
    db.session.flush()
    assert_stock_invariant(product.id)
    return product, old, change


def confirm_stocktake(stocktake_id, actor_id):
    stocktake = db.session.scalar(
        _locked(select(Stocktake).where(Stocktake.id == stocktake_id), Stocktake)
    )
    if stocktake is None:
        raise LookupError
    if stocktake.status == "completed":
        return stocktake, True
    if stocktake.status == "cancelled":
        raise DomainError("Không thể xác nhận phiếu đã hủy.")
    items = list(
        db.session.scalars(
            _locked(
                select(StocktakeItem)
                .where(StocktakeItem.stocktake_id == stocktake.id)
                .order_by(StocktakeItem.id),
                StocktakeItem,
            )
        )
    )
    # Validate every stale snapshot before applying any change.
    products = {
        item.inventory_id: db.session.scalar(
            _locked(select(Product).where(Product.id == item.inventory_id), Product)
        )
        for item in items
    }
    for item in items:
        product = products[item.inventory_id]
        if product is None or quantity(product.quantity) != quantity(item.system_quantity):
            raise DomainError(
                "Tồn kho đã thay đổi sau khi lập phiếu; vui lòng tạo phiếu kiểm kê mới."
            )
    for item in items:
        set_stock(
            item.inventory_id,
            item.counted_quantity,
            actor_id,
            stocktake.code,
            item.reason,
            expected=item.system_quantity,
        )
    stocktake.status = "completed"
    stocktake.confirmed_by = actor_id
    stocktake.confirmed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    return stocktake, False
