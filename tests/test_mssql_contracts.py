from sqlalchemy import select
from sqlalchemy.dialects import mssql

from app.db import _mssql_insert_with_identity, _mssql_sql
from app.models import InventoryLot, Product, Receipt
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
