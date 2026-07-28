import json
import sqlite3

import click
from flask import current_app, g
from flask.cli import with_appcontext
from werkzeug.security import generate_password_hash


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(
            current_app.config["DATABASE"],
            detect_types=sqlite3.PARSE_DECLTYPES,
            timeout=10,
        )
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
        g.db.execute("PRAGMA busy_timeout = 10000")
    return g.db


def close_db(error=None):
    database = g.pop("db", None)
    if database is not None:
        database.close()


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
    database = get_db()
    with current_app.open_resource("schema.sql") as schema:
        database.executescript(schema.read().decode("utf-8"))
    seed_database(database)
    database.commit()


def seed_database(database):
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


@click.command("init-db")
@with_appcontext
def init_db_command():
    """Khởi tạo lại cơ sở dữ liệu và dữ liệu demo."""
    init_database()
    click.echo("Đã khởi tạo cơ sở dữ liệu DNP WMS.")


def init_app(app):
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)
