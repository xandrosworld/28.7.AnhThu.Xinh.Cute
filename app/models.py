"""Relational WMS model.

The legacy UI calls products ``inventory``.  The table name is retained for
backwards compatibility while stock is additionally represented by immutable
lot movements.  Aggregate ``inventory.quantity`` is updated in the same
transaction and is verified by :func:`app.services.assert_stock_invariant`.
"""

from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, Index, UniqueConstraint, event

from .extensions import db


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TimestampMixin:
    created_at = db.Column(
        db.DateTime, nullable=False, default=utcnow,
        server_default=db.func.current_timestamp(),
    )
    updated_at = db.Column(
        db.DateTime, nullable=False, default=utcnow, onupdate=utcnow,
        server_default=db.func.current_timestamp(),
    )


class ActiveMixin:
    status = db.Column(
        db.Unicode(16), nullable=False, default="active",
        server_default="active", index=True,
    )


class Role(db.Model):
    __tablename__ = "roles"
    __table_args__ = (
        CheckConstraint("status IN ('active','inactive')", name="valid_status"),
    )
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.Unicode(32), nullable=False, unique=True)
    name = db.Column(db.Unicode(100), nullable=False)
    description = db.Column(db.Unicode(255), nullable=False, default="")
    status = db.Column(
        db.Unicode(16), nullable=False, default="active", server_default="active"
    )


class User(TimestampMixin, db.Model):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "role IN ('admin','cs','warehouse','manager','staff')",
            name="valid_role",
        ),
        CheckConstraint("status IN ('active','locked')", name="valid_status"),
    )
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.Unicode(30), nullable=False, unique=True)
    password_hash = db.Column(db.Unicode(255), nullable=False)
    full_name = db.Column(db.Unicode(150), nullable=False)
    email = db.Column(db.Unicode(255), nullable=False, unique=True)
    phone = db.Column(db.Unicode(30), nullable=False, default="", server_default="")
    role = db.Column(db.Unicode(16), nullable=False)
    role_id = db.Column(db.Integer, db.ForeignKey("roles.id"), nullable=False, index=True)
    status = db.Column(db.Unicode(16), nullable=False, default="active")
    avatar_initials = db.Column(db.Unicode(8), nullable=False, default="", server_default="")


class Category(TimestampMixin, ActiveMixin, db.Model):
    __tablename__ = "categories"
    __table_args__ = (CheckConstraint("status IN ('active','inactive')", name="valid_status"),)
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.Unicode(20), nullable=False, unique=True)
    name = db.Column(db.Unicode(150), nullable=False, unique=True)
    description = db.Column(db.Unicode(500), nullable=False, default="", server_default="")


class Unit(ActiveMixin, db.Model):
    __tablename__ = "units"
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.Unicode(20), nullable=False, unique=True)
    name = db.Column(db.Unicode(80), nullable=False)
    allow_break_pack = db.Column(db.Boolean, nullable=False, default=False)


class Warehouse(ActiveMixin, db.Model):
    __tablename__ = "warehouses"
    __table_args__ = (CheckConstraint("status IN ('active','inactive')", name="valid_status"),)
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.Unicode(20), nullable=False, unique=True)
    name = db.Column(db.Unicode(150), nullable=False)
    address = db.Column(db.Unicode(500), nullable=False, default="", server_default="")


class Customer(TimestampMixin, ActiveMixin, db.Model):
    __tablename__ = "customers"
    __table_args__ = (CheckConstraint("status IN ('active','inactive')", name="valid_status"),)
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.Unicode(20), nullable=False, unique=True)
    name = db.Column(db.Unicode(200), nullable=False)
    email = db.Column(db.Unicode(255), nullable=False, default="", server_default="")
    phone = db.Column(db.Unicode(30), nullable=False, default="", server_default="")
    # Compatibility projection for the existing frontend.
    contract_emails = db.Column(db.Unicode, nullable=False, default="", server_default="")


