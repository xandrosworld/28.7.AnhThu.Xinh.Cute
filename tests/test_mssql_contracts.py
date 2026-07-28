from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects import mssql
from sqlalchemy.schema import CreateTable

from app.api import USER_ACTIVITY_COUNT_SQL
from app.db import _mssql_insert_with_identity, _mssql_sql
from app.extensions import db as orm
from app.models import InboundInspection, InventoryLot, Product, Receipt, ReceiptItem
from app.services import _locked


def test_mssql_legacy_query_translation_preserves_parameter_order():
    statement, params = _mssql_sql(
        "SELECT * FROM inventory ORDER BY id LIMIT ? OFFSET ?",
        [10, 20],
    )
    assert statement.endswith("OFFSET ? ROWS FETCH NEXT ? ROWS ONLY")
    assert params == (20, 10)

    statement, params = _mssql_sql(
        "SELECT * FROM stock_movements WHERE date(created_at)>=date(?) "
        "ORDER BY id DESC LIMIT 30",
        ["2026-01-01"],
    )
    assert "CAST(created_at AS date)>=CAST(? AS date)" in statement
    assert statement.endswith("OFFSET 0 ROWS FETCH NEXT 30 ROWS ONLY")
    assert params == ("2026-01-01",)


def test_mssql_identity_projection_handles_multiline_insert():
    statement, changed = _mssql_insert_with_identity(
        """
        INSERT INTO receipts
            (code, receipt_type, partner_id)
        VALUES (?, ?, ?)
        """
    )
    assert changed is True
    assert "OUTPUT INSERTED.id VALUES" in " ".join(statement.split())


def test_sqlserver_critical_selects_compile_with_update_locks():
    for model in (Receipt, Product, InventoryLot):
        sql = str(
            _locked(select(model).where(model.id == 1), model).compile(
                dialect=mssql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )
        assert "WITH (UPDLOCK, ROWLOCK, HOLDLOCK)" in sql


def test_report_dates_are_validated_before_database_cast(client, admin_login):
    for path in (
        "/api/reports/summary?from=not-a-date",
        "/api/reports/summary?to=2026-02-30",
        "/api/reports/summary?from=2026-12-31&to=2026-01-01",
        "/api/reports/export.csv?from=2026-12-31&to=2026-01-01",
    ):
        response = client.get(path)
        assert response.status_code == 422
        payload = response.get_json()
        assert payload["error"]["code"] == "validation_error"
        assert payload["error"]["fields"]


def test_mssql_uses_default_no_action_instead_of_unsupported_restrict():
    product_ddl = str(CreateTable(Product.__table__).compile(dialect=mssql.dialect()))
    normalized = " ".join(product_ddl.upper().split())
    assert "FOREIGN KEY(CATEGORY_ID) REFERENCES CATEGORIES (ID)" in normalized
    assert "FOREIGN KEY(WAREHOUSE_ID) REFERENCES WAREHOUSES (ID)" in normalized
    assert "ON DELETE RESTRICT" not in normalized

    migration_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path("migrations/versions").glob("*.py")
    ).upper()
    assert "RESTRICT" not in migration_source


def test_user_activity_query_needs_no_union_pagination_translation():
    statement, params = _mssql_sql(USER_ACTIVITY_COUNT_SQL, [7] * 6)
    assert "UNION" not in statement.upper()
    assert "LIMIT" not in statement.upper()
    assert "ORDER BY" not in statement.upper()
    assert params == (7, 7, 7, 7, 7, 7)


def test_human_facing_columns_compile_as_nvarchar_for_sqlserver():
    ddl = "\n".join(
        str(CreateTable(model.__table__).compile(dialect=mssql.dialect()))
        for model in (Product, Receipt, ReceiptItem, InboundInspection)
    ).upper()
    assert "NAME NVARCHAR(200)" in ddl
    assert "NOTE NVARCHAR(1000)" in ddl
    assert ddl.count("ISSUE_NOTE NVARCHAR(500)") == 2
    assert "DESCRIPTION NVARCHAR(1000)" in ddl

    all_ddl = "\n".join(
        str(CreateTable(table).compile(dialect=mssql.dialect()))
        for table in orm.metadata.sorted_tables
    ).upper()
    assert " NTEXT" not in all_ddl
    assert " VARCHAR(" not in all_ddl
    assert " TEXT" not in all_ddl
    assert "CONTRACT_EMAILS NVARCHAR(MAX)" in all_ddl
    assert "DETAILS NVARCHAR(MAX)" in all_ddl


def test_sqlite_round_trips_vietnamese_business_text(db):
    expected = "Thiếu 01 kiện – vỏ móp, chờ đối soát"
    db.execute(
        "UPDATE receipt_items SET issue_note=? WHERE id=1",
        (expected,),
    )
    db.commit()
    actual = db.execute(
        "SELECT issue_note FROM receipt_items WHERE id=1"
    ).fetchone()[0]
    assert actual == expected
