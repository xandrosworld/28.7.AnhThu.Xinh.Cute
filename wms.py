"""Nghiệp vụ WMS mở rộng cho phân hệ backend của Lê Phương Thảo.

Module này cố ý dùng SQL chuẩn và transaction ngắn. SQLite là cấu hình chạy
ngay; schema dùng kiểu dữ liệu/ràng buộc tương thích để có thể chuyển sang SQL
Server qua migration trong môi trường môn học mà không đổi luật nghiệp vụ.
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import secrets
import sqlite3
import tempfile
from datetime import date, datetime, timezone
from functools import wraps
from pathlib import Path

import click
from flask import (
    current_app,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.exceptions import HTTPException


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ROLES = {"ADMIN", "CS", "WAREHOUSE"}


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ok(data=None, message=None, status=200, meta=None, **legacy):
    payload = {"data": data}
    if meta is not None:
        payload["meta"] = meta
    if message:
        payload["message"] = message
    payload.update(legacy)
    return jsonify(payload), status


def fail(code: str, message: str, status=400, fields=None):
    detail = {"code": code, "message": message, "fields": fields or {}}
    # message/details giữ tương thích với frontend cũ.
    return jsonify(error=detail, message=message, details=fields or {}), status


def body() -> dict:
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise ValidationError("JSON_OBJECT_REQUIRED", "Dữ liệu gửi lên phải là JSON object.")
    return value


def text(value, name, maximum=255, required=True):
    result = str(value or "").strip()
    if required and not result:
        raise ValidationError("VALIDATION_ERROR", "Dữ liệu chưa hợp lệ.", {name: "Trường này là bắt buộc."})
    if len(result) > maximum:
        raise ValidationError("VALIDATION_ERROR", "Dữ liệu chưa hợp lệ.", {name: f"Tối đa {maximum} ký tự."})
    return result


def positive_decimal(value, name, allow_zero=False):
    if isinstance(value, bool):
        raise ValidationError("VALIDATION_ERROR", "Dữ liệu chưa hợp lệ.", {name: "Phải là một số hữu hạn."})
    try:
        number = round(float(value), 3)
    except (TypeError, ValueError):
        raise ValidationError("VALIDATION_ERROR", "Dữ liệu chưa hợp lệ.", {name: "Phải là một số."})
    if not math.isfinite(number):
        raise ValidationError("VALIDATION_ERROR", "Dữ liệu chưa hợp lệ.", {name: "Phải là một số hữu hạn."})
    if number < 0 or (not allow_zero and number == 0):
        raise ValidationError(
            "VALIDATION_ERROR",
            "Dữ liệu chưa hợp lệ.",
            {name: "Phải lớn hơn 0." if not allow_zero else "Không được âm."},
        )
    return number


def positive_id(value, name, required=True):
    if isinstance(value, bool):
        value = None
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = 0
    if result <= 0 and required:
        raise ValidationError("VALIDATION_ERROR", "Dữ liệu chưa hợp lệ.", {name: "Phải là mã số nguyên dương."})
    return result if result > 0 else None


def iso_date(value, name, required=True):
    result = text(value, name, 10, required)
    if not result:
        return None
    try:
        return date.fromisoformat(result).isoformat()
    except ValueError:
        raise ValidationError("VALIDATION_ERROR", "Dữ liệu chưa hợp lệ.", {name: "Dùng định dạng YYYY-MM-DD."})


class ValidationError(Exception):
    def __init__(self, code, message, fields=None, status=422):
        self.code, self.message, self.fields, self.status = code, message, fields or {}, status
        super().__init__(message)


EXTENSION_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
 password_hash TEXT NOT NULL, full_name TEXT NOT NULL, email TEXT NOT NULL UNIQUE COLLATE NOCASE,
 role TEXT NOT NULL CHECK(role IN ('ADMIN','CS','WAREHOUSE')),
 status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','locked')),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS customers (
 id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL UNIQUE COLLATE NOCASE,
 name TEXT NOT NULL, tax_code TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL DEFAULT '',
 address TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1))
);
CREATE TABLE IF NOT EXISTS customer_contract_emails (
 id INTEGER PRIMARY KEY AUTOINCREMENT, customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
 email TEXT NOT NULL COLLATE NOCASE, active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
 UNIQUE(customer_id,email)
);
CREATE TABLE IF NOT EXISTS suppliers (
 id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL UNIQUE COLLATE NOCASE,
 name TEXT NOT NULL, tax_code TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL DEFAULT '',
 email TEXT NOT NULL DEFAULT '', address TEXT NOT NULL DEFAULT '',
 active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1))
);
CREATE TABLE IF NOT EXISTS warehouses (
 id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL UNIQUE COLLATE NOCASE,
 name TEXT NOT NULL, address TEXT NOT NULL DEFAULT '',
 active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1))
);
CREATE TABLE IF NOT EXISTS inventory_lots (
 id INTEGER PRIMARY KEY AUTOINCREMENT, pallet_id TEXT NOT NULL UNIQUE COLLATE NOCASE,
 barcode TEXT NOT NULL UNIQUE COLLATE NOCASE,
 product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
 warehouse_id INTEGER NOT NULL REFERENCES warehouses(id) ON DELETE RESTRICT,
 unit TEXT NOT NULL, location TEXT NOT NULL DEFAULT '', received_at TEXT NOT NULL,
 expiry_date TEXT, quantity REAL NOT NULL CHECK(quantity >= 0),
 reserved_quantity REAL NOT NULL DEFAULT 0 CHECK(reserved_quantity >= 0 AND reserved_quantity <= quantity),
 active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1))
);
CREATE INDEX IF NOT EXISTS idx_lot_pick ON inventory_lots(product_id,warehouse_id,expiry_date,received_at);
CREATE TABLE IF NOT EXISTS inbound_receipts (
 id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL UNIQUE COLLATE NOCASE,
 supplier_id INTEGER REFERENCES suppliers(id) ON DELETE RESTRICT,
 warehouse_id INTEGER NOT NULL REFERENCES warehouses(id) ON DELETE RESTRICT,
 expected_date TEXT NOT NULL, container_no TEXT NOT NULL DEFAULT '', seal_no TEXT NOT NULL DEFAULT '',
 note TEXT NOT NULL DEFAULT '', status TEXT NOT NULL CHECK(status IN ('DRAFT','INSPECTED','CONFIRMED','CANCELLED')),
 created_by INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 confirmed_by INTEGER REFERENCES users(id) ON DELETE RESTRICT, confirmed_at TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS inbound_items (
 id INTEGER PRIMARY KEY AUTOINCREMENT, receipt_id INTEGER NOT NULL REFERENCES inbound_receipts(id) ON DELETE CASCADE,
 product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
 expected_quantity REAL NOT NULL CHECK(expected_quantity > 0),
 accepted_quantity REAL, rejected_quantity REAL, reject_reason TEXT NOT NULL DEFAULT '',
 unit TEXT NOT NULL, pallet_id TEXT NOT NULL UNIQUE COLLATE NOCASE,
 barcode TEXT NOT NULL UNIQUE COLLATE NOCASE, location TEXT NOT NULL DEFAULT '',
 expiry_date TEXT, UNIQUE(receipt_id,product_id,pallet_id)
);
CREATE TABLE IF NOT EXISTS outbound_allocations (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 order_id INTEGER NOT NULL REFERENCES outbound_orders(id) ON DELETE CASCADE,
 outbound_item_id INTEGER NOT NULL REFERENCES outbound_items(id) ON DELETE CASCADE,
 lot_id INTEGER NOT NULL REFERENCES inventory_lots(id) ON DELETE RESTRICT,
 quantity REAL NOT NULL CHECK(quantity > 0), created_at TEXT NOT NULL,
 UNIQUE(order_id,outbound_item_id,lot_id)
);
CREATE TABLE IF NOT EXISTS stocktake_headers (
 id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL UNIQUE COLLATE NOCASE,
 warehouse_id INTEGER NOT NULL REFERENCES warehouses(id) ON DELETE RESTRICT,
 status TEXT NOT NULL CHECK(status IN ('DRAFT','CONFIRMED','CANCELLED')),
 reason TEXT NOT NULL, created_by INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 confirmed_by INTEGER REFERENCES users(id) ON DELETE RESTRICT, confirmed_at TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS stocktake_items (
 id INTEGER PRIMARY KEY AUTOINCREMENT, stocktake_id INTEGER NOT NULL REFERENCES stocktake_headers(id) ON DELETE CASCADE,
 lot_id INTEGER NOT NULL REFERENCES inventory_lots(id) ON DELETE RESTRICT,
 system_quantity REAL NOT NULL CHECK(system_quantity >= 0),
 actual_quantity REAL NOT NULL CHECK(actual_quantity >= 0),
 difference REAL NOT NULL, note TEXT NOT NULL DEFAULT '', UNIQUE(stocktake_id,lot_id)
);
CREATE TABLE IF NOT EXISTS wms_movements (
 id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
 lot_id INTEGER REFERENCES inventory_lots(id) ON DELETE RESTRICT,
 warehouse_id INTEGER NOT NULL REFERENCES warehouses(id) ON DELETE RESTRICT,
 movement_type TEXT NOT NULL CHECK(movement_type IN ('INBOUND','OUTBOUND','STOCKTAKE')),
 quantity_change REAL NOT NULL, quantity_before REAL NOT NULL CHECK(quantity_before >= 0),
 quantity_after REAL NOT NULL CHECK(quantity_after >= 0),
 reference_type TEXT NOT NULL, reference_id INTEGER NOT NULL,
 actor_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
 UNIQUE(reference_type,reference_id,lot_id,movement_type)
);
CREATE INDEX IF NOT EXISTS idx_wms_movements_product_date ON wms_movements(product_id,created_at);
CREATE TABLE IF NOT EXISTS audit_logs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
 action TEXT NOT NULL, entity_type TEXT NOT NULL, entity_id INTEGER,
 details TEXT NOT NULL DEFAULT '{}', ip_address TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);
"""