class CustomerContractEmail(TimestampMixin, ActiveMixin, db.Model):
    __tablename__ = "customer_contract_emails"
    __table_args__ = (
        UniqueConstraint(
            "customer_id", "normalized_email",
            name="uq_customer_contract_emails_customer_email",
        ),
        CheckConstraint("status IN ('active','inactive')", name="valid_status"),
    )
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(
        db.Integer, db.ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email = db.Column(db.Unicode(255), nullable=False)
    normalized_email = db.Column(db.Unicode(255), nullable=False, index=True)


class Supplier(TimestampMixin, ActiveMixin, db.Model):
    __tablename__ = "suppliers"
    __table_args__ = (CheckConstraint("status IN ('active','inactive')", name="valid_status"),)
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.Unicode(20), nullable=False, unique=True)
    name = db.Column(db.Unicode(200), nullable=False)
    email = db.Column(db.Unicode(255), nullable=False, default="", server_default="")
    phone = db.Column(db.Unicode(30), nullable=False, default="", server_default="")
    address = db.Column(db.Unicode(500), nullable=False, default="", server_default="")


class Product(TimestampMixin, ActiveMixin, db.Model):
    __tablename__ = "inventory"
    __table_args__ = (
        CheckConstraint("quantity >= 0", name="quantity_nonnegative"),
        CheckConstraint("min_quantity >= 0", name="minimum_nonnegative"),
        CheckConstraint("status IN ('active','inactive')", name="valid_status"),
        Index("ix_inventory_name", "name"),
        Index(
            "uq_inventory_barcode_not_null",
            "barcode",
            unique=True,
            sqlite_where=db.text("barcode IS NOT NULL"),
            mssql_where=db.text("barcode IS NOT NULL"),
        ),
    )
    id = db.Column(db.Integer, primary_key=True)
    sku = db.Column(db.Unicode(40), nullable=False, unique=True)
    barcode = db.Column(db.Unicode(100), nullable=True)
    name = db.Column(db.Unicode(200), nullable=False)
    category_id = db.Column(
        db.Integer, db.ForeignKey("categories.id"), nullable=False, index=True
    )
    warehouse_id = db.Column(
        db.Integer, db.ForeignKey("warehouses.id"), nullable=False, index=True
    )
    unit = db.Column(db.Unicode(40), nullable=False)
    unit_id = db.Column(db.Integer, db.ForeignKey("units.id"), nullable=False, index=True)
    quantity = db.Column(db.Numeric(18, 3), nullable=False, default=0, server_default="0")
    min_quantity = db.Column(db.Numeric(18, 3), nullable=False, default=0, server_default="0")
    location = db.Column(db.Unicode(100), nullable=False, default="", server_default="")
    description = db.Column(db.Unicode(1000), nullable=False, default="", server_default="")


class InventoryLot(TimestampMixin, ActiveMixin, db.Model):
    __tablename__ = "inventory_lots"
    __table_args__ = (
        CheckConstraint("quantity >= 0", name="quantity_nonnegative"),
        UniqueConstraint("pallet_id", name="uq_inventory_lots_pallet_id"),
        CheckConstraint("status IN ('active','depleted','quarantined')", name="valid_status"),
        Index("ix_lot_allocation", "product_id", "warehouse_id", "expiry_date", "received_at"),
        Index(
            "uq_inventory_lots_barcode_not_null",
            "barcode",
            unique=True,
            sqlite_where=db.text("barcode IS NOT NULL"),
            mssql_where=db.text("barcode IS NOT NULL"),
        ),
    )
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(
        db.Integer, db.ForeignKey("inventory.id"), nullable=False
    )
    warehouse_id = db.Column(
        db.Integer, db.ForeignKey("warehouses.id"), nullable=False
    )
    unit = db.Column(db.Unicode(40), nullable=False)
    pallet_id = db.Column(db.Unicode(100), nullable=False)
    barcode = db.Column(db.Unicode(100), nullable=True)
    quantity = db.Column(db.Numeric(18, 3), nullable=False, default=0)
    expiry_date = db.Column(db.Date, nullable=True)
    received_at = db.Column(
        db.DateTime, nullable=False, default=utcnow,
        server_default=db.func.current_timestamp(),
    )


