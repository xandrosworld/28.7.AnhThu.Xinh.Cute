import os
import sqlite3
from datetime import datetime, timedelta

from werkzeug.security import generate_password_hash


REQUIRED_DATABASE_COLUMNS = {
    "users": {"id", "username", "password_hash", "role", "status"},
    "suppliers": {"id", "code", "name", "status"},
    "warehouses": {"id", "code", "name", "status"},
    "products": {"id", "sku", "barcode", "unit", "current_stock"},
    "receipts": {"id", "code", "status", "received_date"},
    "receipt_items": {
        "id", "receipt_id", "product_id", "actual_qty", "rejected_qty",
        "pallet_id", "barcode", "unit",
    },
    "inspections": {"id", "receipt_id", "result"},
    "inventory_lots": {"id", "receipt_item_id", "pallet_id", "quantity"},
    "stock_movements": {"id", "receipt_id", "product_id", "type", "quantity"},
    "audit_logs": {"id", "action", "entity_type"},
}


def init_database(path, seed=True):
    """Create the SQLite schema and optional deterministic demonstration data."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        migrate_legacy_schema(connection)
        schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
        with open(schema_path, encoding="utf-8") as schema_file:
            connection.executescript(schema_file.read())
        backfill_inventory_lots(connection)
        if seed:
            product_count = connection.execute("SELECT COUNT(*) FROM products").fetchone()[0]
            seed_database(connection, seed_demo=product_count == 0)
        connection.commit()
    finally:
        connection.close()


def migrate_legacy_schema(db):
    """Add non-destructive columns required by the enhanced branch.

    The original submission created a local ignored database. This migration
    lets that database continue to work instead of requiring students to delete
    their demo data.
    """
    tables = {
        row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "products" not in tables:
        return

    def columns(table):
        return {row[1] for row in db.execute(f"PRAGMA table_info({table})")}

    receipt_item_columns = columns("receipt_items") if "receipt_items" in tables else set()
    receipt_item_sql = (
        db.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='receipt_items'"
        ).fetchone()[0]
        if "receipt_items" in tables
        else ""
    )
    normalized_receipt_item_sql = "".join(receipt_item_sql.lower().split())
    rebuild_receipt_items = bool(
        receipt_item_columns
        and (
            not {
                "rejected_qty", "rejection_reason", "pallet_id",
                "barcode", "unit", "expiry_date",
            }.issubset(receipt_item_columns)
            or "check(actual_qty>0)" in normalized_receipt_item_sql
        )
    )
    if rebuild_receipt_items:
        # The original schema rejected actual_qty=0, which makes a fully
        # rejected inbound line impossible to inspect after migration.
        db.execute("PRAGMA foreign_keys = OFF")

    additions = {
        "products": [("barcode", "TEXT")],
        "receipts": [
            ("seal_no", "TEXT DEFAULT ''"),
            ("created_by", "INTEGER"),
            ("completed_by", "INTEGER"),
        ],
        "inspections": [("inspected_by_user_id", "INTEGER")],
        "audit_logs": [("actor_user_id", "INTEGER")],
    }
    for table, definitions in additions.items():
        if table not in tables:
            continue
        existing = columns(table)
        for name, definition in definitions:
            if name not in existing:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
    db.execute(
        "UPDATE products SET barcode='LEGACY-' || sku WHERE barcode IS NULL OR barcode=''"
    )
    if "receipt_items" in tables:
        if rebuild_receipt_items:
            def old_or_default(name, default):
                return name if name in receipt_item_columns else default

            db.execute(
                """
                CREATE TABLE receipt_items_migrated (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    receipt_id INTEGER NOT NULL REFERENCES receipts(id) ON DELETE CASCADE,
                    product_id INTEGER NOT NULL REFERENCES products(id),
                    planned_qty REAL NOT NULL CHECK(planned_qty > 0),
                    actual_qty REAL CHECK(actual_qty >= 0),
                    rejected_qty REAL NOT NULL DEFAULT 0 CHECK(rejected_qty >= 0),
                    rejection_reason TEXT DEFAULT '',
                    unit_price REAL NOT NULL DEFAULT 0 CHECK(unit_price >= 0),
                    pallet_id TEXT NOT NULL UNIQUE,
                    barcode TEXT NOT NULL,
                    unit TEXT NOT NULL,
                    expiry_date TEXT,
                    UNIQUE(receipt_id, product_id)
                )
                """
            )
            pallet = old_or_default(
                "pallet_id", "'LEGACY-PLT-' || receipt_items.id"
            )
            barcode = old_or_default(
                "barcode",
                "(SELECT barcode FROM products WHERE products.id=receipt_items.product_id)",
            )
            unit = old_or_default(
                "unit",
                "(SELECT unit FROM products WHERE products.id=receipt_items.product_id)",
            )
            db.execute(
                f"""
                INSERT INTO receipt_items_migrated
                (id,receipt_id,product_id,planned_qty,actual_qty,rejected_qty,
                 rejection_reason,unit_price,pallet_id,barcode,unit,expiry_date)
                SELECT id,receipt_id,product_id,planned_qty,actual_qty,
                       COALESCE({old_or_default("rejected_qty", "0")},0),
                       COALESCE({old_or_default("rejection_reason", "''")},''),
                       unit_price,
                       COALESCE(NULLIF({pallet},''),'LEGACY-PLT-' || receipt_items.id),
                       COALESCE(NULLIF({barcode},''),
                           (SELECT barcode FROM products WHERE products.id=receipt_items.product_id)),
                       COALESCE(NULLIF({unit},''),
                           (SELECT unit FROM products WHERE products.id=receipt_items.product_id)),
                       {old_or_default("expiry_date", "NULL")}
                FROM receipt_items
                """
            )
            db.execute("DROP TABLE receipt_items")
            db.execute("ALTER TABLE receipt_items_migrated RENAME TO receipt_items")
            db.commit()
            db.execute("PRAGMA foreign_keys = ON")
        else:
            db.execute(
                """UPDATE receipt_items
                   SET pallet_id='LEGACY-PLT-' || id
                   WHERE pallet_id IS NULL OR pallet_id=''"""
            )
            db.execute(
                """UPDATE receipt_items
                   SET barcode=(SELECT barcode FROM products WHERE products.id=receipt_items.product_id),
                       unit=(SELECT unit FROM products WHERE products.id=receipt_items.product_id)
                   WHERE barcode IS NULL OR barcode='' OR unit IS NULL OR unit=''"""
            )


def backfill_inventory_lots(db):
    """Represent completed legacy receipts in the pallet-level inventory view."""
    db.execute(
        """
        INSERT OR IGNORE INTO inventory_lots
        (product_id,receipt_item_id,warehouse,pallet_id,barcode,unit,
         quantity,received_at,expiry_date)
        SELECT i.product_id,i.id,r.warehouse,i.pallet_id,i.barcode,i.unit,
               i.actual_qty,COALESCE(r.completed_at,r.received_date),i.expiry_date
        FROM receipt_items i JOIN receipts r ON r.id=i.receipt_id
        WHERE r.status='completed' AND i.actual_qty > 0
        """
    )


def validate_database(db):
    """Reject corrupt, foreign-key-invalid, or non-WMS SQLite restore sources."""
    try:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("File sao lưu không vượt qua kiểm tra toàn vẹn.")
        if db.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("File sao lưu có liên kết khóa ngoại không hợp lệ.")
        tables = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        missing_tables = REQUIRED_DATABASE_COLUMNS.keys() - tables
        if missing_tables:
            raise ValueError(
                "File không phải bản sao WMS hợp lệ (thiếu bảng: "
                + ", ".join(sorted(missing_tables))
                + ")."
            )
        for table, required in REQUIRED_DATABASE_COLUMNS.items():
            present = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
            missing = required - present
            if missing:
                raise ValueError(
                    f"File sao lưu thiếu cột bắt buộc của bảng {table}: "
                    + ", ".join(sorted(missing))
                    + "."
                )
    except sqlite3.DatabaseError as exc:
        raise ValueError(f"File sao lưu SQLite không hợp lệ: {exc}") from exc


def seed_database(db, seed_demo=True):
    db.executemany(
        "INSERT OR IGNORE INTO users(username,password_hash,full_name,role,status) VALUES(?,?,?,?,?)",
        [
            ("admin", generate_password_hash("Admin@123"), "Quản trị hệ thống", "ADMIN", "active"),
            ("cs", generate_password_hash("CS@12345"), "Nhân viên chăm sóc khách hàng", "CS", "active"),
            ("warehouse", generate_password_hash("Kho@12345"), "Nhân viên kho", "WAREHOUSE", "active"),
            ("locked", generate_password_hash("Locked@123"), "Tài khoản khóa", "WAREHOUSE", "locked"),
        ],
    )
    db.executemany(
        "INSERT OR IGNORE INTO suppliers(code,name,status) VALUES(?,?,'active')",
        [
            ("NCC-DA", "Công ty Thép Đông Á"),
            ("NCC-SDS", "Samsung SDS Việt Nam"),
            ("NCC-TT", "Công ty Bao bì Tân Tiến"),
        ],
    )
    db.executemany(
        "INSERT OR IGNORE INTO warehouses(code,name,status) VALUES(?,?,'active')",
        [("KHO-A", "Kho A"), ("KHO-B", "Kho B"), ("KHO-C", "Kho C")],
    )
    if not seed_demo:
        return
    products = [
        ("ST-COIL-012", "Thép cuộn mạ kẽm", "Nguyên vật liệu", "Cuộn", "8938500120012", 35, 10, 12500000),
        ("EL-CABLE-025", "Cáp điện công nghiệp", "Điện tử", "Cuộn", "8938500120029", 18, 20, 2450000),
        ("PK-BOX-100", "Thùng carton 5 lớp", "Bao bì", "Thùng", "8938500120036", 240, 80, 32000),
        ("MC-BEAR-620", "Vòng bi công nghiệp 6205", "Linh kiện", "Cái", "8938500120043", 42, 25, 185000),
        ("SP-OIL-020", "Dầu thủy lực ISO VG 46", "Máy móc", "Can", "8938500120050", 9, 12, 950000),
        ("EL-SENS-014", "Cảm biến quang E3Z", "Điện tử", "Cái", "8938500120067", 55, 15, 870000),
    ]
    db.executemany(
        """INSERT INTO products
           (sku,name,category,unit,barcode,current_stock,min_stock,unit_price)
           VALUES(?,?,?,?,?,?,?,?)""",
        products,
    )
    now = datetime.now().replace(microsecond=0)
    receipts = [
        ("NK-DEMO-001", "Công ty Thép Đông Á", "Kho A", now - timedelta(days=20), "completed", "43C-123.45", "TCNU8291024", "Lô thép tháng này"),
        ("NK-DEMO-002", "Samsung SDS Việt Nam", "Kho B", now - timedelta(days=2), "pending", "51D-889.10", "SEGU4812040", ""),
        ("NK-DEMO-003", "Công ty Bao bì Tân Tiến", "Kho A", now, "pending", "60C-456.78", "", "Giao trước 10 giờ"),
    ]
    admin_id = db.execute("SELECT id FROM users WHERE username='admin'").fetchone()[0]
    for code, supplier, warehouse, received_date, status, vehicle, container, note in receipts:
        cursor = db.execute(
            """INSERT INTO receipts
               (code,supplier,warehouse,received_date,status,vehicle_no,container_no,note,
                created_by,completed_by,created_at,updated_at,completed_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                code, supplier, warehouse, received_date.isoformat(), status, vehicle,
                container, note, admin_id, admin_id if status == "completed" else None,
                (received_date - timedelta(days=1)).isoformat(), received_date.isoformat(),
                received_date.isoformat() if status == "completed" else None,
            ),
        )
        receipt_id = cursor.lastrowid
        product_id = 1 if code.endswith("001") else (2 if code.endswith("002") else 3)
        qty = 30 if code.endswith("001") else (50 if code.endswith("002") else 120)
        product = products[product_id - 1]
        item_cursor = db.execute(
            """INSERT INTO receipt_items
               (receipt_id,product_id,planned_qty,actual_qty,rejected_qty,rejection_reason,
                unit_price,pallet_id,barcode,unit)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                receipt_id, product_id, qty, qty if status == "completed" else None, 0, "",
                product[-1], f"PLT-{code}", product[4], product[3],
            ),
        )
        if status == "completed":
            db.execute(
                """INSERT INTO inspections
                   (receipt_id,checklist_json,result,note,inspected_by,inspected_by_user_id,inspected_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (
                    receipt_id,
                    '{"container":"pass","seal":"pass","goods":"pass","packaging":"pass","barcode":"pass","quantity":"pass","condition":"pass"}',
                    "pass", "", "Quản trị hệ thống", admin_id, received_date.isoformat(),
                ),
            )
            db.execute(
                """INSERT INTO stock_movements
                   (product_id,receipt_id,type,quantity,balance_after,reference_code,created_at)
                   VALUES(?,?,'IN',?,?,?,?)""",
                (product_id, receipt_id, qty, product[5], code, received_date.isoformat()),
            )
            db.execute(
                """INSERT INTO inventory_lots
                   (product_id,receipt_item_id,warehouse,pallet_id,barcode,unit,quantity,received_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (
                    product_id, item_cursor.lastrowid, warehouse, f"PLT-{code}",
                    product[4], product[3], qty, received_date.isoformat(),
                ),
            )
        db.execute(
            "INSERT INTO audit_logs(action,entity_type,entity_id,details,created_at) VALUES(?,?,?,?,?)",
            (
                "COMPLETE" if status == "completed" else "CREATE", "receipt", receipt_id,
                ("Hoàn tất nhập kho " if status == "completed" else "Tạo phiếu nhập ") + code,
                received_date.isoformat(),
            ),
        )


if __name__ == "__main__":
    init_database(os.path.join(os.path.dirname(__file__), "instance", "wms.sqlite3"))
    print("WMS database initialized successfully.")
