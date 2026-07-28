import json
import os
import re
import shutil
import sys
from decimal import Decimal
from datetime import date, datetime
from pathlib import Path

import click
from flask import current_app, g
from flask.cli import with_appcontext
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash

from .extensions import db as orm


class HybridRow:
    """A tiny sqlite3.Row-compatible wrapper around SQLAlchemy rows."""

    __slots__ = ("_values", "_mapping")

    def __init__(self, row):
        raw = row._mapping
        self._mapping = {
            key: self._json_number(value) for key, value in raw.items()
        }
        self._values = tuple(self._mapping.values())

    @staticmethod
    def _json_number(value):
        if isinstance(value, Decimal):
            return int(value) if value == value.to_integral_value() else float(value)
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        return value

    def __getitem__(self, key):
        return self._values[key] if isinstance(key, int) else self._mapping[key]

    def __iter__(self):
        return iter(self._mapping)

    def __len__(self):
        return len(self._mapping)

    def keys(self):
        return self._mapping.keys()


class ResultAdapter:
    def __init__(self, result, lastrowid=None):
        self._result = result
        self.lastrowid = (
            lastrowid if lastrowid is not None else getattr(result, "lastrowid", None)
        )
        self.rowcount = getattr(result, "rowcount", -1)

    def fetchone(self):
        row = self._result.fetchone()
        return HybridRow(row) if row is not None else None

    def fetchall(self):
        return [HybridRow(row) for row in self._result.fetchall()]

    def __iter__(self):
        for row in self._result:
            yield HybridRow(row)


def _mssql_sql(sql, params):
    """Translate the small SQLite query dialect used by legacy read routes."""
    params = list(params)
    sql = re.sub(r"\s+COLLATE\s+NOCASE", "", sql, flags=re.IGNORECASE)
    sql = re.sub(
        r"date\(([^)]+)\)",
        r"CAST(\1 AS date)",
        sql,
        flags=re.IGNORECASE,
    )
    match = re.search(r"\s+LIMIT\s+(\?|\d+)(?:\s+OFFSET\s+(\?|\d+))?\s*$", sql, re.I)
    if not match:
        return sql, tuple(params)
    limit, offset = match.group(1), match.group(2)
    body = sql[: match.start()]
    if "ORDER BY" not in body.upper():
        body += " ORDER BY (SELECT NULL)"
    if limit == "?" and offset == "?" and len(params) >= 2:
        params[-2], params[-1] = params[-1], params[-2]
    return (
        f"{body} OFFSET {offset or 0} ROWS FETCH NEXT {limit} ROWS ONLY",
        tuple(params),
    )


def _mssql_insert_with_identity(sql):
    """Add SQL Server's portable identity projection to a single-row INSERT."""
    if not re.match(r"^\s*INSERT\s+INTO\b", sql, re.I):
        return sql, False
    if "OUTPUT INSERTED." in sql.upper():
        return sql, False
    statement, replacements = re.subn(
        r"(\bINSERT\s+INTO\s+[\[\]\w.]+\s*\([^)]*\))\s*(VALUES\b)",
        r"\1 OUTPUT INSERTED.id \2",
        sql,
        count=1,
        flags=re.I | re.S,
    )
    return statement, bool(replacements)


class DatabaseAdapter:
    """Compatibility facade while endpoints are incrementally moved to ORM.

    ``exec_driver_sql`` deliberately uses DBAPI placeholders, supported by
    sqlite3 and pyodbc alike.  The facade is request-scoped through the
    Flask-SQLAlchemy session, so all multi-statement WMS operations remain
    atomic.
    """

    @property
    def dialect(self):
        return orm.engine.dialect.name

    def execute(self, sql, params=()):
        if sql.strip().upper() in {"BEGIN", "BEGIN TRANSACTION"}:
            return ResultAdapter(_EmptyResult())
        if self.dialect == "mssql":
            statement, params = _mssql_sql(sql, params)
            statement, projects_identity = _mssql_insert_with_identity(statement)
            if projects_identity:
                result = orm.session.connection().exec_driver_sql(
                    statement, tuple(params)
                )
                inserted_id = result.scalar_one()
                return ResultAdapter(_EmptyResult(), lastrowid=int(inserted_id))
        else:
            statement = sql
        result = orm.session.connection().exec_driver_sql(statement, tuple(params))
        return ResultAdapter(result)

    def executemany(self, sql, params):
        batches = [tuple(item) for item in params]
        if self.dialect == "mssql":
            statement, _ = _mssql_sql(sql, ())
        else:
            statement = sql
        result = orm.session.connection().exec_driver_sql(
            statement,
            batches,
        )
        return ResultAdapter(result)

    def commit(self):
        orm.session.commit()

    def rollback(self):
        orm.session.rollback()