class Receipt(TimestampMixin, db.Model):
    __tablename__ = "receipts"
    __table_args__ = (
        CheckConstraint("receipt_type IN ('inbound','outbound')", name="valid_type"),
        CheckConstraint(
            "status IN ('draft','pending','picking','completed','rejected','cancelled')",
            name="valid_status",
        ),
        CheckConstraint(
            "(receipt_type='inbound' AND supplier_id IS NOT NULL AND customer_id IS NULL) "
            "OR (receipt_type='outbound' AND customer_id IS NOT NULL AND supplier_id IS NULL)",
            name="partner_matches_type",
        ),
        Index("ix_receipts_type_status", "receipt_type", "status"),
    )
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.Unicode(40), nullable=False, unique=True)
    receipt_type = db.Column(db.Unicode(16), nullable=False)
    partner_id = db.Column(db.Integer, nullable=False, index=True)
    customer_id = db.Column(
        db.Integer, db.ForeignKey("customers.id"), nullable=True
    )
    supplier_id = db.Column(
        db.Integer, db.ForeignKey("suppliers.id"), nullable=True
    )
    partner_name = db.Column(db.Unicode(200), nullable=False)
    warehouse_id = db.Column(
        db.Integer, db.ForeignKey("warehouses.id"), nullable=False
    )
    request_email = db.Column(db.Unicode(255), nullable=False, default="", server_default="")
    container_no = db.Column(db.Unicode(100), nullable=False, default="", server_default="")
    seal_no = db.Column(db.Unicode(100), nullable=False, default="", server_default="")
    status = db.Column(db.Unicode(16), nullable=False, default="draft")
    note = db.Column(db.Unicode(1000), nullable=False, default="", server_default="")
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    confirmed_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    confirmed_at = db.Column(db.DateTime)


class ReceiptItem(db.Model):
    __tablename__ = "receipt_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("accepted_quantity >= 0", name="accepted_nonnegative"),
        UniqueConstraint(
            "receipt_id", "inventory_id", "pallet_id",
            name="uq_receipt_items_receipt_product_pallet",
        ),
    )
    id = db.Column(db.Integer, primary_key=True)
    receipt_id = db.Column(
        db.Integer, db.ForeignKey("receipts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    inventory_id = db.Column(
        db.Integer, db.ForeignKey("inventory.id"), nullable=False
    )
    quantity = db.Column(db.Numeric(18, 3), nullable=False)
    accepted_quantity = db.Column(db.Numeric(18, 3), nullable=False, default=0, server_default="0")
    rejected_quantity = db.Column(db.Numeric(18, 3), nullable=False, default=0, server_default="0")
    pallet_id = db.Column(db.Unicode(100), nullable=False, default="", server_default="")
    barcode = db.Column(db.Unicode(100), nullable=False, default="", server_default="")
    expiry_date = db.Column(db.Date)
    issue_note = db.Column(db.Unicode(500), nullable=False, default="", server_default="")


@event.listens_for(Receipt, "before_insert")
@event.listens_for(Receipt, "before_update")
def _sync_receipt_partner_fk(mapper, connection, target):
    """Keep legacy ``partner_id`` writes referentially safe."""
    if target.receipt_type == "inbound":
        target.supplier_id = target.supplier_id or target.partner_id
        target.customer_id = None
    elif target.receipt_type == "outbound":
        target.customer_id = target.customer_id or target.partner_id
        target.supplier_id = None


class InboundInspection(TimestampMixin, db.Model):
    __tablename__ = "inbound_inspections"
    __table_args__ = (
        UniqueConstraint(
            "receipt_item_id", name="uq_inbound_inspections_receipt_item",
        ),
        CheckConstraint("accepted_quantity >= 0", name="accepted_nonnegative"),
        CheckConstraint("rejected_quantity >= 0", name="rejected_nonnegative"),
    )
    id = db.Column(db.Integer, primary_key=True)
    receipt_item_id = db.Column(
        db.Integer, db.ForeignKey("receipt_items.id", ondelete="CASCADE"), nullable=False
    )
    accepted_quantity = db.Column(db.Numeric(18, 3), nullable=False)
    rejected_quantity = db.Column(db.Numeric(18, 3), nullable=False, default=0)
    issue_note = db.Column(db.Unicode(500), nullable=False, default="")
    inspected_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)


class OutboundAllocation(TimestampMixin, db.Model):
    __tablename__ = "outbound_allocations"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        UniqueConstraint(
            "receipt_item_id", "lot_id",
            name="uq_outbound_allocations_line_lot",
        ),
    )
    id = db.Column(db.Integer, primary_key=True)
    receipt_item_id = db.Column(
        db.Integer, db.ForeignKey("receipt_items.id", ondelete="CASCADE"), nullable=False
    )
    lot_id = db.Column(db.Integer, db.ForeignKey("inventory_lots.id"), nullable=False)
    quantity = db.Column(db.Numeric(18, 3), nullable=False)


