import pytest


def _require_sqlite(app):
    if not app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite:///"):
        pytest.skip("CLI backup/restore is intentionally SQLite-only.")


def test_init_db_command(runner):
    result = runner.invoke(args=["init-db"])
    assert result.exit_code == 0
    assert "Đã khởi tạo" in result.output


def test_seed_command_is_safe_to_repeat(runner):
    first = runner.invoke(args=["seed-db"])
    second = runner.invoke(args=["seed-db"])
    assert first.exit_code == 0
    assert second.exit_code == 0
    assert "đã tồn tại" in first.output.lower()
    assert "đã tồn tại" in second.output.lower()


def test_sqlite_backup_restore_round_trip(app, runner, tmp_path):
    from app.db import get_db

    _require_sqlite(app)
    backup = tmp_path / "verified-backup.sqlite"
    created = runner.invoke(args=["backup-db", "--output", str(backup)])
    assert created.exit_code == 0, created.output
    assert backup.is_file()
    assert backup.stat().st_size > 0

    with app.app_context():
        database = get_db()
        database.execute(
            """
            INSERT INTO categories (code, name, description, status)
            VALUES (?, ?, ?, ?)
            """,
            ("AFTER_BACKUP", "Sau backup", "Phải biến mất sau restore", "active"),
        )
        database.commit()
        assert database.execute(
            "SELECT COUNT(*) FROM categories WHERE code=?", ("AFTER_BACKUP",)
        ).fetchone()[0] == 1

    refused = runner.invoke(args=["restore-db", str(backup)])
    assert refused.exit_code != 0
    assert "--yes" in refused.output

    restored = runner.invoke(args=["restore-db", str(backup), "--yes"])
    assert restored.exit_code == 0, restored.output

    with app.app_context():
        assert get_db().execute(
            "SELECT COUNT(*) FROM categories WHERE code=?", ("AFTER_BACKUP",)
        ).fetchone()[0] == 0


def test_backup_rejects_overwrite_and_restore_rejects_corruption(
    app, runner, tmp_path
):
    _require_sqlite(app)
    backup = tmp_path / "existing.sqlite"
    backup.write_bytes(b"do-not-overwrite")
    result = runner.invoke(args=["backup-db", "--output", str(backup)])
    assert result.exit_code != 0
    assert backup.read_bytes() == b"do-not-overwrite"

    corrupt = tmp_path / "corrupt.sqlite"
    corrupt.write_bytes(b"not a sqlite database")
    result = runner.invoke(args=["restore-db", str(corrupt), "--yes"])
    assert result.exit_code != 0