class _EmptyResult:
    lastrowid = None
    rowcount = 0

    @staticmethod
    def fetchone():
        return None

    @staticmethod
    def fetchall():
        return []


def get_db():
    if "db_adapter" not in g:
        g.db_adapter = DatabaseAdapter()
        if orm.engine.dialect.name == "sqlite":
            g.db_adapter.execute("PRAGMA foreign_keys = ON")
            g.db_adapter.execute("PRAGMA busy_timeout = 10000")
    return g.db_adapter


def close_db(error=None):
    g.pop("db_adapter", None)
    orm.session.remove()
    # Windows keeps SQLite files locked while pooled connections remain open;
    # test databases are disposable, so release the pool at context teardown.
    if current_app.config.get("TESTING") and orm.engine.dialect.name == "sqlite":
        orm.engine.dispose()


def audit(action, entity_type, entity_id=None, details=None, user_id=None, ip_address=""):
    get_db().execute(
        """
        INSERT INTO audit_logs
            (user_id, action, entity_type, entity_id, details, ip_address)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            action,
            entity_type,
            entity_id,
            json.dumps(details or {}, ensure_ascii=False),
            ip_address or "",
        ),
    )


def init_database():
    # Import registers all tables on metadata.
    from . import models  # noqa: F401

    orm.session.remove()
    orm.drop_all()
    orm.create_all()
    database = get_db()
    seed_database(database)
    database.commit()


def seed_database(database):
    database.executemany(
        "INSERT INTO roles (code, name, description) VALUES (?, ?, ?)",
        [
            ("ADMIN", "Quản trị viên", "Quản trị tài khoản và dữ liệu nền"),
            ("CS", "Chăm sóc khách hàng", "Lập và theo dõi phiếu nhập/xuất"),
            ("WAREHOUSE", "Nhân viên kho", "Kiểm nhận, xuất hàng và kiểm kê"),
        ],
    )
    database.executemany(
        "INSERT INTO units (code, name, allow_break_pack, status) VALUES (?, ?, ?, ?)",
        [
            ("CAI", "Cái", False, "active"),
            ("THUNG", "Thùng", False, "active"),
            ("HOP", "Hộp", False, "active"),
            ("PALLET", "Pallet", False, "active"),
            ("CUON", "Cuộn", False, "active"),
            ("CAY", "Cây", False, "active"),
            ("BAO", "Bao", False, "active"),
            ("TAM", "Tấm", False, "active"),
            ("VIEN", "Viên", False, "active"),
        ],
    )
    users = [
        (
            "admin",
            generate_password_hash("Admin@123"),
            "Nguyễn Anh Thư",
            "admin@dnp.vn",
            "0901 234 567",
            "admin",
            "active",
            "AT",
        ),
        (
            "quanlykho",
            generate_password_hash("Kho@12345"),
            "Trần Minh Quân",
            "quanlykho@dnp.vn",
            "0902 345 678",
            "manager",
            "active",
            "MQ",
        ),
        (
            "nhanvien",
            generate_password_hash("NV@123456"),
            "Lê Hoàng Nam",
            "nhanvien@dnp.vn",
            "0903 456 789",
            "staff",
            "active",
            "HN",
        ),
        (
            "khoatam",
            generate_password_hash("Locked@123"),
            "Phạm Thu Hà",
            "thuhakhoa@dnp.vn",
            "",
            "staff",
            "locked",
            "TH",
        ),
        (
            "cs",
            generate_password_hash("Cs@123456"),
            "Nguyễn Minh Châu",
            "cs@dnp.vn",
            "0904 111 222",
            "cs",
            "active",
            "MC",
        ),
        (
            "warehouse",
            generate_password_hash("Kho@12345"),
            "Phạm Quốc Bảo",
            "warehouse@dnp.vn",
            "0905 333 444",
            "warehouse",
            "active",
            "QB",
        ),
    ]
    database.executemany(
        """
        INSERT INTO users
            (username, password_hash, full_name, email, phone, role, status, avatar_initials)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        users,
    )

    categories = [
        ("NVL", "Nguyên vật liệu", "Vật tư đầu vào phục vụ sản xuất", "active"),
        ("VTXD", "Vật tư xây dựng", "Vật tư dùng cho công trình", "active"),
        ("VTDIEN", "Vật tư điện", "Thiết bị và vật tư ngành điện", "active"),
        ("PK", "Phụ kiện", "Các loại phụ kiện hỗ trợ", "active"),
        ("DGOI", "Vật tư đóng gói", "Bao bì và vật tư đóng gói", "active"),
        ("TB", "Thiết bị", "Máy móc và thiết bị kho", "active"),
    ]
    database.executemany(
        "INSERT INTO categories (code, name, description, status) VALUES (?, ?, ?, ?)",
        categories,
    )

    warehouses = [
        ("HN", "Kho Hà Nội", "KCN Quang Minh, Mê Linh, Hà Nội", "active"),
        ("HCM", "Kho TP.HCM", "KCN Tân Bình, TP.HCM", "active"),
        ("DN", "Kho Đà Nẵng", "KCN Hòa Khánh, Đà Nẵng", "active"),
        ("BD", "Kho Bình Dương", "KCN Sóng Thần, Bình Dương", "active"),
    ]
    database.executemany(
        "INSERT INTO warehouses (code, name, address, status) VALUES (?, ?, ?, ?)",
        warehouses,
    )

    category_ids = {
        row["code"]: row["id"] for row in database.execute("SELECT id, code FROM categories")
    }
    warehouse_ids = {
        row["code"]: row["id"] for row in database.execute("SELECT id, code FROM warehouses")
    }
    inventory = [
        ("SKU-1001", "Thép hộp 40x40", "NVL", "HN", "Cây", 1250, 200, "A-01-01"),
        ("SKU-1002", "Xi măng PCB40", "VTXD", "HCM", "Bao", 42, 100, "B-02-03"),
        ("SKU-1003", "Dây điện Cadivi 2.5", "VTDIEN", "DN", "Cuộn", 0, 20, "C-01-04"),
        ("SKU-1004", "Bu lông M12", "PK", "BD", "Hộp", 720, 100, "A-03-02"),
        ("SKU-1005", "Màng PE bọc hàng", "DGOI", "HN", "Cuộn", 110, 30, "D-02-01"),
        ("SKU-1006", "Máy quấn màng pallet", "TB", "HCM", "Cái", 3, 2, "E-01-01"),
        ("SKU-1007", "Tôn lạnh 0.45mm", "NVL", "DN", "Tấm", 520, 80, "A-02-05"),
        ("SKU-1008", "Gạch terrazzo", "VTXD", "BD", "Viên", 1450, 300, "B-01-02"),
        ("SKU-1009", "Aptomat 2P 32A", "VTDIEN", "HN", "Cái", 18, 25, "C-03-02"),
        ("SKU-1010", "Pallet gỗ 1.2x1.0m", "DGOI", "HCM", "Cái", 85, 20, "D-01-03"),
        ("SKU-1011", "Ống thép D50", "NVL", "DN", "Cây", 310, 60, "A-04-01"),
        ("SKU-1012", "Sơn nội thất trắng", "VTXD", "BD", "Thùng", 0, 15, "B-04-02"),
    ]
    database.executemany(
        """
        INSERT INTO inventory
            (sku, name, category_id, warehouse_id, unit, quantity, min_quantity, location)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (sku, name, category_ids[category], warehouse_ids[warehouse], unit, qty, minimum, location)
            for sku, name, category, warehouse, unit, qty, minimum, location in inventory
        ],
    )
    database.executemany(
        """
        INSERT INTO customers (code, name, email, phone, contract_emails, status)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            ("KH001", "Công ty Minh Phát", "kho@minhphat.vn", "028 3812 3456", "kho@minhphat.vn,dieuphoi@minhphat.vn", "active"),
            ("KH002", "Xây dựng An Khang", "vattu@ankhang.vn", "024 3765 4321", "vattu@ankhang.vn", "active"),
            ("KH003", "Nội thất Thành Công", "muahang@thanhcong.vn", "0908 112 233", "muahang@thanhcong.vn", "active"),
        ],
    )
    database.executemany(
        """
        INSERT INTO suppliers (code, name, email, phone, address, status)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            ("NCC001", "Thép Việt Nam", "sales@thepvietnam.vn", "028 3866 7788", "TP.HCM", "active"),
            ("NCC002", "Vật liệu Đông Á", "donhang@dong-a.vn", "024 3555 6677", "Hà Nội", "active"),
            ("NCC003", "Thiết bị Hoàng Gia", "cs@hoanggia.vn", "0236 399 8899", "Đà Nẵng", "active"),
        ],
    )
    database.execute(
        """
        INSERT INTO receipts
          (code, receipt_type, partner_id, supplier_id, partner_name, warehouse_id, container_no,
           seal_no, status, note, created_by)
        VALUES ('PN-2026-001', 'inbound', 1, 1, 'Thép Việt Nam', ?, 'DNPX260701',
                'SEAL-8801', 'pending', 'Chờ nhân viên kho kiểm nhận', 1)
        """,
        (warehouse_ids["HN"],),
    )
    database.execute(
        """
        INSERT INTO receipt_items
          (receipt_id, inventory_id, quantity, accepted_quantity, pallet_id, barcode)
        VALUES (1, 1, 120, 120, 'PLT-HN-0001', '8938501000012')
        """
    )
    # Normalized contract-email records are authoritative for outbound checks.
    customer_rows = database.execute(
        "SELECT id, contract_emails FROM customers"
    ).fetchall()
    database.executemany(
        """
        INSERT INTO customer_contract_emails
            (customer_id, email, normalized_email, status)
        VALUES (?, ?, ?, 'active')
        """,
        [
            (customer["id"], email.strip(), email.strip().casefold())
            for customer in customer_rows
            for email in customer["contract_emails"].split(",")
            if email.strip()
        ],
    )
    # Initial demo stock is represented as one lot per product.  Subsequent
    # confirmed receipts add immutable, traceable pallets.
    products = database.execute(
        "SELECT id, warehouse_id, unit, barcode, quantity FROM inventory WHERE quantity > 0"
    ).fetchall()
    database.executemany(
        """
        INSERT INTO inventory_lots
            (product_id, warehouse_id, unit, pallet_id, barcode, quantity, status)
        VALUES (?, ?, ?, ?, ?, ?, 'active')
        """,
        [
            (
                product["id"],
                product["warehouse_id"],
                product["unit"],
                f"OPENING-{product['id']:04d}",
                None,
                product["quantity"],
            )
            for product in products
        ],
    )
    database.execute(
        """
        INSERT INTO receipts
          (code, receipt_type, partner_id, customer_id, partner_name, warehouse_id, request_email,
           status, note, created_by)
        VALUES ('PX-2026-001', 'outbound', 1, 1, 'Công ty Minh Phát', ?,
                'kho@minhphat.vn', 'picking', 'Ưu tiên giao ca sáng', 1)
        """,
        (warehouse_ids["HN"],),
    )
    database.execute(
        """
        INSERT INTO receipt_items
          (receipt_id, inventory_id, quantity, accepted_quantity, pallet_id, barcode)
        VALUES (2, 1, 30, 30, 'PLT-HN-0001', '8938501000012')
        """
    )


@click.command("init-db")
@with_appcontext
def init_db_command():
    """Khởi tạo lại cơ sở dữ liệu và dữ liệu demo."""
    init_database()
    message = "Đã khởi tạo cơ sở dữ liệu DNP WMS."
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    if encoding.lower().replace("-", "") not in {"utf8", "utf8sig"}:
        message = "Da khoi tao co so du lieu DNP WMS."
    click.echo(message)


def _safe_echo(message, fallback):
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    if encoding.lower().replace("-", "") not in {"utf8", "utf8sig"}:
        message = fallback
    click.echo(message)


@click.command("seed-db")
@with_appcontext
def seed_db_command():
    """Nạp dữ liệu demo một lần, an toàn khi chạy lặp."""
    database = get_db()
    if database.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
        _safe_echo(
            "Dữ liệu demo đã tồn tại; không tạo bản ghi trùng.",
            "Du lieu demo da ton tai; khong tao ban ghi trung.",
        )
        return
    seed_database(database)
    database.commit()
    _safe_echo("Đã nạp dữ liệu demo DNP WMS.", "Da nap du lieu demo DNP WMS.")


def _sqlite_file():
    uri = current_app.config["SQLALCHEMY_DATABASE_URI"]
    if not uri.startswith("sqlite:///") or uri.endswith(":memory:"):
        raise click.ClickException(
            "Lệnh này chỉ hỗ trợ SQLite. Với SQL Server, dùng BACKUP DATABASE/RESTORE DATABASE."
        )
    return Path(uri.removeprefix("sqlite:///")).resolve()


@click.command("backup-db")
@click.option(
    "--output", type=click.Path(path_type=Path), required=True,
    help="Tệp .sqlite đích; phải khác cơ sở dữ liệu đang chạy.",
)
@with_appcontext
def backup_db_command(output):
    """Tạo bản sao SQLite nhất quán sau khi flush transaction."""
    source = _sqlite_file()
    target = output.expanduser().resolve()
    if source == target:
        raise click.ClickException("Tệp backup phải khác tệp cơ sở dữ liệu đang chạy.")
    target.parent.mkdir(parents=True, exist_ok=True)
    orm.session.commit()
    # VACUUM INTO is atomic and includes committed WAL content.
    escaped = str(target).replace("'", "''")
    if target.exists():
        raise click.ClickException("Tệp backup đã tồn tại; hãy chọn tên mới.")
    orm.session.connection().exec_driver_sql(f"VACUUM INTO '{escaped}'")
    _safe_echo(f"Đã tạo backup: {target}", f"Da tao backup: {target}")


@click.command("restore-db")
@click.argument("source", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--yes", is_flag=True, help="Xác nhận ghi đè cơ sở dữ liệu hiện tại.")
@with_appcontext
def restore_db_command(source, yes):
    """Khôi phục SQLite; yêu cầu xác nhận rõ vì thao tác ghi đè."""
    if not yes:
        raise click.ClickException("Thêm --yes để xác nhận khôi phục dữ liệu.")
    target = _sqlite_file()
    source = source.resolve()
    if source == target:
        raise click.ClickException("Nguồn khôi phục trùng cơ sở dữ liệu đang chạy.")
    # Verify the source before replacing anything.
    import sqlite3

    connection = sqlite3.connect(source)
    try:
        result = connection.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        connection.close()
    if result != "ok":
        raise click.ClickException("Tệp backup không vượt qua kiểm tra toàn vẹn.")
    orm.session.remove()
    orm.engine.dispose()
    # SQLite's online-backup API replaces pages through an opened database
    # handle and therefore works on Windows without unlinking a locked file.
    source_connection = sqlite3.connect(source)
    target_connection = sqlite3.connect(target)
    try:
        source_connection.backup(target_connection)
    finally:
        target_connection.close()
        source_connection.close()
    _safe_echo(
        f"Đã khôi phục cơ sở dữ liệu từ: {source}",
        f"Da khoi phuc co so du lieu tu: {source}",
    )


def init_app(app):
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)
    app.cli.add_command(seed_db_command)
    app.cli.add_command(backup_db_command)
    app.cli.add_command(restore_db_command)
