"""enforce role/unit linkage and inbound inspection

Revision ID: b72f4d8a10c3
Revises: 9f4581f6e261
Create Date: 2026-07-28
"""

from alembic import context, op
import sqlalchemy as sa


revision = "b72f4d8a10c3"
down_revision = "9f4581f6e261"
branch_labels = None
depends_on = None


def _set_nullable(table_name, column_name, index_name, nullable):
    """Change nullability without leaving SQL Server indexes in the way."""
    if context.get_context().dialect.name == "mssql":
        op.drop_index(index_name, table_name=table_name)
        op.alter_column(
            table_name,
            column_name,
            existing_type=sa.Integer(),
            nullable=nullable,
        )
        op.create_index(index_name, table_name, [column_name], unique=False)
        return

    with op.batch_alter_table(table_name) as batch_op:
        batch_op.alter_column(
            column_name, existing_type=sa.Integer(), nullable=nullable
        )


def upgrade():
    with op.batch_alter_table("roles") as batch_op:
        batch_op.add_column(
            sa.Column(
                "status", sa.Unicode(16), nullable=False, server_default="active"
            )
        )
        batch_op.create_check_constraint(
            op.f("ck_roles_valid_status"), "status IN ('active','inactive')"
        )

    configured_roles = (
        ("ADMIN", "Quản trị viên", "Quản trị tài khoản và dữ liệu nền"),
        ("CS", "Chăm sóc khách hàng", "Lập và theo dõi phiếu nhập/xuất"),
        ("WAREHOUSE", "Nhân viên kho", "Kiểm nhận, xuất hàng và kiểm kê"),
        ("MANAGER", "Quản lý kho", "Vai trò tương thích cho dữ liệu cũ"),
        ("STAFF", "Nhân viên", "Vai trò tương thích cho dữ liệu cũ"),
    )
    if context.is_offline_mode():
        # Static SQL describes schema evolution only. Runtime backfill below is
        # intentionally online because it must inspect legacy free-text values.
        _set_nullable("users", "role_id", op.f("ix_users_role_id"), False)
        _set_nullable(
            "inventory", "unit_id", op.f("ix_inventory_unit_id"), False
        )
        return

    bind = op.get_bind()
    has_legacy_users = bind.execute(sa.text("SELECT COUNT(*) FROM users")).scalar()
    if has_legacy_users:
        for code, name, description in configured_roles:
            exists = bind.execute(
                sa.text("SELECT id FROM roles WHERE code=:code"), {"code": code}
            ).first()
            if not exists:
                bind.execute(
                    sa.text(
                        "INSERT INTO roles (code,name,description) "
                        "VALUES (:code,:name,:description)"
                    ),
                    {"code": code, "name": name, "description": description},
                )

    bind.execute(
        sa.text(
            "UPDATE users SET role_id=("
            "SELECT roles.id FROM roles WHERE roles.code=UPPER(users.role)"
            ") WHERE role_id IS NULL"
        )
    )

    units = bind.execute(
        sa.text("SELECT DISTINCT unit FROM inventory WHERE unit_id IS NULL")
    ).fetchall()
    for index, row in enumerate(units, start=1):
        label = str(row[0]).strip()
        unit = bind.execute(
            sa.text(
                "SELECT id FROM units "
                "WHERE LOWER(code)=LOWER(:value) OR LOWER(name)=LOWER(:value)"
            ),
            {"value": label},
        ).first()
        if not unit:
            code = f"LEGACY{index:03d}"
            bind.execute(
                sa.text(
                    "INSERT INTO units (code,name,allow_break_pack,status) "
                    "VALUES (:code,:name,:allow_break_pack,'active')"
                ),
                {"code": code, "name": label, "allow_break_pack": False},
            )
            unit = bind.execute(
                sa.text("SELECT id FROM units WHERE code=:code"), {"code": code}
            ).first()
        bind.execute(
            sa.text(
                "UPDATE inventory SET unit_id=:unit_id WHERE unit_id IS NULL "
                "AND LOWER(unit)=LOWER(:label)"
            ),
            {"unit_id": unit[0], "label": label},
        )

    # Completed historical receipts are preserved as already inspected.
    bind.execute(
        sa.text(
            "INSERT INTO inbound_inspections "
            "(receipt_item_id,accepted_quantity,rejected_quantity,issue_note,"
            " inspected_by,created_at,updated_at) "
            "SELECT ri.id,ri.accepted_quantity,ri.rejected_quantity,ri.issue_note,"
            " COALESCE(r.confirmed_by,r.created_by),CURRENT_TIMESTAMP,CURRENT_TIMESTAMP "
            "FROM receipt_items ri JOIN receipts r ON r.id=ri.receipt_id "
            "WHERE r.receipt_type='inbound' AND r.status='completed' "
            "AND NOT EXISTS (SELECT 1 FROM inbound_inspections ii "
            "WHERE ii.receipt_item_id=ri.id)"
        )
    )
    # Open legacy documents must go through the explicit inspection endpoint.
    bind.execute(
        sa.text(
            "UPDATE receipt_items SET accepted_quantity=0,rejected_quantity=0,"
            "issue_note='' WHERE id IN ("
            "SELECT ri.id FROM receipt_items ri JOIN receipts r ON r.id=ri.receipt_id "
            "WHERE r.receipt_type='inbound' AND r.status<>'completed' "
            "AND NOT EXISTS (SELECT 1 FROM inbound_inspections ii "
            "WHERE ii.receipt_item_id=ri.id))"
        )
    )

    _set_nullable("users", "role_id", op.f("ix_users_role_id"), False)
    _set_nullable("inventory", "unit_id", op.f("ix_inventory_unit_id"), False)


def downgrade():
    _set_nullable("inventory", "unit_id", op.f("ix_inventory_unit_id"), True)
    _set_nullable("users", "role_id", op.f("ix_users_role_id"), True)
    with op.batch_alter_table("roles") as batch_op:
        batch_op.drop_constraint(op.f("ck_roles_valid_status"), type_="check")
        batch_op.drop_column("status")
