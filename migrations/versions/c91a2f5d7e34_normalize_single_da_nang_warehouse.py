"""normalize existing data to the single Da Nang warehouse

Revision ID: c91a2f5d7e34
Revises: b72f4d8a10c3
Create Date: 2026-07-29
"""

from alembic import context, op
import sqlalchemy as sa


revision = "c91a2f5d7e34"
down_revision = "b72f4d8a10c3"
branch_labels = None
depends_on = None


WAREHOUSE_TABLES = ("inventory", "inventory_lots", "receipts", "stocktakes")


def upgrade():
    if context.is_offline_mode():
        return

    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "warehouses" not in tables:
        return

    warehouse_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM warehouses")
    ).scalar_one()
    # On a fresh installation seed-db creates the canonical warehouse after
    # migrations have completed.
    if not warehouse_count:
        return

    da_nang_id = bind.execute(
        sa.text(
            """SELECT id FROM warehouses
               WHERE UPPER(code) = 'DN'
               ORDER BY id"""
        )
    ).scalar()
    if da_nang_id is None:
        bind.execute(
            sa.text(
                """INSERT INTO warehouses (code, name, address, status)
                   VALUES (:code, :name, :address, :status)"""
            ),
            {
                "code": "DN",
                "name": "Kho Đà Nẵng",
                "address": "KCN Hòa Khánh, Đà Nẵng",
                "status": "active",
            },
        )
        da_nang_id = bind.execute(
            sa.text("SELECT id FROM warehouses WHERE code = 'DN'")
        ).scalar_one()

    bind.execute(
        sa.text(
            """UPDATE warehouses
               SET code=:code, name=:name, address=:address, status=:status
               WHERE id=:warehouse_id"""
        ),
        {
            "code": "DN",
            "name": "Kho Đà Nẵng",
            "address": "KCN Hòa Khánh, Đà Nẵng",
            "status": "active",
            "warehouse_id": da_nang_id,
        },
    )
    for table_name in WAREHOUSE_TABLES:
        if table_name in tables:
            bind.execute(
                sa.text(
                    f"UPDATE {table_name} SET warehouse_id=:warehouse_id "
                    "WHERE warehouse_id<>:warehouse_id"
                ),
                {"warehouse_id": da_nang_id},
            )
    if "inventory" in tables:
        bind.execute(sa.text("UPDATE inventory SET location=''"))
    bind.execute(
        sa.text("DELETE FROM warehouses WHERE id<>:warehouse_id"),
        {"warehouse_id": da_nang_id},
    )


def downgrade():
    # The previous warehouse assignment cannot be reconstructed without
    # inventing historical data. Schema compatibility is unchanged.
    pass