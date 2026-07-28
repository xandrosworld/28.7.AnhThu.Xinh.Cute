"""DNP Logistics - phân hệ Hàng hóa và Xuất kho."""

from __future__ import annotations

import os
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from flask import Flask, g, jsonify, render_template, request


BASE_DIR = Path(__file__).resolve().parent
ORDER_STATUSES = {"pending", "processing", "completed", "cancelled"}
EDITABLE_STATUSES = {"pending", "processing"}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_mapping(
        DATABASE=os.environ.get("DNP_DATABASE", str(BASE_DIR / "instance" / "dnp_wms.sqlite3")),
        JSON_AS_ASCII=False,
    )
    if test_config:
        app.config.update(test_config)

    Path(app.config["DATABASE"]).parent.mkdir(parents=True, exist_ok=True)

    def get_db() -> sqlite3.Connection:
        if "db" not in g:
            g.db = sqlite3.connect(app.config["DATABASE"])
            g.db.row_factory = sqlite3.Row
            g.db.execute("PRAGMA foreign_keys = ON")
            g.db.execute("PRAGMA busy_timeout = 5000")
        return g.db

    @app.teardown_appcontext
    def close_db(_error=None):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    def init_db(seed: bool = True) -> None:
        db = get_db()
        db.executescript(SCHEMA)
        if seed and db.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 0:
            seed_db(db)
        db.commit()

    app.get_db = get_db  # type: ignore[attr-defined]
    app.init_db = init_db  # type: ignore[attr-defined]

    with app.app_context():
        init_db()

    register_page_routes(app)
    register_api_routes(app, get_db)
    return app


def register_page_routes(app: Flask) -> None:
    pages = {
        "/": ("products.html", "products"),
        "/hang-hoa": ("products.html", "products"),
        "/hang-hoa/them": ("product_form.html", "product-create"),
        "/hang-hoa/<int:item_id>": ("product_detail.html", "product-detail"),
        "/hang-hoa/<int:item_id>/sua": ("product_form.html", "product-edit"),
        "/xuat-kho": ("orders.html", "orders"),
        "/xuat-kho/tao": ("order_form.html", "order-create"),
        "/xuat-kho/<int:item_id>": ("order_detail.html", "order-detail"),
        "/xuat-kho/<int:item_id>/sua": ("order_form.html", "order-edit"),
        "/xuat-kho/<int:item_id>/kiem-tra": ("inspection.html", "inspection"),
        "/lich-su-xuat-kho": ("history.html", "history"),
    }

    for index, (rule, (template, page)) in enumerate(pages.items()):
        endpoint = f"page_{index}"

        def view(item_id=None, _template=template, _page=page):
            return render_template(_template, page=_page, item_id=item_id)

        app.add_url_rule(rule, endpoint, view)

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "dnp-logistics-outbound"}