def _columns(db, table):
    return {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}


def init_extension(db):
    db.executescript(EXTENSION_SCHEMA)
    # Migration tăng dần cho database đã tồn tại của nhánh.
    outbound_columns = _columns(db, "outbound_orders")
    additions = {
        "customer_id": "INTEGER REFERENCES customers(id) ON DELETE RESTRICT",
        "request_email": "TEXT NOT NULL DEFAULT ''",
        "warehouse_id": "INTEGER REFERENCES warehouses(id) ON DELETE RESTRICT",
        "confirmed_by": "INTEGER REFERENCES users(id) ON DELETE RESTRICT",
        "confirmed_at": "TEXT",
    }
    for column, definition in additions.items():
        if column not in outbound_columns:
            db.execute(f"ALTER TABLE outbound_orders ADD COLUMN {column} {definition}")
    timestamp = utcnow()
    if db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        db.executemany(
            """INSERT INTO users(username,password_hash,full_name,email,role,status,created_at,updated_at)
               VALUES(?,?,?,?,?,'active',?,?)""",
            [
                ("admin", generate_password_hash("Admin@123"), "Quản trị hệ thống", "admin@dnp.vn", "ADMIN", timestamp, timestamp),
                ("cs", generate_password_hash("CS@12345"), "Nhân viên CS", "cs@dnp.vn", "CS", timestamp, timestamp),
                ("warehouse", generate_password_hash("Kho@12345"), "Lê Phương Thảo", "warehouse@dnp.vn", "WAREHOUSE", timestamp, timestamp),
            ],
        )
    if db.execute("SELECT COUNT(*) FROM warehouses").fetchone()[0] == 0:
        db.executemany(
            "INSERT INTO warehouses(code,name,address) VALUES(?,?,?)",
            [("DN", "Kho Đà Nẵng", "KCN Hòa Khánh"), ("HCM", "Kho TP.HCM", "KCN Tân Bình")],
        )
    if db.execute("SELECT COUNT(*) FROM suppliers").fetchone()[0] == 0:
        db.execute(
            "INSERT INTO suppliers(code,name,tax_code,phone,email,address) VALUES(?,?,?,?,?,?)",
            ("NCC-DEMO", "Nhà cung cấp Minh Long", "0400123456", "0905000001", "ncc@minhlong.vn", "Đà Nẵng"),
        )
    if db.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 0:
        cursor = db.execute(
            "INSERT INTO customers(code,name,tax_code,phone,address) VALUES(?,?,?,?,?)",
            ("KH-DEMO", "Công ty Khách hàng Demo", "0400654321", "0905000002", "Đà Nẵng"),
        )
        db.execute(
            "INSERT INTO customer_contract_emails(customer_id,email) VALUES(?,?)",
            (cursor.lastrowid, "muahang@khachhang.vn"),
        )
    # Chuyển tồn tổng hợp cũ thành lot mở đầu để thuật toán picking hoạt động.
    warehouse_id = db.execute("SELECT id FROM warehouses ORDER BY id LIMIT 1").fetchone()[0]
    for product in db.execute("SELECT id,sku,barcode,unit,location,quantity FROM products").fetchall():
        exists = db.execute("SELECT 1 FROM inventory_lots WHERE product_id=? LIMIT 1", (product["id"],)).fetchone()
        if not exists and product["quantity"] > 0:
            db.execute(
                """INSERT INTO inventory_lots
                   (pallet_id,barcode,product_id,warehouse_id,unit,location,received_at,quantity)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (
                    f"OPEN-{product['sku']}", f"LOT-{product['barcode']}", product["id"], warehouse_id,
                    product["unit"], product["location"], "2026-01-01T00:00:00+00:00", product["quantity"],
                ),
            )
    db.commit()


def audit(db, action, entity, entity_id=None, details=None):
    db.execute(
        """INSERT INTO audit_logs(user_id,action,entity_type,entity_id,details,ip_address,created_at)
           VALUES(?,?,?,?,?,?,?)""",
        (
            session.get("user_id"), action, entity, entity_id,
            json.dumps(details or {}, ensure_ascii=False), request.remote_addr or "", utcnow(),
        ),
    )


def register_wms(app, get_db):
    with app.app_context():
        init_extension(get_db())

    @app.errorhandler(ValidationError)
    def handle_validation(error):
        if "db" in g:
            get_db().rollback()
        return fail(error.code, error.message, error.status, error.fields)

    @app.errorhandler(sqlite3.Error)
    def handle_database_error(error):
        if "db" in g:
            get_db().rollback()
        current_app.logger.exception("Database error while serving %s", request.path)
        status = 409 if isinstance(error, sqlite3.IntegrityError) else 500
        code = "DATABASE_CONSTRAINT" if status == 409 else "DATABASE_ERROR"
        return fail(code, "Dữ liệu xung đột." if status == 409 else "Không thể xử lý dữ liệu.", status)

    @app.errorhandler(HTTPException)
    def handle_http_error(error):
        if not request.path.startswith("/api/"):
            return error
        return fail(
            f"HTTP_{error.code}",
            error.description if error.code not in {404, 405} else (
                "Không tìm thấy API." if error.code == 404 else "Phương thức không được hỗ trợ."
            ),
            error.code,
        )

    @app.before_request
    def security_gate():
        user_id = session.get("user_id")
        g.current_user = (
            get_db().execute("SELECT * FROM users WHERE id=? AND status='active'", (user_id,)).fetchone()
            if user_id else None
        )
        public = request.path in {"/login", "/health", "/api/auth/login"} or request.path.startswith("/static/")
        if public:
            return None
        if g.current_user is None:
            if request.path.startswith("/api/"):
                return fail("AUTH_REQUIRED", "Vui lòng đăng nhập.", 401)
            return redirect(url_for("login_page", next=request.path))
        if request.path.startswith("/api/") and request.method not in {"GET", "HEAD", "OPTIONS"}:
            supplied = request.headers.get("X-CSRF-Token", "")
            expected = session.get("csrf_token", "")
            if not expected or not secrets.compare_digest(supplied, expected):
                return fail("CSRF_INVALID", "CSRF token không hợp lệ.", 403)
            if request.path == "/api/outbound-orders" or request.path.startswith("/api/outbound-orders/"):
                return fail(
                    "USE_COMPLIANT_WORKFLOW",
                    "API phiếu xuất cũ chỉ còn cho phép đọc. Hãy dùng /api/outbound-receipts.",
                    410,
                    {"workflow": "/api/outbound-receipts"},
                )
            needed = required_roles(request.path, request.method)
            if needed and g.current_user["role"] not in needed and g.current_user["role"] != "ADMIN":
                return fail("FORBIDDEN", "Bạn không có quyền thực hiện thao tác này.", 403)
        return None

    @app.get("/login")
    def login_page():
        if g.current_user:
            return redirect("/")
        return render_template("login.html")

    @app.post("/api/auth/login")
    def login():
        payload = body()
        username = text(payload.get("username"), "username", 50)
        password = str(payload.get("password") or "")
        user = get_db().execute("SELECT * FROM users WHERE username=? COLLATE NOCASE", (username,)).fetchone()
        if not user or not check_password_hash(user["password_hash"], password):
            return fail("INVALID_CREDENTIALS", "Tên đăng nhập hoặc mật khẩu không đúng.", 401)
        if user["status"] != "active":
            return fail("ACCOUNT_LOCKED", "Tài khoản đã bị khóa.", 403)
        session.clear()
        session["user_id"] = user["id"]
        session["csrf_token"] = secrets.token_urlsafe(32)
        audit(get_db(), "LOGIN", "user", user["id"])
        get_db().commit()
        data = user_json(user)
        return ok(data, "Đăng nhập thành công.", user=data, csrf_token=session["csrf_token"])

    @app.get("/api/auth/me")
    def me():
        data = user_json(g.current_user)
        return ok(data, user=data, csrf_token=session.get("csrf_token"))

    @app.post("/api/auth/logout")
    def logout():
        audit(get_db(), "LOGOUT", "user", g.current_user["id"])
        get_db().commit()
        session.clear()
        return ok(message="Đã đăng xuất.")

    @app.get("/api/roles")
    def role_list():
        return ok([{"code": value, "name": value} for value in sorted(ROLES)])

    register_master_routes(app, get_db)
    register_inbound_routes(app, get_db)
    register_outbound_routes(app, get_db)
    register_inventory_routes(app, get_db)
    register_report_routes(app, get_db)
    register_cli(app, get_db)


def required_roles(path, method):
    if (
        path.startswith("/api/users")
        or path.startswith("/api/warehouses")
        or (path.startswith("/api/categories") and method != "GET")
    ):
        return {"ADMIN"}
    if path.startswith(("/api/customers", "/api/suppliers", "/api/products")):
        return {"CS"}
    if path.startswith("/api/inbound-receipts"):
        return {"WAREHOUSE"} if path.endswith(("/inspect", "/confirm", "/cancel")) else {"CS"}
    if path.startswith(("/api/outbound-receipts", "/api/outbound-orders")):
        return {"WAREHOUSE"} if path.endswith(("/confirm", "/cancel", "/status", "/inspection")) else {"CS", "WAREHOUSE"}
    if path.startswith("/api/stocktakes"):
        return {"WAREHOUSE"}
    return set()


def user_json(row):
    return {key: row[key] for key in ("id", "username", "full_name", "email", "role", "status")}


def register_master_routes(app, get_db):
    specs = {
        "customers": ("customers", ("code", "name", "tax_code", "phone", "address")),
        "suppliers": ("suppliers", ("code", "name", "tax_code", "phone", "email", "address")),
        "warehouses": ("warehouses", ("code", "name", "address")),
    }

    for route, (table, columns) in specs.items():
        def listing(_table=table):
            rows = [dict(r) for r in get_db().execute(f"SELECT * FROM {_table} ORDER BY name").fetchall()]
            if _table == "customers":
                for row in rows:
                    row["contract_emails"] = [
                        x["email"] for x in get_db().execute(
                            "SELECT email FROM customer_contract_emails WHERE customer_id=? AND active=1 ORDER BY email",
                            (row["id"],),
                        )
                    ]
            return ok(rows, meta={"total": len(rows)}, items=rows)

        def create(_table=table, _columns=columns):
            payload = body()
            values = [text(payload.get(c), c, 120, required=c in {"code", "name"}) for c in _columns]
            try:
                cursor = get_db().execute(
                    f"INSERT INTO {_table}({','.join(_columns)}) VALUES({','.join('?' for _ in _columns)})",
                    values,
                )
                if _table == "customers":
                    save_contract_emails(get_db(), cursor.lastrowid, payload.get("contract_emails", []))
                audit(get_db(), "CREATE", _table, cursor.lastrowid)
                get_db().commit()
            except sqlite3.IntegrityError:
                get_db().rollback()
                return fail("DUPLICATE", "Mã dữ liệu đã tồn tại.", 409)
            except Exception:
                get_db().rollback()
                raise
            return ok({"id": cursor.lastrowid}, "Đã tạo dữ liệu.", 201, id=cursor.lastrowid)

        def update(item_id, _table=table, _columns=columns):
            payload = body()
            if not get_db().execute(f"SELECT 1 FROM {_table} WHERE id=?", (item_id,)).fetchone():
                return fail("NOT_FOUND", "Không tìm thấy dữ liệu.", 404)
            values = [text(payload.get(c), c, 120, required=c in {"code", "name"}) for c in _columns]
            try:
                get_db().execute(
                    f"UPDATE {_table} SET {','.join(f'{c}=?' for c in _columns)} WHERE id=?",
                    [*values, item_id],
                )
                if _table == "customers" and "contract_emails" in payload:
                    get_db().execute("DELETE FROM customer_contract_emails WHERE customer_id=?", (item_id,))
                    save_contract_emails(get_db(), item_id, payload["contract_emails"])
                audit(get_db(), "UPDATE", _table, item_id)
                get_db().commit()
            except sqlite3.IntegrityError:
                get_db().rollback()
                return fail("DUPLICATE", "Mã hoặc email đã tồn tại.", 409)
            except Exception:
                get_db().rollback()
                raise
            return ok({"id": item_id}, "Đã cập nhật dữ liệu.")

        def deactivate(item_id, _table=table):
            if not get_db().execute(f"SELECT 1 FROM {_table} WHERE id=?", (item_id,)).fetchone():
                return fail("NOT_FOUND", "Không tìm thấy dữ liệu.", 404)
            get_db().execute(f"UPDATE {_table} SET active=0 WHERE id=?", (item_id,))
            audit(get_db(), "DEACTIVATE", _table, item_id)
            get_db().commit()
            return ok({"id": item_id, "active": False}, "Đã ngừng hoạt động; lịch sử được bảo toàn.")

        app.add_url_rule(f"/api/{route}", f"{route}_list_wms", listing, methods=["GET"])
        app.add_url_rule(f"/api/{route}", f"{route}_create_wms", create, methods=["POST"])
        app.add_url_rule(f"/api/{route}/<int:item_id>", f"{route}_update_wms", update, methods=["PUT"])
        app.add_url_rule(f"/api/{route}/<int:item_id>", f"{route}_deactivate_wms", deactivate, methods=["DELETE"])


def save_contract_emails(db, customer_id, values):
    if not isinstance(values, list) or not values:
        raise ValidationError("VALIDATION_ERROR", "Khách hàng cần ít nhất một email hợp đồng.", {"contract_emails": "Bắt buộc."})
    normalized = []
    for value in values:
        email = str(value or "").strip().lower()
        if not EMAIL_RE.fullmatch(email):
            raise ValidationError("VALIDATION_ERROR", "Email hợp đồng chưa hợp lệ.", {"contract_emails": email})
        normalized.append((customer_id, email))
    db.executemany("INSERT INTO customer_contract_emails(customer_id,email) VALUES(?,?)", normalized)


def next_code(db, table, prefix):
    base = f"{prefix}-{date.today():%y%m%d}-"
    rows = db.execute(f"SELECT code FROM {table} WHERE code LIKE ?", (base + "%",)).fetchall()
    numbers = []
    for row in rows:
        try:
            numbers.append(int(row["code"].rsplit("-", 1)[1]))
        except ValueError:
            pass
    return f"{base}{max(numbers, default=0)+1:03d}"


def register_inbound_routes(app, get_db):
    @app.get("/api/inbound-receipts")
    def inbound_list():
        rows = [dict(r) for r in get_db().execute(
            """SELECT r.*,s.name supplier_name,w.name warehouse_name,
               COUNT(i.id) line_count,COALESCE(SUM(i.expected_quantity),0) total_quantity
               FROM inbound_receipts r LEFT JOIN suppliers s ON s.id=r.supplier_id
               JOIN warehouses w ON w.id=r.warehouse_id LEFT JOIN inbound_items i ON i.receipt_id=r.id
               GROUP BY r.id ORDER BY r.id DESC"""
        )]
        return ok(rows, meta={"total": len(rows)}, items=rows)

    @app.post("/api/inbound-receipts")
    def inbound_create():
        db, payload = get_db(), body()
        warehouse_id = positive_id(payload.get("warehouse_id"), "warehouse_id")
        supplier_id = positive_id(payload.get("supplier_id"), "supplier_id", False)
        items = payload.get("items")
        if not isinstance(items, list) or not items or not all(isinstance(item, dict) for item in items):
            raise ValidationError("VALIDATION_ERROR", "Phiếu nhập chưa hợp lệ.", {"items": "Cần ít nhất một dòng hàng."})
        if not db.execute("SELECT 1 FROM warehouses WHERE id=? AND active=1", (warehouse_id,)).fetchone():
            raise ValidationError("NOT_FOUND", "Không tìm thấy kho đang hoạt động.", {"warehouse_id": "Không hợp lệ."}, 404)
        if supplier_id and not db.execute("SELECT 1 FROM suppliers WHERE id=? AND active=1", (supplier_id,)).fetchone():
            raise ValidationError("NOT_FOUND", "Không tìm thấy nhà cung cấp đang hoạt động.", {"supplier_id": "Không hợp lệ."}, 404)
        timestamp = utcnow()
        try:
            db.execute("BEGIN IMMEDIATE")
            code = next_code(db, "inbound_receipts", "PN")
            cursor = db.execute(
                """INSERT INTO inbound_receipts
                   (code,supplier_id,warehouse_id,expected_date,container_no,seal_no,note,status,created_by,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,'DRAFT',?,?,?)""",
                (
                    code, supplier_id, warehouse_id,
                    iso_date(payload.get("expected_date") or str(date.today()), "expected_date"),
                    text(payload.get("container_no"), "container_no", 50, False),
                    text(payload.get("seal_no"), "seal_no", 50, False),
                    text(payload.get("note"), "note", 500, False),
                    g.current_user["id"], timestamp, timestamp,
                ),
            )
            seen = set()
            for item in items:
                product_id = positive_id(item.get("product_id"), "product_id")
                pallet_id = text(item.get("pallet_id"), "pallet_id", 60).upper()
                if pallet_id in seen:
                    raise ValidationError("DUPLICATE_LINE", "Pallet bị trùng trong phiếu.", {"pallet_id": pallet_id}, 409)
                seen.add(pallet_id)
                product = db.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
                if not product:
                    raise ValidationError("NOT_FOUND", "Không tìm thấy hàng hóa.", status=404)
                unit = text(item.get("unit") or product["unit"], "unit", 30)
                if unit.casefold() != product["unit"].casefold():
                    raise ValidationError("UNIT_MISMATCH", "Đơn vị nhập phải đúng đơn vị của hàng hóa.", {"unit": product["unit"]}, 409)
                db.execute(
                    """INSERT INTO inbound_items
                       (receipt_id,product_id,expected_quantity,unit,pallet_id,barcode,location,expiry_date)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    (
                        cursor.lastrowid, product_id, positive_decimal(item.get("quantity"), "quantity"),
                        unit, pallet_id, text(item.get("barcode"), "barcode", 80),
                        text(item.get("location"), "location", 50, False),
                        iso_date(item.get("expiry_date"), "expiry_date", False),
                    ),
                )
            audit(db, "CREATE", "inbound_receipt", cursor.lastrowid, {"code": code})
            db.commit()
        except Exception:
            db.rollback()
            raise
        return ok({"id": cursor.lastrowid, "code": code}, "Đã tạo phiếu nhập.", 201, id=cursor.lastrowid, code=code)

    @app.get("/api/inbound-receipts/<int:receipt_id>")
    def inbound_detail(receipt_id):
        receipt = get_db().execute("SELECT * FROM inbound_receipts WHERE id=?", (receipt_id,)).fetchone()
        if not receipt:
            return fail("NOT_FOUND", "Không tìm thấy phiếu nhập.", 404)
        result = dict(receipt)
        result["items"] = [dict(r) for r in get_db().execute(
            """SELECT i.*,p.sku,p.name FROM inbound_items i JOIN products p ON p.id=i.product_id
               WHERE i.receipt_id=? ORDER BY i.id""", (receipt_id,)
        )]
        return ok(result, item=result)

    @app.post("/api/inbound-receipts/<int:receipt_id>/inspect")
    def inbound_inspect(receipt_id):
        db, payload = get_db(), body()
        receipt = db.execute("SELECT * FROM inbound_receipts WHERE id=?", (receipt_id,)).fetchone()
        if not receipt:
            return fail("NOT_FOUND", "Không tìm thấy phiếu nhập.", 404)
        if receipt["status"] not in {"DRAFT", "INSPECTED"}:
            return fail("INVALID_STATE", "Phiếu không còn ở trạng thái kiểm tra.", 409)
        submitted = payload.get("items")
        if not isinstance(submitted, list) or not all(isinstance(item, dict) for item in submitted):
            raise ValidationError("VALIDATION_ERROR", "Danh sách kiểm tra không hợp lệ.")
        expected_ids = {r["id"] for r in db.execute("SELECT id FROM inbound_items WHERE receipt_id=?", (receipt_id,))}
        submitted_ids = {positive_id(i.get("item_id"), "item_id") for i in submitted}
        if submitted_ids != expected_ids:
            raise ValidationError("ITEM_SET_MISMATCH", "Phải kiểm tra đủ các dòng hàng.", status=409)
        try:
            db.execute("BEGIN IMMEDIATE")
            for item in submitted:
                accepted = positive_decimal(item.get("accepted_quantity"), "accepted_quantity", True)
                rejected = positive_decimal(item.get("rejected_quantity", 0), "rejected_quantity", True)
                original = db.execute("SELECT expected_quantity FROM inbound_items WHERE id=?", (item["item_id"],)).fetchone()[0]
                if abs(accepted + rejected - original) > 0.001:
                    raise ValidationError("QUANTITY_MISMATCH", "Số đạt + số từ chối phải bằng số dự kiến.", status=409)
                reason = text(item.get("reject_reason"), "reject_reason", 255, False)
                if rejected and not reason:
                    raise ValidationError("VALIDATION_ERROR", "Hàng từ chối phải có lý do.", {"reject_reason": "Bắt buộc."})
                db.execute(
                    "UPDATE inbound_items SET accepted_quantity=?,rejected_quantity=?,reject_reason=? WHERE id=?",
                    (accepted, rejected, reason, item["item_id"]),
                )
            db.execute("UPDATE inbound_receipts SET status='INSPECTED',updated_at=? WHERE id=?", (utcnow(), receipt_id))
            audit(db, "INSPECT", "inbound_receipt", receipt_id)
            db.commit()
        except Exception:
            db.rollback()
            raise
        return ok({"id": receipt_id, "status": "INSPECTED"}, "Đã lưu kết quả kiểm tra.")

    @app.post("/api/inbound-receipts/<int:receipt_id>/confirm")
    def inbound_confirm(receipt_id):
        db = get_db()
        try:
            db.execute("BEGIN IMMEDIATE")
            receipt = db.execute("SELECT * FROM inbound_receipts WHERE id=?", (receipt_id,)).fetchone()
            if not receipt:
                db.rollback()
                return fail("NOT_FOUND", "Không tìm thấy phiếu nhập.", 404)
            if receipt["status"] == "CONFIRMED":
                db.rollback()
                return ok({"id": receipt_id, "status": "CONFIRMED", "idempotent": True}, "Phiếu đã được xác nhận trước đó.")
            if receipt["status"] != "INSPECTED":
                db.rollback()
                return fail("INVALID_STATE", "Cần hoàn tất kiểm tra trước khi xác nhận.", 409)
            items = db.execute("SELECT * FROM inbound_items WHERE receipt_id=?", (receipt_id,)).fetchall()
            timestamp = utcnow()
            for item in items:
                accepted = item["accepted_quantity"]
                if accepted is None:
                    raise ValidationError("INSPECTION_REQUIRED", "Chưa kiểm tra đủ dòng hàng.", status=409)
                if accepted <= 0:
                    continue
                product = db.execute(
                    "SELECT quantity,max_stock FROM products WHERE id=?", (item["product_id"],)
                ).fetchone()
                if float(product["quantity"]) + float(accepted) > float(product["max_stock"]) + 0.001:
                    raise ValidationError(
                        "STOCK_CAPACITY_EXCEEDED",
                        "Số lượng nhập vượt tồn tối đa của hàng hóa.",
                        {"product_id": item["product_id"], "max_stock": product["max_stock"]},
                        409,
                    )
                lot = db.execute(
                    """INSERT INTO inventory_lots
                       (pallet_id,barcode,product_id,warehouse_id,unit,location,received_at,expiry_date,quantity)
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                    (
                        item["pallet_id"], item["barcode"], item["product_id"], receipt["warehouse_id"],
                        item["unit"], item["location"], timestamp, item["expiry_date"], accepted,
                    ),
                )
                db.execute("UPDATE products SET quantity=quantity+?,updated_at=? WHERE id=?", (accepted, timestamp, item["product_id"]))
                db.execute(
                    """INSERT INTO wms_movements
                       (product_id,lot_id,warehouse_id,movement_type,quantity_change,quantity_before,quantity_after,
                        reference_type,reference_id,actor_id,note,created_at)
                       VALUES(?,?,?,'INBOUND',?,?,?,?,?,?,?,?)""",
                    (
                        item["product_id"], lot.lastrowid, receipt["warehouse_id"], accepted, 0, accepted,
                        "INBOUND_RECEIPT", receipt_id, g.current_user["id"], "Xác nhận nhập kho", timestamp,
                    ),
                )
            db.execute(
                "UPDATE inbound_receipts SET status='CONFIRMED',confirmed_by=?,confirmed_at=?,updated_at=? WHERE id=?",
                (g.current_user["id"], timestamp, timestamp, receipt_id),
            )
            audit(db, "CONFIRM", "inbound_receipt", receipt_id)
            db.commit()
        except sqlite3.IntegrityError:
            db.rollback()
            return fail("DUPLICATE_CONFIRMATION", "Phiếu hoặc pallet đã được ghi nhận.", 409)
        except Exception:
            db.rollback()
            raise
        return ok({"id": receipt_id, "status": "CONFIRMED", "idempotent": False}, "Đã nhập kho.")

    @app.post("/api/inbound-receipts/<int:receipt_id>/cancel")
    def inbound_cancel(receipt_id):
        return cancel_draft(get_db(), "inbound_receipts", receipt_id, "phiếu nhập")


def register_outbound_routes(app, get_db):
    @app.get("/api/outbound-receipts")
    def outbound_list():
        rows = [dict(r) for r in get_db().execute(
            """SELECT o.*,COUNT(i.id) line_count,COALESCE(SUM(i.quantity),0) total_quantity
               FROM outbound_orders o LEFT JOIN outbound_items i ON i.order_id=o.id
               GROUP BY o.id ORDER BY o.id DESC"""
        )]
        return ok(rows, meta={"total": len(rows)}, items=rows)

    @app.post("/api/outbound-receipts")
    def outbound_create():
        db, payload = get_db(), body()
        customer_id = positive_id(payload.get("customer_id"), "customer_id")
        warehouse_id = positive_id(payload.get("warehouse_id"), "warehouse_id")
        request_email = str(payload.get("request_email") or "").strip().lower()
        customer = db.execute("SELECT * FROM customers WHERE id=? AND active=1", (customer_id,)).fetchone()
        authorized = db.execute(
            "SELECT 1 FROM customer_contract_emails WHERE customer_id=? AND email=? COLLATE NOCASE AND active=1",
            (customer_id, request_email),
        ).fetchone()
        if not customer:
            return fail("CUSTOMER_NOT_FOUND", "Không tìm thấy khách hàng.", 404)
        if not authorized:
            return fail("CONTRACT_EMAIL_REQUIRED", "Email yêu cầu không thuộc hợp đồng khách hàng.", 422, {"request_email": "Email chưa đăng ký."})
        items = payload.get("items")
        if not isinstance(items, list) or not items or not all(isinstance(item, dict) for item in items):
            raise ValidationError("VALIDATION_ERROR", "Phiếu xuất chưa hợp lệ.", {"items": "Cần ít nhất một dòng."})
        if not db.execute("SELECT 1 FROM warehouses WHERE id=? AND active=1", (warehouse_id,)).fetchone():
            raise ValidationError("NOT_FOUND", "Không tìm thấy kho đang hoạt động.", {"warehouse_id": "Không hợp lệ."}, 404)
        timestamp = utcnow()
        try:
            db.execute("BEGIN IMMEDIATE")
            code = next_code(db, "outbound_orders", "PX")
            cursor = db.execute(
                """INSERT INTO outbound_orders
                   (code,outbound_date,customer_name,tax_code,phone,address,container_no,seal_no,vehicle_no,c_number,
                    note,status,created_by,created_at,updated_at,customer_id,request_email,warehouse_id)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,'pending',?,?,?,?,?,?)""",
                (
                    code, iso_date(payload.get("outbound_date") or str(date.today()), "outbound_date"),
                    customer["name"], customer["tax_code"], customer["phone"], customer["address"],
                    text(payload.get("container_no"), "container_no", 50, False),
                    text(payload.get("seal_no"), "seal_no", 50, False),
                    text(payload.get("vehicle_no"), "vehicle_no", 30, False),
                    text(payload.get("c_number"), "c_number", 30, False),
                    text(payload.get("note"), "note", 500, False),
                    g.current_user["full_name"], timestamp, timestamp, customer_id, request_email, warehouse_id,
                ),
            )
            seen = set()
            for item in items:
                product_id = positive_id(item.get("product_id"), "product_id")
                if product_id in seen:
                    raise ValidationError("DUPLICATE_LINE", "Hàng hóa bị trùng trong phiếu.", status=409)
                seen.add(product_id)
                product = db.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
                if not product:
                    raise ValidationError("NOT_FOUND", "Không tìm thấy hàng hóa.", status=404)
                unit = text(item.get("unit") or product["unit"], "unit", 30)
                if unit.casefold() != product["unit"].casefold():
                    raise ValidationError("UNIT_MISMATCH", "Không được xuất khác đơn vị đã nhập.", status=409)
                db.execute(
                    "INSERT INTO outbound_items(order_id,product_id,quantity,unit_price) VALUES(?,?,?,?)",
                    (cursor.lastrowid, product_id, positive_decimal(item.get("quantity"), "quantity"), product["unit_price"]),
                )
            audit(db, "CREATE", "outbound_receipt", cursor.lastrowid, {"request_email": request_email})
            db.commit()
        except Exception:
            db.rollback()
            raise
        shortages = compute_shortages(db, cursor.lastrowid)
        return ok(
            {"id": cursor.lastrowid, "code": code, "stock_available": not shortages, "shortages": shortages},
            "Đã tạo phiếu xuất.", 201, id=cursor.lastrowid, code=code,
        )

    @app.get("/api/outbound-receipts/<int:order_id>")
    def outbound_detail(order_id):
        order = get_db().execute("SELECT * FROM outbound_orders WHERE id=?", (order_id,)).fetchone()
        if not order:
            return fail("NOT_FOUND", "Không tìm thấy phiếu xuất.", 404)
        result = dict(order)
        result["items"] = [dict(r) for r in get_db().execute(
            """SELECT i.*,p.sku,p.name,p.unit FROM outbound_items i JOIN products p ON p.id=i.product_id
               WHERE i.order_id=? ORDER BY i.id""", (order_id,)
        )]
        return ok(result, item=result)

    @app.get("/api/outbound-receipts/<int:order_id>/check-stock")
    def outbound_check(order_id):
        if not get_db().execute("SELECT 1 FROM outbound_orders WHERE id=?", (order_id,)).fetchone():
            return fail("NOT_FOUND", "Không tìm thấy phiếu xuất.", 404)
        shortages = compute_shortages(get_db(), order_id)
        return ok({"valid": not shortages, "shortages": shortages}, valid=not shortages, shortages=shortages)

    @app.get("/api/outbound-receipts/<int:order_id>/picking-list")
    def picking_list(order_id):
        if not get_db().execute("SELECT 1 FROM outbound_orders WHERE id=?", (order_id,)).fetchone():
            return fail("NOT_FOUND", "Không tìm thấy phiếu xuất.", 404)
        shortages = compute_shortages(get_db(), order_id)
        if shortages:
            return fail("INSUFFICIENT_STOCK", "Không đủ tồn kho để tạo picking list.", 409, {"shortages": shortages})
        picks = plan_picks(get_db(), order_id)
        return ok(picks, meta={"strategy": "FEFO_THEN_FIFO"}, items=picks)

    @app.post("/api/outbound-receipts/<int:order_id>/confirm")
    def outbound_confirm(order_id):
        db = get_db()
        try:
            db.execute("BEGIN IMMEDIATE")
            order = db.execute("SELECT * FROM outbound_orders WHERE id=?", (order_id,)).fetchone()
            if not order:
                db.rollback()
                return fail("NOT_FOUND", "Không tìm thấy phiếu xuất.", 404)
            if order["status"] == "completed":
                db.rollback()
                return ok({"id": order_id, "status": "completed", "idempotent": True}, "Phiếu đã xác nhận trước đó.")
            if order["status"] == "cancelled":
                db.rollback()
                return fail("INVALID_STATE", "Phiếu đã hủy.", 409)
            assert_stock_totals(db, order_id)
            shortages = compute_shortages(db, order_id)
            if shortages:
                db.rollback()
                return fail("INSUFFICIENT_STOCK", "Không đủ tồn kho.", 409, {"shortages": shortages})
            picks = plan_picks(db, order_id)
            timestamp = utcnow()
            for pick in picks:
                lot = db.execute("SELECT quantity FROM inventory_lots WHERE id=?", (pick["lot_id"],)).fetchone()
                before, quantity = float(lot["quantity"]), float(pick["quantity"])
                if before < quantity:
                    raise ValidationError("CONCURRENT_STOCK_CHANGE", "Tồn kho vừa thay đổi; vui lòng kiểm tra lại.", status=409)
                after = before - quantity
                db.execute("UPDATE inventory_lots SET quantity=? WHERE id=?", (after, pick["lot_id"]))
                updated = db.execute(
                    "UPDATE products SET quantity=quantity-?,updated_at=? WHERE id=? AND quantity>=?",
                    (quantity, timestamp, pick["product_id"], quantity),
                )
                if updated.rowcount != 1:
                    raise ValidationError(
                        "STOCK_INVARIANT_BROKEN",
                        "Tồn tổng hợp không khớp tồn theo lô; phiếu chưa được xuất.",
                        status=409,
                    )
                db.execute(
                    "INSERT INTO outbound_allocations(order_id,outbound_item_id,lot_id,quantity,created_at) VALUES(?,?,?,?,?)",
                    (order_id, pick["outbound_item_id"], pick["lot_id"], quantity, timestamp),
                )
                db.execute(
                    """INSERT INTO wms_movements
                       (product_id,lot_id,warehouse_id,movement_type,quantity_change,quantity_before,quantity_after,
                        reference_type,reference_id,actor_id,note,created_at)
                       VALUES(?,?,?,'OUTBOUND',?,?,?,?,?,?,?,?)""",
                    (
                        pick["product_id"], pick["lot_id"], order["warehouse_id"], -quantity, before, after,
                        "OUTBOUND_RECEIPT", order_id, g.current_user["id"], "FEFO/FIFO picking", timestamp,
                    ),
                )
            db.execute(
                """UPDATE outbound_orders SET status='completed',confirmed_by=?,confirmed_at=?,updated_at=? WHERE id=?""",
                (g.current_user["id"], timestamp, timestamp, order_id),
            )
            audit(db, "CONFIRM", "outbound_receipt", order_id)
            db.commit()
        except sqlite3.IntegrityError:
            db.rollback()
            return fail("DUPLICATE_CONFIRMATION", "Phiếu đã được ghi nhận.", 409)
        except Exception:
            db.rollback()
            raise
        return ok({"id": order_id, "status": "completed", "idempotent": False}, "Đã xuất kho.")

    @app.post("/api/outbound-receipts/<int:order_id>/cancel")
    def outbound_cancel(order_id):
        return cancel_draft(get_db(), "outbound_orders", order_id, "phiếu xuất")


def compute_shortages(db, order_id):
    order = db.execute("SELECT warehouse_id FROM outbound_orders WHERE id=?", (order_id,)).fetchone()
    if not order:
        return [{"message": "Không tìm thấy phiếu."}]
    rows = db.execute(
        """SELECT i.product_id,p.sku,p.name,i.quantity requested,
           COALESCE(SUM(l.quantity-l.reserved_quantity),0) available
           FROM outbound_items i JOIN products p ON p.id=i.product_id
           LEFT JOIN inventory_lots l ON l.product_id=i.product_id AND l.warehouse_id=? AND l.active=1
             AND (l.expiry_date IS NULL OR l.expiry_date>=?)
           WHERE i.order_id=? GROUP BY i.id HAVING available < requested""",
        (order["warehouse_id"], date.today().isoformat(), order_id),
    )
    return [dict(r) for r in rows]


def plan_picks(db, order_id):
    order = db.execute("SELECT warehouse_id FROM outbound_orders WHERE id=?", (order_id,)).fetchone()
    picks = []
    for item in db.execute("SELECT * FROM outbound_items WHERE order_id=? ORDER BY id", (order_id,)):
        remaining = float(item["quantity"])
        lots = db.execute(
            """SELECT l.*,p.sku,p.name FROM inventory_lots l JOIN products p ON p.id=l.product_id
               WHERE l.product_id=? AND l.warehouse_id=? AND l.active=1 AND l.quantity>l.reserved_quantity
                 AND (l.expiry_date IS NULL OR l.expiry_date>=?)
               ORDER BY CASE WHEN l.expiry_date IS NULL THEN 1 ELSE 0 END,l.expiry_date,l.received_at,l.id""",
            (item["product_id"], order["warehouse_id"], date.today().isoformat()),
        )
        for lot in lots:
            available = float(lot["quantity"] - lot["reserved_quantity"])
            take = min(remaining, available)
            if take:
                picks.append({
                    "outbound_item_id": item["id"], "product_id": item["product_id"],
                    "sku": lot["sku"], "name": lot["name"], "lot_id": lot["id"],
                    "pallet_id": lot["pallet_id"], "barcode": lot["barcode"],
                    "unit": lot["unit"], "location": lot["location"],
                    "expiry_date": lot["expiry_date"], "quantity": take,
                })
                remaining -= take
            if remaining <= 0.0001:
                break
    return picks


def assert_stock_totals(db, order_id):
    inconsistent = db.execute(
        """SELECT p.id,p.sku,p.quantity,COALESCE(SUM(l.quantity),0) lot_quantity
           FROM outbound_items i JOIN products p ON p.id=i.product_id
           LEFT JOIN inventory_lots l ON l.product_id=p.id AND l.active=1
           WHERE i.order_id=? GROUP BY p.id
           HAVING ABS(p.quantity-COALESCE(SUM(l.quantity),0))>0.001""",
        (order_id,),
    ).fetchall()
    if inconsistent:
        raise ValidationError(
            "STOCK_INVARIANT_BROKEN",
            "Tồn tổng hợp không khớp tồn theo lô; cần đối soát trước khi xuất.",
            {"products": [row["sku"] for row in inconsistent]},
            409,
        )


def cancel_draft(db, table, item_id, label):
    row = db.execute(f"SELECT status FROM {table} WHERE id=?", (item_id,)).fetchone()
    if not row:
        return fail("NOT_FOUND", f"Không tìm thấy {label}.", 404)
    if row["status"] in {"CONFIRMED", "completed", "CANCELLED", "cancelled"}:
        return fail("INVALID_STATE", f"Không thể hủy {label} đã hoàn tất/hủy.", 409)
    status = "CANCELLED" if table == "inbound_receipts" else "cancelled"
    db.execute(f"UPDATE {table} SET status=? WHERE id=?", (status, item_id))
    audit(db, "CANCEL", table, item_id)
    db.commit()
    return ok({"id": item_id, "status": status}, f"Đã hủy {label}.")


def register_inventory_routes(app, get_db):
    @app.get("/api/inventory")
    def inventory():
        q = str(request.args.get("q") or "").strip()
        warehouse_id = request.args.get("warehouse_id", type=int)
        clauses, params = ["l.active=1"], []
        if q:
            clauses.append("(p.sku LIKE ? OR p.name LIKE ? OR l.barcode LIKE ? OR l.pallet_id LIKE ?)")
            params += [f"%{q}%"] * 4
        if warehouse_id:
            clauses.append("l.warehouse_id=?")
            params.append(warehouse_id)
        rows = [dict(r) for r in get_db().execute(
            f"""SELECT l.*,p.sku,p.name,w.code warehouse_code,w.name warehouse_name,
                l.quantity-l.reserved_quantity available_quantity
                FROM inventory_lots l JOIN products p ON p.id=l.product_id
                JOIN warehouses w ON w.id=l.warehouse_id WHERE {' AND '.join(clauses)}
                ORDER BY p.name,l.expiry_date,l.received_at""", params
        )]
        return ok(rows, meta={"total": len(rows)}, items=rows)

    @app.get("/api/stock-movements")
    def movements():
        rows = [dict(r) for r in get_db().execute(
            """SELECT m.*,p.sku,p.name,l.pallet_id,w.name warehouse_name,u.full_name actor_name
               FROM wms_movements m JOIN products p ON p.id=m.product_id
               LEFT JOIN inventory_lots l ON l.id=m.lot_id JOIN warehouses w ON w.id=m.warehouse_id
               JOIN users u ON u.id=m.actor_id ORDER BY m.id DESC LIMIT 500"""
        )]
        return ok(rows, meta={"total": len(rows)}, items=rows)

    @app.get("/api/stocktakes")
    def stocktake_list():
        rows = [dict(r) for r in get_db().execute(
            """SELECT s.*,w.name warehouse_name,COUNT(i.id) line_count
               FROM stocktake_headers s JOIN warehouses w ON w.id=s.warehouse_id
               LEFT JOIN stocktake_items i ON i.stocktake_id=s.id GROUP BY s.id ORDER BY s.id DESC"""
        )]
        return ok(rows, meta={"total": len(rows)}, items=rows)

    @app.post("/api/stocktakes")
    def stocktake_create():
        db, payload = get_db(), body()
        warehouse_id = positive_id(payload.get("warehouse_id"), "warehouse_id")
        reason = text(payload.get("reason"), "reason", 255)
        items = payload.get("items")
        if not isinstance(items, list) or not items or not all(isinstance(item, dict) for item in items):
            raise ValidationError("VALIDATION_ERROR", "Phiếu kiểm kê cần ít nhất một dòng.", {"items": "Bắt buộc."})
        if not db.execute("SELECT 1 FROM warehouses WHERE id=? AND active=1", (warehouse_id,)).fetchone():
            raise ValidationError("NOT_FOUND", "Không tìm thấy kho đang hoạt động.", {"warehouse_id": "Không hợp lệ."}, 404)
        timestamp = utcnow()
        try:
            db.execute("BEGIN IMMEDIATE")
            code = next_code(db, "stocktake_headers", "KK")
            cursor = db.execute(
                """INSERT INTO stocktake_headers(code,warehouse_id,status,reason,created_by,created_at)
                   VALUES(?,?,'DRAFT',?,?,?)""",
                (code, warehouse_id, reason, g.current_user["id"], timestamp),
            )
            seen = set()
            for item in items:
                lot_id = positive_id(item.get("lot_id"), "lot_id")
                if lot_id in seen:
                    raise ValidationError("DUPLICATE_LINE", "Lô bị trùng trong kiểm kê.", status=409)
                seen.add(lot_id)
                lot = db.execute("SELECT * FROM inventory_lots WHERE id=? AND warehouse_id=?", (lot_id, warehouse_id)).fetchone()
                if not lot:
                    raise ValidationError("NOT_FOUND", "Không tìm thấy lô trong kho.", status=404)
                actual = positive_decimal(item.get("actual_quantity"), "actual_quantity", True)
                db.execute(
                    """INSERT INTO stocktake_items(stocktake_id,lot_id,system_quantity,actual_quantity,difference,note)
                       VALUES(?,?,?,?,?,?)""",
                    (cursor.lastrowid, lot_id, lot["quantity"], actual, actual-lot["quantity"], text(item.get("note"), "note", 255, False)),
                )
            audit(db, "CREATE", "stocktake", cursor.lastrowid)
            db.commit()
        except Exception:
            db.rollback()
            raise
        return ok({"id": cursor.lastrowid, "code": code}, "Đã tạo phiếu kiểm kê.", 201, id=cursor.lastrowid)

    @app.post("/api/stocktakes/<int:stocktake_id>/confirm")
    def stocktake_confirm(stocktake_id):
        db = get_db()
        try:
            db.execute("BEGIN IMMEDIATE")
            header = db.execute("SELECT * FROM stocktake_headers WHERE id=?", (stocktake_id,)).fetchone()
            if not header:
                db.rollback()
                return fail("NOT_FOUND", "Không tìm thấy phiếu kiểm kê.", 404)
            if header["status"] == "CONFIRMED":
                db.rollback()
                return ok({"id": stocktake_id, "status": "CONFIRMED", "idempotent": True}, "Phiếu đã xác nhận.")
            if header["status"] != "DRAFT":
                db.rollback()
                return fail("INVALID_STATE", "Phiếu không thể xác nhận.", 409)
            timestamp = utcnow()
            for item in db.execute("SELECT * FROM stocktake_items WHERE stocktake_id=?", (stocktake_id,)):
                lot = db.execute("SELECT * FROM inventory_lots WHERE id=?", (item["lot_id"],)).fetchone()
                before, after = float(lot["quantity"]), float(item["actual_quantity"])
                # Recheck snapshot prevents overwrite when stock changed since counting.
                if abs(before - float(item["system_quantity"])) > 0.001:
                    raise ValidationError("CONCURRENT_STOCK_CHANGE", "Tồn kho đã thay đổi sau lúc đếm.", status=409)
                product = db.execute(
                    "SELECT quantity,max_stock FROM products WHERE id=?", (lot["product_id"],)
                ).fetchone()
                if float(product["quantity"]) + after - before > float(product["max_stock"]) + 0.001:
                    raise ValidationError(
                        "STOCK_CAPACITY_EXCEEDED",
                        "Kết quả kiểm kê vượt tồn tối đa của hàng hóa.",
                        {"product_id": lot["product_id"], "max_stock": product["max_stock"]},
                        409,
                    )
                db.execute("UPDATE inventory_lots SET quantity=? WHERE id=?", (after, lot["id"]))
                db.execute("UPDATE products SET quantity=quantity+?,updated_at=? WHERE id=?", (after-before, timestamp, lot["product_id"]))
                if abs(after-before) > 0.001:
                    db.execute(
                        """INSERT INTO wms_movements
                           (product_id,lot_id,warehouse_id,movement_type,quantity_change,quantity_before,quantity_after,
                            reference_type,reference_id,actor_id,note,created_at)
                           VALUES(?,?,?,'STOCKTAKE',?,?,?,?,?,?,?,?)""",
                        (
                            lot["product_id"], lot["id"], lot["warehouse_id"], after-before, before, after,
                            "STOCKTAKE", stocktake_id, g.current_user["id"], header["reason"], timestamp,
                        ),
                    )
            db.execute(
                "UPDATE stocktake_headers SET status='CONFIRMED',confirmed_by=?,confirmed_at=? WHERE id=?",
                (g.current_user["id"], timestamp, stocktake_id),
            )
            audit(db, "CONFIRM", "stocktake", stocktake_id)
            db.commit()
        except Exception:
            db.rollback()
            raise
        return ok({"id": stocktake_id, "status": "CONFIRMED", "idempotent": False}, "Đã cân bằng tồn kho.")


def register_report_routes(app, get_db):
    @app.get("/api/reports/summary")
    def report_summary():
        db = get_db()
        summary = dict(db.execute(
            """SELECT COUNT(DISTINCT p.id) products,COUNT(DISTINCT l.id) lots,
               COALESCE(SUM(l.quantity),0) quantity,
               SUM(CASE WHEN p.quantity<=p.min_stock THEN 1 ELSE 0 END) low_stock
               FROM products p LEFT JOIN inventory_lots l ON l.product_id=p.id AND l.active=1"""
        ).fetchone())
        summary["inbound_confirmed"] = db.execute("SELECT COUNT(*) FROM inbound_receipts WHERE status='CONFIRMED'").fetchone()[0]
        summary["outbound_completed"] = db.execute("SELECT COUNT(*) FROM outbound_orders WHERE status='completed'").fetchone()[0]
        return ok(summary, **summary)

    @app.get("/api/reports/movements.csv")
    def movements_csv():
        output = io.StringIO()
        output.write("\ufeff")
        writer = csv.writer(output)
        writer.writerow(["Thời gian", "Loại", "SKU", "Hàng hóa", "Pallet", "Thay đổi", "Tồn trước", "Tồn sau", "Tham chiếu"])
        for row in get_db().execute(
            """SELECT m.*,p.sku,p.name,l.pallet_id FROM wms_movements m
               JOIN products p ON p.id=m.product_id LEFT JOIN inventory_lots l ON l.id=m.lot_id
               ORDER BY m.created_at DESC"""
        ):
            writer.writerow([
                row["created_at"], row["movement_type"], row["sku"], row["name"], row["pallet_id"] or "",
                row["quantity_change"], row["quantity_before"], row["quantity_after"],
                f"{row['reference_type']} #{row['reference_id']}",
            ])
        return current_app.response_class(
            output.getvalue(),
            mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=bao-cao-bien-dong.csv"},
        )


def register_cli(app, get_db):
    @app.cli.command("seed-demo")
    def seed_demo():
        init_extension(get_db())
        click.echo("Đã bổ sung dữ liệu demo WMS.")

    @app.cli.command("backup-db")
    @click.argument("destination", type=click.Path(path_type=Path))
    def backup_db(destination):
        source = Path(current_app.config["DATABASE"])
        if current_app.config.get("DATABASE_URL", "").startswith(("mssql", "sqlserver")):
            raise click.ClickException("SQL Server cần dùng BACKUP DATABASE theo hướng dẫn vận hành.")
        if current_app.config["DATABASE"] != ":memory:" and source.resolve() == destination.resolve():
            raise click.ClickException("Tệp backup phải khác database đang chạy.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        connection = get_db()
        handle = tempfile.NamedTemporaryFile(prefix=".wms-backup-", suffix=".sqlite3", dir=destination.parent, delete=False)
        temporary = Path(handle.name)
        handle.close()
        target = sqlite3.connect(temporary)
        try:
            connection.backup(target)
            target.close()
            validate_sqlite_backup(temporary)
            os.replace(temporary, destination)
        finally:
            try:
                target.close()
            finally:
                temporary.unlink(missing_ok=True)
        click.echo(f"Đã sao lưu: {destination}")

    @app.cli.command("restore-db")
    @click.argument("source", type=click.Path(exists=True, path_type=Path))
    @click.option("--yes", is_flag=True, help="Xác nhận ghi đè database hiện tại.")
    def restore_db(source, yes):
        if not yes:
            raise click.ClickException("Thêm --yes để xác nhận phục hồi.")
        destination = Path(current_app.config["DATABASE"])
        if current_app.config.get("DATABASE_URL", "").startswith(("mssql", "sqlserver")):
            raise click.ClickException("SQL Server cần dùng RESTORE DATABASE theo hướng dẫn vận hành.")
        if current_app.config["DATABASE"] == ":memory:":
            raise click.ClickException("Không thể phục hồi đè lên database :memory:.")
        if source.resolve() == destination.resolve():
            raise click.ClickException("Tệp phục hồi phải khác database đang chạy.")
        try:
            validate_sqlite_backup(source)
        except (sqlite3.Error, ValueError) as error:
            raise click.ClickException(f"Tệp phục hồi không hợp lệ: {error}") from error
        destination.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(prefix=".wms-restore-", suffix=".sqlite3", dir=destination.parent, delete=False)
        temporary = Path(handle.name)
        handle.close()
        source_connection = sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)
        target_connection = sqlite3.connect(temporary)
        try:
            source_connection.backup(target_connection)
        finally:
            target_connection.close()
            source_connection.close()
        validate_sqlite_backup(temporary)
        get_db().close()
        g.pop("db", None)
        try:
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        click.echo(f"Đã phục hồi: {destination}")


def validate_sqlite_backup(path):
    uri = path.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        result = connection.execute("PRAGMA quick_check").fetchone()
        if not result or result[0] != "ok":
            raise ValueError("SQLite quick_check thất bại.")
        if connection.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("Database có khóa ngoại không nhất quán.")
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        required = {"products", "outbound_orders", "users", "inventory_lots"}
        missing = required - tables
        if missing:
            raise ValueError("Thiếu bảng bắt buộc: " + ", ".join(sorted(missing)))
    finally:
        connection.close()