class StockMovement(db.Model):
    __tablename__ = "stock_movements"
    __table_args__ = (
        CheckConstraint("movement_type IN ('inbound','outbound','stocktake','adjustment')", name="valid_type"),
        CheckConstraint("balance_after >= 0", name="balance_nonnegative"),
        UniqueConstraint(
            "movement_type", "reference_code", "inventory_id", "pallet_id",
            name="uq_stock_movements_idempotency",
        ),
        Index("ix_movement_created", "created_at"),
    )
    id = db.Column(db.Integer, primary_key=True)
    inventory_id = db.Column(db.Integer, db.ForeignKey("inventory.id"), nullable=False)
    lot_id = db.Column(db.Integer, db.ForeignKey("inventory_lots.id"), nullable=True)
    movement_type = db.Column(db.Unicode(20), nullable=False)
    reference_code = db.Column(db.Unicode(40), nullable=False)
    quantity_change = db.Column(db.Numeric(18, 3), nullable=False)
    balance_after = db.Column(db.Numeric(18, 3), nullable=False)
    pallet_id = db.Column(db.Unicode(100), nullable=False, default="", server_default="")
    reason = db.Column(db.Unicode(500), nullable=False, default="", server_default="")
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(
        db.DateTime, nullable=False, default=utcnow,
        server_default=db.func.current_timestamp(),
    )


class Stocktake(TimestampMixin, db.Model):
    __tablename__ = "stocktakes"
    __table_args__ = (
        CheckConstraint("status IN ('draft','completed','cancelled')", name="valid_status"),
    )
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.Unicode(40), nullable=False, unique=True)
    warehouse_id = db.Column(db.Integer, db.ForeignKey("warehouses.id"), nullable=False)
    status = db.Column(db.Unicode(16), nullable=False, default="draft", server_default="draft")
    note = db.Column(db.Unicode(1000), nullable=False, default="", server_default="")
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    confirmed_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    confirmed_at = db.Column(db.DateTime)


class StocktakeItem(db.Model):
    __tablename__ = "stocktake_items"
    __table_args__ = (
        CheckConstraint("system_quantity >= 0", name="system_nonnegative"),
        CheckConstraint("counted_quantity >= 0", name="counted_nonnegative"),
        UniqueConstraint(
            "stocktake_id", "inventory_id",
            name="uq_stocktake_items_stocktake_product",
        ),
    )
    id = db.Column(db.Integer, primary_key=True)
    stocktake_id = db.Column(db.Integer, db.ForeignKey("stocktakes.id", ondelete="CASCADE"), nullable=False)
    inventory_id = db.Column(db.Integer, db.ForeignKey("inventory.id"), nullable=False)
    system_quantity = db.Column(db.Numeric(18, 3), nullable=False)
    counted_quantity = db.Column(db.Numeric(18, 3), nullable=False)
    reason = db.Column(db.Unicode(500), nullable=False, default="", server_default="")


class InventoryAdjustment(db.Model):
    __tablename__ = "inventory_adjustments"
    __table_args__ = (
        CheckConstraint("old_quantity >= 0", name="old_nonnegative"),
        CheckConstraint("new_quantity >= 0", name="new_nonnegative"),
    )
    id = db.Column(db.Integer, primary_key=True)
    inventory_id = db.Column(db.Integer, db.ForeignKey("inventory.id"), nullable=False, index=True)
    old_quantity = db.Column(db.Numeric(18, 3), nullable=False)
    new_quantity = db.Column(db.Numeric(18, 3), nullable=False)
    difference = db.Column(db.Numeric(18, 3), nullable=False)
    reason = db.Column(db.Unicode(200), nullable=False)
    note = db.Column(db.Unicode(500), nullable=False, default="", server_default="")
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(
        db.DateTime, nullable=False, default=utcnow, index=True,
        server_default=db.func.current_timestamp(),
    )


class AuditLog(db.Model):
    __tablename__ = "audit_logs"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), index=True)
    action = db.Column(db.Unicode(80), nullable=False)
    entity_type = db.Column(db.Unicode(80), nullable=False)
    entity_id = db.Column(db.Integer)
    details = db.Column(db.Unicode, nullable=False, default="{}", server_default="{}")
    ip_address = db.Column(db.Unicode(64), nullable=False, default="", server_default="")
    created_at = db.Column(
        db.DateTime, nullable=False, default=utcnow, index=True,
        server_default=db.func.current_timestamp(),
    )


# Semantic aliases used by services and documentation.
InboundReceipt = Receipt
OutboundReceipt = Receipt