def register_api_routes(app: Flask, get_db) -> None:
    @app.errorhandler(ApiError)
    def api_error(error):
        return jsonify({"error": error.message, "details": error.details}), error.status

    @app.get("/api/categories")
    def categories_list():
        rows = get_db().execute(
            """SELECT c.*, COUNT(p.id) AS product_count
               FROM categories c LEFT JOIN products p ON p.category_id = c.id
               GROUP BY c.id ORDER BY c.name"""
        ).fetchall()
        return jsonify([dict(row) for row in rows])

    @app.post("/api/categories")
    def category_create():
        payload = json_body()
        name = required_text(payload, "name", 80)
        code = required_text(payload, "code", 20).upper()
        try:
            cursor = get_db().execute(
                "INSERT INTO categories(code, name, description) VALUES (?, ?, ?)",
                (code, name, clean_text(payload.get("description"), 255)),
            )
            get_db().commit()
        except sqlite3.IntegrityError:
            raise ApiError("Mã hoặc tên danh mục đã tồn tại.", 409)
        return jsonify({"id": cursor.lastrowid, "message": "Đã thêm danh mục."}), 201

    @app.patch("/api/categories/<int:item_id>")
    def category_update(item_id):
        payload = json_body()
        ensure_exists(get_db(), "categories", item_id, "Danh mục")
        name = required_text(payload, "name", 80)
        code = required_text(payload, "code", 20).upper()
        try:
            get_db().execute(
                "UPDATE categories SET code=?, name=?, description=? WHERE id=?",
                (code, name, clean_text(payload.get("description"), 255), item_id),
            )
            get_db().commit()
        except sqlite3.IntegrityError:
            raise ApiError("Mã hoặc tên danh mục đã tồn tại.", 409)
        return {"message": "Đã cập nhật danh mục."}

    @app.delete("/api/categories/<int:item_id>")
    def category_delete(item_id):
        ensure_exists(get_db(), "categories", item_id, "Danh mục")
        used = get_db().execute(
            "SELECT COUNT(*) FROM products WHERE category_id=?", (item_id,)
        ).fetchone()[0]
        if used:
            raise ApiError("Không thể xóa danh mục đang có hàng hóa.", 409)
        get_db().execute("DELETE FROM categories WHERE id=?", (item_id,))
        get_db().commit()
        return {"message": "Đã xóa danh mục."}

    @app.get("/api/products")
    def products_list():
        db = get_db()
        query = clean_text(request.args.get("q"), 100)
        category_id = request.args.get("category_id", type=int)
        status = request.args.get("status", "")
        page = max(request.args.get("page", 1, type=int), 1)
        per_page = min(max(request.args.get("per_page", 8, type=int), 1), 50)
        where, params = ["1=1"], []
        if query:
            where.append("(p.sku LIKE ? OR p.barcode LIKE ? OR p.name LIKE ?)")
            term = f"%{query}%"
            params.extend([term, term, term])
        if category_id:
            where.append("p.category_id=?")
            params.append(category_id)
        if status == "in_stock":
            where.append("p.quantity > p.min_stock")
        elif status == "low_stock":
            where.append("p.quantity > 0 AND p.quantity <= p.min_stock")
        elif status == "out_of_stock":
            where.append("p.quantity = 0")
        predicate = " AND ".join(where)
        total = db.execute(
            f"SELECT COUNT(*) FROM products p WHERE {predicate}", params
        ).fetchone()[0]
        rows = db.execute(
            f"""SELECT p.*, c.name AS category_name, c.code AS category_code
                FROM products p JOIN categories c ON c.id=p.category_id
                WHERE {predicate} ORDER BY p.updated_at DESC, p.id DESC
                LIMIT ? OFFSET ?""",
            [*params, per_page, (page - 1) * per_page],
        ).fetchall()
        return {
            "items": [product_json(row) for row in rows],
            "pagination": pagination_json(page, per_page, total),
        }

    @app.get("/api/products/stats")
    def products_stats():
        row = get_db().execute(
            """SELECT COUNT(*) AS total,
               SUM(CASE WHEN quantity > min_stock THEN 1 ELSE 0 END) AS in_stock,
               SUM(CASE WHEN quantity > 0 AND quantity <= min_stock THEN 1 ELSE 0 END) AS low_stock,
               SUM(CASE WHEN quantity = 0 THEN 1 ELSE 0 END) AS out_of_stock,
               COALESCE(SUM(quantity * unit_price), 0) AS inventory_value
               FROM products"""
        ).fetchone()
        result = dict(row)
        result["category_count"] = get_db().execute(
            "SELECT COUNT(*) FROM categories"
        ).fetchone()[0]
        return result

    @app.get("/api/products/<int:item_id>")
    def product_get(item_id):
        row = product_row(get_db(), item_id)
        result = product_json(row)
        result["outbound_count"] = get_db().execute(
            "SELECT COUNT(DISTINCT order_id) FROM outbound_items WHERE product_id=?",
            (item_id,),
        ).fetchone()[0]
        return result

    @app.post("/api/products")
    def product_create():
        payload = json_body()
        values = validate_product(get_db(), payload)
        timestamp = now_iso()
        try:
            cursor = get_db().execute(
                """INSERT INTO products
                   (sku, barcode, name, description, category_id, unit, location,
                    quantity, min_stock, max_stock, unit_price, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (*values, timestamp, timestamp),
            )
            get_db().commit()
        except sqlite3.IntegrityError:
            raise ApiError("SKU hoặc barcode đã tồn tại.", 409)
        return jsonify({"id": cursor.lastrowid, "message": "Đã thêm hàng hóa."}), 201

    @app.put("/api/products/<int:item_id>")
    def product_update(item_id):
        ensure_exists(get_db(), "products", item_id, "Hàng hóa")
        values = validate_product(get_db(), json_body())
        try:
            get_db().execute(
                """UPDATE products SET sku=?, barcode=?, name=?, description=?,
                   category_id=?, unit=?, location=?, quantity=?, min_stock=?,
                   max_stock=?, unit_price=?, updated_at=? WHERE id=?""",
                (*values, now_iso(), item_id),
            )
            get_db().commit()
        except sqlite3.IntegrityError:
            raise ApiError("SKU hoặc barcode đã tồn tại.", 409)
        return {"message": "Đã cập nhật hàng hóa."}

    @app.delete("/api/products/<int:item_id>")
    def product_delete(item_id):
        ensure_exists(get_db(), "products", item_id, "Hàng hóa")
        used = get_db().execute(
            "SELECT COUNT(*) FROM outbound_items WHERE product_id=?", (item_id,)
        ).fetchone()[0]
        if used:
            raise ApiError("Không thể xóa hàng hóa đã phát sinh phiếu xuất.", 409)
        get_db().execute("DELETE FROM products WHERE id=?", (item_id,))
        get_db().commit()
        return {"message": "Đã xóa hàng hóa."}

    @app.get("/api/outbound-orders")
    def orders_list():
        db = get_db()
        query = clean_text(request.args.get("q"), 100)
        status = request.args.get("status", "")
        page = max(request.args.get("page", 1, type=int), 1)
        per_page = min(max(request.args.get("per_page", 8, type=int), 1), 50)
        where, params = ["1=1"], []
        if query:
            where.append("(o.code LIKE ? OR o.customer_name LIKE ? OR o.vehicle_no LIKE ?)")
            term = f"%{query}%"
            params.extend([term, term, term])
        if status in ORDER_STATUSES:
            where.append("o.status=?")
            params.append(status)
        predicate = " AND ".join(where)
        total = db.execute(
            f"SELECT COUNT(*) FROM outbound_orders o WHERE {predicate}", params
        ).fetchone()[0]
        rows = db.execute(
            f"""SELECT o.*, COUNT(i.id) AS line_count,
                COALESCE(SUM(i.quantity),0) AS total_quantity,
                COALESCE(SUM(i.quantity*i.unit_price),0) AS total_value
                FROM outbound_orders o LEFT JOIN outbound_items i ON i.order_id=o.id
                WHERE {predicate} GROUP BY o.id
                ORDER BY o.outbound_date DESC, o.id DESC LIMIT ? OFFSET ?""",
            [*params, per_page, (page - 1) * per_page],
        ).fetchall()
        return {
            "items": [order_json(row) for row in rows],
            "pagination": pagination_json(page, per_page, total),
        }

    @app.get("/api/outbound-orders/stats")
    def orders_stats():
        row = get_db().execute(
            """SELECT COUNT(*) total,
               SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) pending,
               SUM(CASE WHEN status='processing' THEN 1 ELSE 0 END) processing,
               SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) completed,
               SUM(CASE WHEN status='cancelled' THEN 1 ELSE 0 END) cancelled
               FROM outbound_orders"""
        ).fetchone()
        return {key: (value or 0) for key, value in dict(row).items()}

    @app.get("/api/outbound-orders/<int:item_id>")
    def order_get(item_id):
        return order_detail(get_db(), item_id)

    @app.post("/api/outbound-orders")
    def order_create():
        db = get_db()
        payload = json_body()
        header, items = validate_order(db, payload)
        timestamp = now_iso()
        try:
            db.execute("BEGIN IMMEDIATE")
            code = next_order_code(db)
            cursor = db.execute(
                """INSERT INTO outbound_orders
                   (code, outbound_date, customer_name, tax_code, phone, address,
                    container_no, seal_no, vehicle_no, c_number, note, status,
                    created_by, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (code, *header, "pending", "Lê Thảo", timestamp, timestamp),
            )
            insert_order_items(db, cursor.lastrowid, items)
            add_history(db, cursor.lastrowid, None, "pending", "Tạo phiếu xuất")
            db.commit()
        except Exception:
            db.rollback()
            raise
        return jsonify({"id": cursor.lastrowid, "code": code, "message": "Đã tạo phiếu xuất."}), 201

    @app.put("/api/outbound-orders/<int:item_id>")
    def order_update(item_id):
        db = get_db()
        existing = order_row(db, item_id)
        if existing["status"] not in EDITABLE_STATUSES:
            raise ApiError("Chỉ có thể sửa phiếu đang chờ duyệt hoặc đang xử lý.", 409)
        header, items = validate_order(db, json_body())
        try:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """UPDATE outbound_orders SET outbound_date=?, customer_name=?,
                   tax_code=?, phone=?, address=?, container_no=?, seal_no=?,
                   vehicle_no=?, c_number=?, note=?, updated_at=? WHERE id=?""",
                (*header, now_iso(), item_id),
            )
            db.execute("DELETE FROM outbound_items WHERE order_id=?", (item_id,))
            insert_order_items(db, item_id, items)
            add_history(db, item_id, existing["status"], existing["status"], "Cập nhật thông tin phiếu")
            db.commit()
        except Exception:
            db.rollback()
            raise
        return {"message": "Đã cập nhật phiếu xuất."}

    @app.delete("/api/outbound-orders/<int:item_id>")
    def order_delete(item_id):
        db = get_db()
        existing = order_row(db, item_id)
        if existing["status"] not in EDITABLE_STATUSES:
            raise ApiError("Không thể xóa phiếu đã hoàn thành hoặc đã hủy.", 409)
        db.execute("DELETE FROM outbound_orders WHERE id=?", (item_id,))
        db.commit()
        return {"message": "Đã xóa phiếu xuất."}

    @app.post("/api/outbound-orders/<int:item_id>/validate-stock")
    def order_validate_stock(item_id):
        db = get_db()
        order_row(db, item_id)
        shortages = stock_shortages(db, item_id)
        inspections = db.execute(
            """SELECT oi.*, p.sku, p.name FROM outbound_inspections oi
               JOIN products p ON p.id=oi.product_id WHERE oi.order_id=?""",
            (item_id,),
        ).fetchall()
        return {
            "valid": not shortages,
            "shortages": shortages,
            "inspection_complete": inspection_passed(db, item_id),
            "inspections": [dict(row) for row in inspections],
        }

    @app.put("/api/outbound-orders/<int:item_id>/inspection")
    def order_save_inspection(item_id):
        db = get_db()
        order = order_row(db, item_id)
        if order["status"] != "processing":
            raise ApiError("Chỉ kiểm tra phiếu đang xử lý.", 409)
        payload = json_body()
        raw_items = payload.get("items")
        expected = db.execute(
            """SELECT i.product_id, i.quantity, p.quantity AS stock
               FROM outbound_items i JOIN products p ON p.id=i.product_id
               WHERE i.order_id=? ORDER BY i.id""",
            (item_id,),
        ).fetchall()
        if not isinstance(raw_items, list) or len(raw_items) != len(expected):
            raise ApiError("Biên bản phải kiểm tra đủ tất cả mặt hàng.")
        submitted = {int(item.get("product_id", 0)): item for item in raw_items if isinstance(item, dict)}
        if set(submitted) != {row["product_id"] for row in expected}:
            raise ApiError("Danh sách hàng kiểm tra không khớp phiếu xuất.")
        timestamp = now_iso()
        records = []
        for row in expected:
            raw = submitted[row["product_id"]]
            actual = positive_int(raw.get("actual_quantity"), "Số lượng kiểm đếm")
            condition_ok = 1 if raw.get("condition_ok") is True else 0
            passed = int(actual == row["quantity"] and condition_ok and row["stock"] >= row["quantity"])
            records.append((
                item_id, row["product_id"], row["quantity"], actual, condition_ok,
                clean_text(raw.get("note"), 255), passed, "Lê Thảo", timestamp,
            ))
        try:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM outbound_inspections WHERE order_id=?", (item_id,))
            db.executemany(
                """INSERT INTO outbound_inspections
                   (order_id, product_id, expected_quantity, actual_quantity,
                    condition_ok, note, passed, inspector, inspected_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                records,
            )
            add_history(
                db, item_id, order["status"], order["status"],
                "Lưu biên bản kiểm tra: " + ("đạt" if all(r[6] for r in records) else "chưa đạt"),
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        passed = all(record[6] for record in records)
        return {"message": "Đã lưu biên bản kiểm tra.", "passed": passed}

    @app.post("/api/outbound-orders/<int:item_id>/status")
    def order_change_status(item_id):
        db = get_db()
        payload = json_body()
        new_status = payload.get("status")
        note = clean_text(payload.get("note"), 255)
        if new_status not in ORDER_STATUSES:
            raise ApiError("Trạng thái không hợp lệ.")
        existing = order_row(db, item_id)
        old_status = existing["status"]
        allowed = {
            "pending": {"processing", "cancelled"},
            "processing": {"pending", "completed", "cancelled"},
            "completed": set(),
            "cancelled": set(),
        }
        if new_status not in allowed[old_status]:
            raise ApiError("Chuyển trạng thái không hợp lệ.", 409)
        try:
            db.execute("BEGIN IMMEDIATE")
            if new_status == "completed":
                if not inspection_passed(db, item_id):
                    raise ApiError("Phiếu phải có biên bản kiểm tra đạt trước khi hoàn thành.", 409)
                shortages = stock_shortages(db, item_id)
                if shortages:
                    raise ApiError("Tồn kho không đủ để hoàn thành phiếu.", 409, shortages)
                lines = db.execute(
                    """SELECT i.product_id, i.quantity, p.quantity AS stock
                       FROM outbound_items i JOIN products p ON p.id=i.product_id
                       WHERE i.order_id=?""", (item_id,),
                ).fetchall()
                timestamp = now_iso()
                for line in lines:
                    after = line["stock"] - line["quantity"]
                    db.execute(
                        "UPDATE products SET quantity=?, updated_at=? WHERE id=?",
                        (after, timestamp, line["product_id"]),
                    )
                    db.execute(
                        """INSERT INTO stock_movements
                           (product_id, order_id, movement_type, quantity_change,
                            quantity_before, quantity_after, actor, created_at)
                           VALUES (?, ?, 'OUTBOUND', ?, ?, ?, ?, ?)""",
                        (
                            line["product_id"], item_id, -line["quantity"],
                            line["stock"], after, "Lê Thảo", timestamp,
                        ),
                    )
            db.execute(
                "UPDATE outbound_orders SET status=?, updated_at=? WHERE id=?",
                (new_status, now_iso(), item_id),
            )
            add_history(db, item_id, old_status, new_status, note or "Cập nhật trạng thái")
            db.commit()
        except Exception:
            db.rollback()
            raise
        return {"message": "Đã cập nhật trạng thái.", "status": new_status}

    @app.get("/api/outbound-history")
    def outbound_history():
        rows = get_db().execute(
            """SELECT h.*, o.code FROM order_history h
               JOIN outbound_orders o ON o.id=h.order_id
               ORDER BY h.created_at DESC, h.id DESC LIMIT 200"""
        ).fetchall()
        return jsonify([dict(row) for row in rows])


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400, details=None):
        self.message = message
        self.status = status
        self.details = details
        super().__init__(message)


def json_body() -> dict:
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ApiError("Dữ liệu gửi lên phải là JSON hợp lệ.")
    return payload


def clean_text(value, limit: int) -> str:
    return str(value or "").strip()[:limit]


def required_text(payload: dict, field: str, limit: int) -> str:
    value = clean_text(payload.get(field), limit)
    if not value:
        raise ApiError(f"Trường '{field}' không được để trống.")
    return value


def positive_int(value, field: str, allow_zero: bool = True) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise ApiError(f"Trường '{field}' phải là số nguyên.")
    minimum = 0 if allow_zero else 1
    if result < minimum:
        raise ApiError(f"Trường '{field}' phải lớn hơn hoặc bằng {minimum}.")
    return result


def nonnegative_float(value, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ApiError(f"Trường '{field}' phải là số.")
    if result < 0:
        raise ApiError(f"Trường '{field}' không được âm.")
    return round(result, 2)


def ensure_exists(db, table: str, item_id: int, label: str):
    row = db.execute(f"SELECT id FROM {table} WHERE id=?", (item_id,)).fetchone()
    if row is None:
        raise ApiError(f"{label} không tồn tại.", 404)
    return row


def validate_product(db, payload: dict) -> tuple:
    sku = required_text(payload, "sku", 30).upper()
    barcode = required_text(payload, "barcode", 30)
    name = required_text(payload, "name", 150)
    description = clean_text(payload.get("description"), 500)
    category_id = positive_int(payload.get("category_id"), "category_id", False)
    ensure_exists(db, "categories", category_id, "Danh mục")
    unit = required_text(payload, "unit", 30)
    location = required_text(payload, "location", 30).upper()
    quantity = positive_int(payload.get("quantity"), "quantity")
    min_stock = positive_int(payload.get("min_stock"), "min_stock")
    max_stock = positive_int(payload.get("max_stock"), "max_stock", False)
    if max_stock < min_stock or quantity > max_stock:
        raise ApiError("Tồn tối đa phải ≥ tồn tối thiểu và ≥ số lượng hiện tại.")
    unit_price = nonnegative_float(payload.get("unit_price"), "unit_price")
    return (
        sku, barcode, name, description, category_id, unit, location,
        quantity, min_stock, max_stock, unit_price,
    )


def validate_order(db, payload: dict) -> tuple[tuple, list[tuple]]:
    outbound_date = required_text(payload, "outbound_date", 10)
    try:
        date.fromisoformat(outbound_date)
    except ValueError:
        raise ApiError("Ngày xuất kho không hợp lệ.")
    customer_name = required_text(payload, "customer_name", 150)
    phone = clean_text(payload.get("phone"), 20)
    if phone and (not phone.replace("+", "").isdigit() or len(phone) < 9):
        raise ApiError("Số điện thoại không hợp lệ.")
    header = (
        outbound_date,
        customer_name,
        clean_text(payload.get("tax_code"), 30),
        phone,
        clean_text(payload.get("address"), 255),
        clean_text(payload.get("container_no"), 50),
        clean_text(payload.get("seal_no"), 50),
        clean_text(payload.get("vehicle_no"), 30).upper(),
        clean_text(payload.get("c_number"), 50),
        clean_text(payload.get("note"), 500),
    )
    raw_items = payload.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise ApiError("Phiếu xuất phải có ít nhất một mặt hàng.")
    items, seen = [], set()
    for index, raw in enumerate(raw_items, start=1):
        if not isinstance(raw, dict):
            raise ApiError(f"Dòng hàng {index} không hợp lệ.")
        product_id = positive_int(raw.get("product_id"), f"product_id dòng {index}", False)
        if product_id in seen:
            raise ApiError("Một hàng hóa không được xuất hiện nhiều lần trong phiếu.")
        seen.add(product_id)
        product = product_row(db, product_id)
        quantity = positive_int(raw.get("quantity"), f"quantity dòng {index}", False)
        items.append((product_id, quantity, float(product["unit_price"])))
    return header, items


def product_row(db, item_id: int):
    row = db.execute(
        """SELECT p.*, c.name AS category_name, c.code AS category_code
           FROM products p JOIN categories c ON c.id=p.category_id WHERE p.id=?""",
        (item_id,),
    ).fetchone()
    if row is None:
        raise ApiError("Hàng hóa không tồn tại.", 404)
    return row


def product_json(row) -> dict:
    result = dict(row)
    quantity, minimum = result["quantity"], result["min_stock"]
    result["status"] = (
        "out_of_stock" if quantity == 0 else "low_stock" if quantity <= minimum else "in_stock"
    )
    result["inventory_value"] = round(quantity * result["unit_price"], 2)
    return result


def order_row(db, item_id: int):
    row = db.execute("SELECT * FROM outbound_orders WHERE id=?", (item_id,)).fetchone()
    if row is None:
        raise ApiError("Phiếu xuất không tồn tại.", 404)
    return row


def order_json(row) -> dict:
    result = dict(row)
    for key in ("line_count", "total_quantity", "total_value"):
        if key not in result:
            result[key] = 0
    return result


def order_detail(db, item_id: int) -> dict:
    order = order_json(order_row(db, item_id))
    items = db.execute(
        """SELECT i.*, p.sku, p.name, p.unit, p.quantity AS stock,
           (i.quantity*i.unit_price) AS line_total
           FROM outbound_items i JOIN products p ON p.id=i.product_id
           WHERE i.order_id=? ORDER BY i.id""",
        (item_id,),
    ).fetchall()
    order["items"] = [dict(row) for row in items]
    order["line_count"] = len(items)
    order["total_quantity"] = sum(row["quantity"] for row in items)
    order["total_value"] = sum(row["line_total"] for row in items)
    order["history"] = [
        dict(row) for row in db.execute(
            "SELECT * FROM order_history WHERE order_id=? ORDER BY created_at DESC, id DESC",
            (item_id,),
        ).fetchall()
    ]
    order["inspections"] = [
        dict(row) for row in db.execute(
            "SELECT * FROM outbound_inspections WHERE order_id=? ORDER BY id", (item_id,)
        ).fetchall()
    ]
    order["stock_movements"] = [
        dict(row) for row in db.execute(
            "SELECT * FROM stock_movements WHERE order_id=? ORDER BY id", (item_id,)
        ).fetchall()
    ]
    return order


def insert_order_items(db, order_id: int, items: list[tuple]) -> None:
    db.executemany(
        "INSERT INTO outbound_items(order_id, product_id, quantity, unit_price) VALUES (?, ?, ?, ?)",
        [(order_id, *item) for item in items],
    )


def add_history(db, order_id: int, old_status, new_status: str, note: str) -> None:
    db.execute(
        """INSERT INTO order_history(order_id, old_status, new_status, note, actor, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (order_id, old_status, new_status, note, "Lê Thảo", now_iso()),
    )


def stock_shortages(db, order_id: int) -> list[dict]:
    rows = db.execute(
        """SELECT p.id AS product_id, p.sku, p.name, p.quantity AS available,
           i.quantity AS requested
           FROM outbound_items i JOIN products p ON p.id=i.product_id
           WHERE i.order_id=? AND i.quantity > p.quantity""",
        (order_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def inspection_passed(db, order_id: int) -> bool:
    row = db.execute(
        """SELECT COUNT(*) AS inspected, COALESCE(SUM(passed),0) AS passed
           FROM outbound_inspections WHERE order_id=?""", (order_id,)
    ).fetchone()
    expected = db.execute(
        "SELECT COUNT(*) FROM outbound_items WHERE order_id=?", (order_id,)
    ).fetchone()[0]
    return expected > 0 and row["inspected"] == expected and row["passed"] == expected


def next_order_code(db) -> str:
    prefix = f"PX-{date.today():%y%m%d}-"
    rows = db.execute(
        "SELECT code FROM outbound_orders WHERE code LIKE ?", (prefix + "%",)
    ).fetchall()
    suffixes = []
    for row in rows:
        try:
            suffixes.append(int(row["code"].removeprefix(prefix)))
        except ValueError:
            continue
    return f"{prefix}{max(suffixes, default=0) + 1:03d}"


def pagination_json(page: int, per_page: int, total: int) -> dict:
    pages = max((total + per_page - 1) // per_page, 1)
    return {"page": page, "per_page": per_page, "total": total, "pages": pages}


SCHEMA = """
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku TEXT NOT NULL UNIQUE,
    barcode TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE RESTRICT,
    unit TEXT NOT NULL,
    location TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK(quantity >= 0),
    min_stock INTEGER NOT NULL CHECK(min_stock >= 0),
    max_stock INTEGER NOT NULL CHECK(max_stock > 0),
    unit_price REAL NOT NULL CHECK(unit_price >= 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK(max_stock >= min_stock),
    CHECK(max_stock >= quantity)
);
CREATE TABLE IF NOT EXISTS outbound_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    outbound_date TEXT NOT NULL,
    customer_name TEXT NOT NULL,
    tax_code TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    address TEXT NOT NULL DEFAULT '',
    container_no TEXT NOT NULL DEFAULT '',
    seal_no TEXT NOT NULL DEFAULT '',
    vehicle_no TEXT NOT NULL DEFAULT '',
    c_number TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL CHECK(status IN ('pending','processing','completed','cancelled')),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS outbound_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES outbound_orders(id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
    quantity INTEGER NOT NULL CHECK(quantity > 0),
    unit_price REAL NOT NULL CHECK(unit_price >= 0),
    UNIQUE(order_id, product_id)
);
CREATE TABLE IF NOT EXISTS order_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES outbound_orders(id) ON DELETE CASCADE,
    old_status TEXT,
    new_status TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS outbound_inspections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES outbound_orders(id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
    expected_quantity INTEGER NOT NULL CHECK(expected_quantity > 0),
    actual_quantity INTEGER NOT NULL CHECK(actual_quantity >= 0),
    condition_ok INTEGER NOT NULL CHECK(condition_ok IN (0,1)),
    note TEXT NOT NULL DEFAULT '',
    passed INTEGER NOT NULL CHECK(passed IN (0,1)),
    inspector TEXT NOT NULL,
    inspected_at TEXT NOT NULL,
    UNIQUE(order_id, product_id)
);
CREATE TABLE IF NOT EXISTS stock_movements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
    order_id INTEGER NOT NULL REFERENCES outbound_orders(id) ON DELETE RESTRICT,
    movement_type TEXT NOT NULL CHECK(movement_type IN ('OUTBOUND')),
    quantity_change INTEGER NOT NULL CHECK(quantity_change < 0),
    quantity_before INTEGER NOT NULL CHECK(quantity_before >= 0),
    quantity_after INTEGER NOT NULL CHECK(quantity_after >= 0),
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(order_id, product_id, movement_type)
);
CREATE INDEX IF NOT EXISTS idx_products_name ON products(name);
CREATE INDEX IF NOT EXISTS idx_orders_status_date ON outbound_orders(status, outbound_date);
"""


def seed_db(db) -> None:
    categories = [
        ("ELC", "Điện tử", "Thiết bị điện tử và công nghệ"),
        ("HME", "Gia dụng", "Thiết bị gia đình"),
        ("FUR", "Nội thất", "Sản phẩm nội thất"),
        ("SPT", "Thể thao", "Dụng cụ và thiết bị thể thao"),
        ("CLT", "Thời trang", "Quần áo và phụ kiện"),
    ]
    db.executemany("INSERT INTO categories(code,name,description) VALUES (?,?,?)", categories)
    cat = {r["code"]: r["id"] for r in db.execute("SELECT id,code FROM categories")}
    timestamp = now_iso()
    products = [
        ("ELC-2026-X1","8938505970011","iPhone 15 Pro Max 256GB","Titanium tự nhiên",cat["ELC"],"Cái","A-12-04",145,50,500,30000000),
        ("HME-WM-800","8938505970028","Máy giặt LG Inverter 9kg","Màu trắng",cat["HME"],"Bộ","B-05-11",12,15,30,9000000),
        ("FUR-DT-240","8938505970035","Bàn ăn gỗ sồi Scandia","Kích thước 1,6 m",cat["FUR"],"Kiện","C-02-01",0,10,50,8500000),
        ("SPT-YG-101","8938505970042","Xe đạp thể thao Trek FX3","Khung nhôm nhẹ",cat["SPT"],"Cái","D-03-02",23,5,60,8000000),
        ("CLT-JK-550","8938505970059","Áo khoác da nam cao cấp","Size M/L/XL",cat["CLT"],"Cái","E-01-03",67,20,200,500000),
        ("ELC-LP-202","8938505970066","Laptop Dell Latitude 5440","Intel Core i7, RAM 16GB",cat["ELC"],"Cái","A-10-02",32,10,100,24500000),
        ("HME-AC-150","8938505970073","Máy lạnh Daikin Inverter 1.5HP","Tiết kiệm điện",cat["HME"],"Bộ","B-08-03",8,10,40,13200000),
        ("SPT-RN-220","8938505970080","Giày chạy bộ Nike Pegasus","Màu đen",cat["SPT"],"Đôi","D-04-06",84,20,250,3200000),
        ("FUR-SF-330","8938505970097","Sofa phòng khách Nordic","Vải chống bám bụi",cat["FUR"],"Bộ","C-01-05",11,4,25,18700000),
        ("CLT-BG-710","8938505970103","Balo công sở chống nước","Ngăn laptop 15 inch",cat["CLT"],"Cái","E-02-08",120,30,300,750000),
    ]
    db.executemany(
        """INSERT INTO products(sku,barcode,name,description,category_id,unit,location,
           quantity,min_stock,max_stock,unit_price,created_at,updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [(*p, timestamp, timestamp) for p in products],
    )
    order_data = [
        ("PX-260725-001","2026-07-25","Công ty TNHH Minh Phát","0402156789","0905123456","Hải Châu, Đà Nẵng","DNP-260725","SL-8012","43C-123.45","CN-001","Giao giờ hành chính","completed"),
        ("PX-260727-001","2026-07-27","Cửa hàng Công nghệ An Tâm","","0911222333","Thanh Khê, Đà Nẵng","","","43C-678.90","","Ưu tiên kiểm đếm kỹ","processing"),
        ("PX-260728-001","2026-07-28","Công ty Nội thất Hòa Bình","0401987654","0935666777","Liên Chiểu, Đà Nẵng","","","","","Khách tự vận chuyển","pending"),
    ]
    for idx, order in enumerate(order_data):
        cursor = db.execute(
            """INSERT INTO outbound_orders(code,outbound_date,customer_name,tax_code,phone,
               address,container_no,seal_no,vehicle_no,c_number,note,status,created_by,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (*order, "Lê Thảo", timestamp, timestamp),
        )
        item_sets = [
            [(1, 2, 30000000), (4, 1, 8000000)],
            [(6, 3, 24500000), (10, 10, 750000)],
            [(9, 2, 18700000)],
        ]
        insert_order_items(db, cursor.lastrowid, item_sets[idx])
        order_id = cursor.lastrowid
        add_history(db, order_id, None, "pending", "Tạo phiếu xuất minh họa")
        if order[-1] in {"processing", "completed"}:
            add_history(db, order_id, "pending", "processing", "Bắt đầu xử lý phiếu")
        if order[-1] == "completed":
            for product_id, quantity, _price in item_sets[idx]:
                current_stock = db.execute(
                    "SELECT quantity FROM products WHERE id=?", (product_id,)
                ).fetchone()[0]
                db.execute(
                    """INSERT INTO outbound_inspections
                       (order_id,product_id,expected_quantity,actual_quantity,condition_ok,
                        note,passed,inspector,inspected_at)
                       VALUES (?,?,?,?,1,'Đủ số lượng và chất lượng',1,'Lê Thảo',?)""",
                    (order_id, product_id, quantity, quantity, timestamp),
                )
                db.execute(
                    """INSERT INTO stock_movements
                       (product_id,order_id,movement_type,quantity_change,
                        quantity_before,quantity_after,actor,created_at)
                       VALUES (?,?,'OUTBOUND',?,?,?,'Lê Thảo',?)""",
                    (product_id, order_id, -quantity, current_stock + quantity, current_stock, timestamp),
                )
            add_history(db, order_id, "processing", "processing", "Biên bản kiểm tra đạt")
            add_history(db, order_id, "processing", "completed", "Hoàn thành và trừ tồn kho")


app = create_app()


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")
