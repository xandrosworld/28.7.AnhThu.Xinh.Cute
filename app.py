import csv
import io
import json
import math
import os
import secrets
import sqlite3
from datetime import date, datetime
from functools import wraps

import click
from flask import (
    Flask, Response, g, jsonify, redirect, render_template, request, session, url_for
)
from werkzeug.security import check_password_hash


VALID_STATUSES = {"draft", "pending", "inspecting", "completed", "rejected"}
CHECKLIST_KEYS = (
    "container",
    "seal",
    "goods",
    "packaging",
    "barcode",
    "quantity",
    "condition",
)


class AutoClosingConnection(sqlite3.Connection):
    """Commit/rollback như context manager chuẩn và luôn giải phóng file SQLite."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.from_mapping(
        DATABASE=os.path.join(app.instance_path, "wms.sqlite3"),
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-only-change-this-key"),
        JSON_AS_ASCII=False,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )
    if test_config:
        app.config.update(test_config)
    os.makedirs(app.instance_path, exist_ok=True)

    def get_db():
        db = sqlite3.connect(app.config["DATABASE"], factory=AutoClosingConnection)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        return db

    app.get_db = get_db

    def json_error(message, status):
        return jsonify(error=message), status

    @app.before_request
    def load_user_and_protect_csrf():
        user_id = session.get("user_id")
        with get_db() as db:
            g.user = db.execute(
                "SELECT id,username,full_name,role,status FROM users WHERE id=?", (user_id,)
            ).fetchone() if user_id else None
        if g.user and g.user["status"] != "active":
            session.clear()
            g.user = None
        if (
            request.path.startswith("/api/")
            and request.method in {"POST", "PUT", "PATCH", "DELETE"}
            and request.endpoint != "api_login"
        ):
            if g.user is None:
                return json_error("Vui lòng đăng nhập.", 401)
            supplied = request.headers.get("X-CSRF-Token", "")
            expected = session.get("csrf_token", "")
            if not expected or not secrets.compare_digest(supplied, expected):
                return json_error("CSRF token không hợp lệ.", 403)

    def login_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if g.user is None:
                if request.path.startswith("/api/") or request.path.endswith(".csv"):
                    return json_error("Vui lòng đăng nhập.", 401)
                return redirect(url_for("login_page", next=request.full_path))
            return view(*args, **kwargs)
        return wrapped

    def roles_required(*roles):
        def decorator(view):
            @wraps(view)
            @login_required
            def wrapped(*args, **kwargs):
                if g.user["role"] not in roles:
                    if request.path.startswith("/api/"):
                        return json_error("Bạn không có quyền thực hiện thao tác này.", 403)
                    return render_template(
                        "error.html", title="Không có quyền",
                        message="Tài khoản không có quyền truy cập trang này."
                    ), 403
                return view(*args, **kwargs)
            return wrapped
        return decorator

    @app.get("/login")
    def login_page():
        if g.user:
            return redirect(url_for("dashboard"))
        return render_template("login.html", page="login")

    @app.post("/api/auth/login")
    def api_login():
        data = request.get_json(silent=True) or {}
        username = str(data.get("username", "")).strip()
        password = str(data.get("password", ""))
        if not username or not password:
            return json_error("Tên đăng nhập và mật khẩu là bắt buộc.", 422)
        with get_db() as db:
            user = db.execute("SELECT * FROM users WHERE username=? COLLATE NOCASE", (username,)).fetchone()
        if not user or not check_password_hash(user["password_hash"], password):
            return json_error("Tên đăng nhập hoặc mật khẩu không đúng.", 401)
        if user["status"] != "active":
            return json_error("Tài khoản đã bị khóa.", 403)
        session.clear()
        session["user_id"] = user["id"]
        session["csrf_token"] = secrets.token_urlsafe(32)
        return jsonify(
            user={key: user[key] for key in ("id", "username", "full_name", "role")},
            csrf_token=session["csrf_token"],
        )

    @app.get("/api/auth/me")
    @login_required
    def api_me():
        return jsonify(
            user=dict(g.user), csrf_token=session.get("csrf_token")
        )

    @app.post("/api/auth/logout")
    @login_required
    def api_logout():
        session.clear()
        return jsonify(message="Đã đăng xuất.")

    @app.template_filter("number")
    def number_filter(value):
        return f"{float(value or 0):,.0f}".replace(",", ".")

    @app.context_processor
    def inject_globals():
        return {"today": date.today().isoformat()}

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; font-src 'self'; frame-ancestors 'none'"
        )
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(404)
    def not_found(_error):
        if request.path.startswith("/api/"):
            return jsonify(error="Không tìm thấy tài nguyên."), 404
        return render_template("error.html", title="Không tìm thấy", message="Trang bạn yêu cầu không tồn tại."), 404

    @app.errorhandler(400)
    def bad_request(error):
        message = getattr(error, "description", "Dữ liệu không hợp lệ.")
        if request.path.startswith("/api/"):
            return jsonify(error=message), 400
        return render_template("error.html", title="Dữ liệu không hợp lệ", message=message), 400

    @app.errorhandler(405)
    def method_not_allowed(_error):
        if request.path.startswith("/api/"):
            return jsonify(error="Phương thức HTTP không được hỗ trợ."), 405
        return render_template(
            "error.html",
            title="Phương thức không được hỗ trợ",
            message="Phương thức HTTP không được hỗ trợ cho tài nguyên này.",
        ), 405

    @app.route("/")
    def index():
        return redirect(url_for("dashboard"))

    @app.route("/dashboard")
    @login_required
    def dashboard():
        return render_template("dashboard.html", active="dashboard", page="dashboard")

    @app.route("/receipts")
    @login_required
    def receipts_page():
        return render_template("receipts.html", active="receipts", page="receipts")

    @app.route("/receipts/new")
    @roles_required("ADMIN", "CS")
    def receipt_new():
        return render_template("receipt_form.html", active="receipts", page="receipt-form", receipt_id=None)

    @app.route("/receipts/<int:receipt_id>")
    @login_required
    def receipt_detail(receipt_id):
        return render_template("receipt_detail.html", active="receipts", page="receipt-detail", receipt_id=receipt_id)

    @app.route("/receipts/<int:receipt_id>/edit")
    @roles_required("ADMIN", "CS")
    def receipt_edit(receipt_id):
        return render_template("receipt_form.html", active="receipts", page="receipt-form", receipt_id=receipt_id)

    @app.route("/receipts/<int:receipt_id>/inspect")
    @roles_required("ADMIN", "WAREHOUSE")
    def receipt_inspect(receipt_id):
        return render_template("inspection.html", active="receipts", page="inspection", receipt_id=receipt_id)

    @app.route("/history")
    @login_required
    def history_page():
        return render_template("history.html", active="history", page="history")

    @app.route("/reports")
    @login_required
    def reports_page():
        return render_template("reports.html", active="reports", page="reports")

    @app.get("/api/products")
    @login_required
    def api_products():
        with get_db() as db:
            rows = db.execute(
                "SELECT id, sku, name, category, unit, barcode, current_stock, min_stock, unit_price "
                "FROM products ORDER BY name"
            ).fetchall()
        return jsonify([dict(row) for row in rows])

    @app.get("/api/master-data")
    @login_required
    def api_master_data():
        with get_db() as db:
            suppliers = db.execute(
                "SELECT id,code,name FROM suppliers WHERE status='active' ORDER BY name"
            ).fetchall()
            warehouses = db.execute(
                "SELECT id,code,name FROM warehouses WHERE status='active' ORDER BY name"
            ).fetchall()
        return jsonify(
            suppliers=[dict(row) for row in suppliers],
            warehouses=[dict(row) for row in warehouses],
        )

    @app.get("/api/inventory")
    @login_required
    def api_inventory():
        q = request.args.get("q", "").strip()
        params = []
        where = ""
        if q:
            where = "WHERE p.sku LIKE ? OR p.name LIKE ? OR l.barcode LIKE ? OR l.pallet_id LIKE ?"
            params = [f"%{q}%"] * 4
        with get_db() as db:
            rows = db.execute(
                f"""SELECT l.id,p.sku,p.name,l.warehouse,l.pallet_id,l.barcode,l.unit,
                           l.quantity,l.received_at,l.expiry_date
                    FROM inventory_lots l JOIN products p ON p.id=l.product_id
                    {where} ORDER BY l.received_at DESC,l.id DESC""",
                params,
            ).fetchall()
        return jsonify([dict(row) for row in rows])

    @app.get("/api/dashboard")
    @login_required
    def api_dashboard():
        with get_db() as db:
            total_products = db.execute("SELECT COUNT(*) FROM products").fetchone()[0]
            stock = db.execute("SELECT COALESCE(SUM(current_stock), 0) FROM products").fetchone()[0]
            low_stock = db.execute("SELECT COUNT(*) FROM products WHERE current_stock <= min_stock").fetchone()[0]
            today_receipts = db.execute(
                "SELECT COUNT(*) FROM receipts WHERE date(received_date)=date('now','localtime')"
            ).fetchone()[0]
            pending = db.execute(
                "SELECT COUNT(*) FROM receipts WHERE status IN ('draft','pending','inspecting')"
            ).fetchone()[0]
            completed = db.execute("SELECT COUNT(*) FROM receipts WHERE status='completed'").fetchone()[0]
            monthly = db.execute(
                """
                SELECT strftime('%Y-%m', m.created_at) month, SUM(m.quantity) quantity
                FROM stock_movements m
                WHERE m.type='IN' AND date(m.created_at) >= date('now','start of month','-5 months')
                GROUP BY month ORDER BY month
                """
            ).fetchall()
            categories = db.execute(
                "SELECT category, SUM(current_stock) quantity FROM products GROUP BY category ORDER BY quantity DESC"
            ).fetchall()
            activity = db.execute(
                "SELECT action, details, created_at FROM audit_logs ORDER BY id DESC LIMIT 6"
            ).fetchall()
            alerts = db.execute(
                "SELECT sku, name, current_stock, min_stock, unit FROM products "
                "WHERE current_stock <= min_stock ORDER BY current_stock ASC LIMIT 6"
            ).fetchall()
        return jsonify(
            kpis={
                "total_products": total_products,
                "stock": stock,
                "low_stock": low_stock,
                "today_receipts": today_receipts,
                "pending": pending,
                "completed": completed,
            },
            monthly=[dict(row) for row in monthly],
            categories=[dict(row) for row in categories],
            activity=[dict(row) for row in activity],
            alerts=[dict(row) for row in alerts],
        )

    @app.get("/api/receipts")
    @login_required
    def api_receipts():
        search = request.args.get("q", "").strip()
        status = request.args.get("status", "").strip()
        start = request.args.get("start", "").strip()
        end = request.args.get("end", "").strip()
        try:
            start_date = date.fromisoformat(start) if start else None
            end_date = date.fromisoformat(end) if end else None
            if start_date and end_date and start_date > end_date:
                raise ValueError
        except ValueError:
            return jsonify(error="Khoảng ngày lọc không hợp lệ."), 400
        clauses, params = [], []
        if search:
            clauses.append("(r.code LIKE ? OR r.supplier LIKE ? OR r.warehouse LIKE ?)")
            term = f"%{search}%"
            params.extend((term, term, term))
        if status:
            if status not in VALID_STATUSES:
                return jsonify(error="Trạng thái lọc không hợp lệ."), 400
            clauses.append("r.status = ?")
            params.append(status)
        if start:
            clauses.append("date(r.received_date) >= date(?)")
            params.append(start)
        if end:
            clauses.append("date(r.received_date) <= date(?)")
            params.append(end)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with get_db() as db:
            rows = db.execute(
                f"""
                SELECT r.*, COUNT(i.id) item_count, COALESCE(SUM(i.planned_qty),0) planned_total,
                       COALESCE(SUM(i.actual_qty),0) actual_total
                FROM receipts r LEFT JOIN receipt_items i ON i.receipt_id=r.id
                {where} GROUP BY r.id ORDER BY r.received_date DESC, r.id DESC
                """,
                params,
            ).fetchall()
        return jsonify([dict(row) for row in rows])

    @app.post("/api/receipts")
    @roles_required("ADMIN", "CS")
    def api_receipt_create():
        data = json_object()
        errors = validate_receipt(data)
        errors.extend(validate_receipt_master(get_db, data))
        if errors:
            return jsonify(error="; ".join(errors)), 400
        now = datetime.now().isoformat(timespec="seconds")
        try:
            with get_db() as db:
                # Serialize code allocation with the insert so two valid concurrent
                # requests cannot observe and attempt to use the same sequence.
                db.execute("BEGIN IMMEDIATE")
                code = make_receipt_code(db)
                cursor = db.execute(
                    """
                    INSERT INTO receipts
                    (code,supplier,warehouse,received_date,status,vehicle_no,container_no,seal_no,
                     note,created_by,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        code,
                        data["supplier"].strip(),
                        data["warehouse"].strip(),
                        data["received_date"],
                        "pending",
                        clean_text(data.get("vehicle_no")),
                        clean_text(data.get("container_no")),
                        clean_text(data.get("seal_no")),
                        clean_text(data.get("note")),
                        g.user["id"],
                        now,
                        now,
                    ),
                )
                receipt_id = cursor.lastrowid
                save_items(db, receipt_id, data["items"])
                add_audit(db, "CREATE", "receipt", receipt_id, f"Tạo phiếu nhập {code}")
            return jsonify(id=receipt_id, code=code, message="Đã tạo phiếu nhập."), 201
        except sqlite3.IntegrityError as exc:
            return jsonify(error=f"Không thể tạo phiếu: {exc}"), 409

    @app.get("/api/receipts/<int:receipt_id>")
    @login_required
    def api_receipt_detail(receipt_id):
        with get_db() as db:
            receipt = db.execute("SELECT * FROM receipts WHERE id=?", (receipt_id,)).fetchone()
            if not receipt:
                return jsonify(error="Không tìm thấy phiếu nhập."), 404
            items = db.execute(
                """
                SELECT i.*, p.sku, p.name, p.current_stock
                FROM receipt_items i JOIN products p ON p.id=i.product_id
                WHERE i.receipt_id=? ORDER BY i.id
                """,
                (receipt_id,),
            ).fetchall()
            inspection = db.execute("SELECT * FROM inspections WHERE receipt_id=?", (receipt_id,)).fetchone()
        result = dict(receipt)
        result["items"] = [dict(row) for row in items]
        result["inspection"] = dict(inspection) if inspection else None
        if result["inspection"]:
            result["inspection"]["checklist"] = json.loads(result["inspection"].pop("checklist_json"))
        return jsonify(result)

    @app.put("/api/receipts/<int:receipt_id>")
    @roles_required("ADMIN", "CS")
    def api_receipt_update(receipt_id):
        data = json_object()
        errors = validate_receipt(data)
        errors.extend(validate_receipt_master(get_db, data))
        if errors:
            return jsonify(error="; ".join(errors)), 400
        now = datetime.now().isoformat(timespec="seconds")
        try:
            with get_db() as db:
                receipt = db.execute("SELECT * FROM receipts WHERE id=?", (receipt_id,)).fetchone()
                if not receipt:
                    return jsonify(error="Không tìm thấy phiếu nhập."), 404
                if receipt["status"] == "completed":
                    return jsonify(error="Phiếu đã hoàn tất nên không thể chỉnh sửa."), 409
                db.execute(
                    """
                    UPDATE receipts SET supplier=?,warehouse=?,received_date=?,vehicle_no=?,
                    container_no=?,seal_no=?,note=?,updated_at=? WHERE id=?
                    """,
                    (
                        data["supplier"].strip(),
                        data["warehouse"].strip(),
                        data["received_date"],
                        clean_text(data.get("vehicle_no")),
                        clean_text(data.get("container_no")),
                        clean_text(data.get("seal_no")),
                        clean_text(data.get("note")),
                        now,
                        receipt_id,
                    ),
                )
                db.execute("DELETE FROM receipt_items WHERE receipt_id=?", (receipt_id,))
                save_items(db, receipt_id, data["items"])
                add_audit(db, "UPDATE", "receipt", receipt_id, f"Cập nhật phiếu nhập {receipt['code']}")
        except sqlite3.IntegrityError:
            return jsonify(error="Mặt hàng không tồn tại hoặc bị trùng trong phiếu."), 409
        return jsonify(message="Đã cập nhật phiếu nhập.")

    @app.delete("/api/receipts/<int:receipt_id>")
    @roles_required("ADMIN", "CS")
    def api_receipt_delete(receipt_id):
        with get_db() as db:
            receipt = db.execute("SELECT * FROM receipts WHERE id=?", (receipt_id,)).fetchone()
            if not receipt:
                return jsonify(error="Không tìm thấy phiếu nhập."), 404
            if receipt["status"] == "completed":
                return jsonify(error="Không thể xóa phiếu đã hoàn tất."), 409
            code = receipt["code"]
            db.execute("DELETE FROM receipts WHERE id=?", (receipt_id,))
            add_audit(db, "DELETE", "receipt", receipt_id, f"Xóa phiếu nhập {code}")
        return jsonify(message="Đã xóa phiếu nhập.")

    @app.post("/api/receipts/<int:receipt_id>/inspection")
    @roles_required("ADMIN", "WAREHOUSE")
    def api_inspection(receipt_id):
        data = json_object()
        checklist = data.get("checklist")
        if not isinstance(checklist, dict):
            checklist = {}
        result = data.get("result")
        missing = [key for key in CHECKLIST_KEYS if checklist.get(key) not in {"pass", "fail"}]
        if missing:
            return jsonify(error="Vui lòng kiểm tra đủ 7 tiêu chí."), 400
        if result not in {"pass", "fail"}:
            return jsonify(error="Kết quả kiểm tra không hợp lệ."), 400
        if result == "pass" and any(value == "fail" for value in checklist.values()):
            return jsonify(error="Không thể chọn Đạt khi checklist còn tiêu chí không đạt."), 400
        mappings = {
            key: data.get(key) or {}
            for key in (
                "actual_quantities",
                "rejected_quantities",
                "rejection_reasons",
                "scanned_barcodes",
            )
        }
        if any(not isinstance(value, dict) for value in mappings.values()):
            return jsonify(error="Dữ liệu kiểm đếm theo mặt hàng không hợp lệ."), 400
        if "note" in data and data["note"] is not None and not isinstance(data["note"], str):
            return jsonify(error="Ghi chú kiểm tra phải là chuỗi."), 400
        actual_quantities = mappings["actual_quantities"]
        rejected_quantities = mappings["rejected_quantities"]
        rejection_reasons = mappings["rejection_reasons"]
        scanned_barcodes = mappings["scanned_barcodes"]
        now = datetime.now().isoformat(timespec="seconds")
        with get_db() as db:
            receipt = db.execute("SELECT * FROM receipts WHERE id=?", (receipt_id,)).fetchone()
            if not receipt:
                return jsonify(error="Không tìm thấy phiếu nhập."), 404
            if receipt["status"] == "completed":
                return jsonify(error="Phiếu đã hoàn tất."), 409
            items = db.execute(
                "SELECT id,planned_qty,barcode FROM receipt_items WHERE receipt_id=?", (receipt_id,)
            ).fetchall()
            for item in items:
                raw = actual_quantities.get(str(item["id"]))
                try:
                    qty = finite_float(raw)
                    rejected_qty = finite_float(
                        rejected_quantities.get(str(item["id"]), 0) or 0
                    )
                except (TypeError, ValueError):
                    return jsonify(error="Số lượng chấp nhận/từ chối phải là số không âm."), 400
                reason = str(rejection_reasons.get(str(item["id"]), "")).strip()
                scanned = str(scanned_barcodes.get(str(item["id"]), "")).strip()
                if qty < 0 or rejected_qty < 0 or qty + rejected_qty > item["planned_qty"]:
                    return jsonify(error="Số lượng chấp nhận/từ chối không hợp lệ so với chứng từ."), 400
                if rejected_qty > 0 and not reason:
                    return jsonify(error="Phải nhập lý do cho số lượng bị từ chối."), 422
                if result == "pass" and qty <= 0:
                    return jsonify(error="Phiếu đạt phải có số lượng chấp nhận lớn hơn 0."), 422
                if result == "pass" and scanned != item["barcode"]:
                    return jsonify(error="Barcode quét không khớp hàng hóa trên chứng từ."), 422
                db.execute(
                    """UPDATE receipt_items
                       SET actual_qty=?,rejected_qty=?,rejection_reason=? WHERE id=?""",
                    (qty, rejected_qty, reason, item["id"]),
                )
            db.execute(
                """
                INSERT INTO inspections
                (receipt_id,checklist_json,result,note,inspected_by,inspected_by_user_id,inspected_at)
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(receipt_id) DO UPDATE SET checklist_json=excluded.checklist_json,
                result=excluded.result,note=excluded.note,inspected_by=excluded.inspected_by,
                inspected_by_user_id=excluded.inspected_by_user_id,
                inspected_at=excluded.inspected_at
                """,
                (
                    receipt_id,
                    json.dumps(checklist, ensure_ascii=False),
                    result,
                    clean_text(data.get("note")),
                    g.user["full_name"],
                    g.user["id"],
                    now,
                ),
            )
            next_status = "inspecting" if result == "pass" else "rejected"
            db.execute("UPDATE receipts SET status=?,updated_at=? WHERE id=?", (next_status, now, receipt_id))
            add_audit(
                db,
                "INSPECT",
                "receipt",
                receipt_id,
                f"Kiểm tra {receipt['code']}: {'Đạt' if result == 'pass' else 'Không đạt'}",
            )
        return jsonify(message="Đã lưu kết quả kiểm tra.", status=next_status)

    @app.post("/api/receipts/<int:receipt_id>/complete")
    @roles_required("ADMIN", "WAREHOUSE")
    def api_complete(receipt_id):
        now = datetime.now().isoformat(timespec="seconds")
        db = get_db()
        try:
            db.execute("BEGIN IMMEDIATE")
            receipt = db.execute("SELECT * FROM receipts WHERE id=?", (receipt_id,)).fetchone()
            if not receipt:
                db.rollback()
                return jsonify(error="Không tìm thấy phiếu nhập."), 404
            if receipt["status"] == "completed":
                db.rollback()
                return jsonify(message="Phiếu đã hoàn tất trước đó.", already_completed=True)
            inspection = db.execute(
                "SELECT * FROM inspections WHERE receipt_id=? AND result='pass'", (receipt_id,)
            ).fetchone()
            if not inspection:
                db.rollback()
                return jsonify(error="Phiếu phải được kiểm tra đạt trước khi hoàn tất."), 409
            items = db.execute(
                "SELECT * FROM receipt_items WHERE receipt_id=? ORDER BY id", (receipt_id,)
            ).fetchall()
            if not items or any((item["actual_qty"] or 0) <= 0 for item in items):
                db.rollback()
                return jsonify(error="Thiếu số lượng thực nhập hợp lệ."), 409
            for item in items:
                product = db.execute(
                    "SELECT current_stock FROM products WHERE id=?", (item["product_id"],)
                ).fetchone()
                balance = product["current_stock"] + item["actual_qty"]
                db.execute(
                    "UPDATE products SET current_stock=? WHERE id=?", (balance, item["product_id"])
                )
                db.execute(
                    """
                    INSERT INTO stock_movements
                    (product_id,receipt_id,type,quantity,balance_after,reference_code,created_at)
                    VALUES(?,?,'IN',?,?,?,?)
                    """,
                    (
                        item["product_id"],
                        receipt_id,
                        item["actual_qty"],
                        balance,
                        receipt["code"],
                        now,
                    ),
                )
                db.execute(
                    """INSERT INTO inventory_lots
                       (product_id,receipt_item_id,warehouse,pallet_id,barcode,unit,
                        quantity,received_at,expiry_date)
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                    (
                        item["product_id"], item["id"], receipt["warehouse"],
                        item["pallet_id"], item["barcode"], item["unit"],
                        item["actual_qty"], now, item["expiry_date"],
                    ),
                )
            db.execute(
                """UPDATE receipts
                   SET status='completed',completed_at=?,completed_by=?,updated_at=?
                   WHERE id=?""",
                (now, g.user["id"], now, receipt_id),
            )
            add_audit(db, "COMPLETE", "receipt", receipt_id, f"Hoàn tất nhập kho {receipt['code']}")
            db.commit()
        except sqlite3.IntegrityError:
            db.rollback()
            receipt = db.execute(
                "SELECT status FROM receipts WHERE id=?", (receipt_id,)
            ).fetchone()
            if receipt and receipt["status"] == "completed":
                return jsonify(message="Phiếu đã được ghi nhận tồn kho.", already_completed=True)
            return jsonify(error="Không thể ghi nhận tồn kho do xung đột dữ liệu."), 409
        finally:
            db.close()
        return jsonify(message="Hoàn tất nhập kho và cập nhật tồn kho.", already_completed=False)

    @app.get("/api/history")
    @login_required
    def api_history():
        action = request.args.get("action", "").strip()
        q = request.args.get("q", "").strip()
        clauses, params = [], []
        if action:
            clauses.append("action=?")
            params.append(action)
        if q:
            clauses.append("details LIKE ?")
            params.append(f"%{q}%")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with get_db() as db:
            rows = db.execute(
                f"SELECT * FROM audit_logs {where} ORDER BY id DESC LIMIT 200", params
            ).fetchall()
        return jsonify([dict(row) for row in rows])

    @app.get("/api/reports")
    @login_required
    def api_reports():
        start = request.args.get("start") or date.today().replace(day=1).isoformat()
        end = request.args.get("end") or date.today().isoformat()
        warehouse = request.args.get("warehouse", "").strip()
        supplier = request.args.get("supplier", "").strip()
        try:
            if date.fromisoformat(start) > date.fromisoformat(end):
                raise ValueError
        except ValueError:
            return jsonify(error="Khoảng ngày báo cáo không hợp lệ."), 400
        extra, filters = "", []
        if warehouse:
            extra += " AND r.warehouse=?"
            filters.append(warehouse)
        if supplier:
            extra += " AND r.supplier LIKE ?"
            filters.append(f"%{supplier}%")
        params = (start, end, *filters)
        with get_db() as db:
            summary = db.execute(
                f"""
                SELECT COUNT(DISTINCT r.id) receipt_count,
                       COALESCE(SUM(i.actual_qty),0) total_quantity,
                       COALESCE(SUM(i.actual_qty*i.unit_price),0) total_value,
                       COUNT(DISTINCT r.supplier) supplier_count
                FROM receipts r LEFT JOIN receipt_items i ON i.receipt_id=r.id
                WHERE r.status='completed' AND date(r.completed_at) BETWEEN date(?) AND date(?)
                {extra}
                """,
                params,
            ).fetchone()
            rows = db.execute(
                f"""
                SELECT r.code,r.supplier,r.warehouse,r.completed_at,
                       SUM(i.actual_qty) quantity,SUM(i.actual_qty*i.unit_price) value
                FROM receipts r JOIN receipt_items i ON i.receipt_id=r.id
                WHERE r.status='completed' AND date(r.completed_at) BETWEEN date(?) AND date(?)
                {extra}
                GROUP BY r.id ORDER BY r.completed_at DESC
                """,
                params,
            ).fetchall()
            top_products = db.execute(
                f"""
                SELECT p.sku,p.name,SUM(i.actual_qty) quantity,p.unit
                FROM receipt_items i JOIN receipts r ON r.id=i.receipt_id
                JOIN products p ON p.id=i.product_id
                WHERE r.status='completed' AND date(r.completed_at) BETWEEN date(?) AND date(?)
                {extra}
                GROUP BY p.id ORDER BY quantity DESC LIMIT 5
                """,
                params,
            ).fetchall()
        return jsonify(
            start=start,
            end=end,
            summary=dict(summary),
            receipts=[dict(row) for row in rows],
            top_products=[dict(row) for row in top_products],
        )

    @app.get("/reports/export.csv")
    @login_required
    def export_report():
        start = request.args.get("start") or "2000-01-01"
        end = request.args.get("end") or date.today().isoformat()
        warehouse = request.args.get("warehouse", "").strip()
        supplier = request.args.get("supplier", "").strip()
        try:
            if date.fromisoformat(start) > date.fromisoformat(end):
                raise ValueError
        except ValueError:
            return jsonify(error="Khoảng ngày báo cáo không hợp lệ."), 400
        extra, filters = "", []
        if warehouse:
            extra += " AND r.warehouse=?"
            filters.append(warehouse)
        if supplier:
            extra += " AND r.supplier LIKE ?"
            filters.append(f"%{supplier}%")
        with get_db() as db:
            rows = db.execute(
                f"""
                SELECT r.code,r.supplier,r.warehouse,r.completed_at,p.sku,p.name,
                       i.pallet_id,i.barcode,i.actual_qty,i.rejected_qty,i.unit,i.unit_price,
                       (i.actual_qty*i.unit_price) value
                FROM receipts r JOIN receipt_items i ON i.receipt_id=r.id
                JOIN products p ON p.id=i.product_id
                WHERE r.status='completed' AND date(r.completed_at) BETWEEN date(?) AND date(?)
                {extra}
                ORDER BY r.completed_at DESC,r.code,p.sku
                """,
                (start, end, *filters),
            ).fetchall()
        output = io.StringIO()
        output.write("\ufeff")
        writer = csv.writer(output)
        writer.writerow(
            ["Mã phiếu", "Nhà cung cấp", "Kho", "Hoàn tất", "SKU", "Hàng hóa",
             "Pallet ID", "Barcode", "Chấp nhận", "Từ chối", "ĐVT", "Đơn giá", "Thành tiền"]
        )
        for row in rows:
            writer.writerow(list(row))
        return Response(
            output.getvalue(),
            content_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename=bao-cao-nhap-kho-{start}-{end}.csv"},
        )

    from database import init_database

    @app.cli.command("init-db")
    def init_db_command():
        """Create missing schema and demonstration data without deleting existing rows."""
        init_database(app.config["DATABASE"])
        click.echo("Đã khởi tạo cơ sở dữ liệu WMS.")

    @app.cli.command("backup-db")
    @click.option("--destination", type=click.Path(dir_okay=False), default=None)
    def backup_db_command(destination):
        """Create a consistent SQLite backup using the online backup API."""
        destination = destination or os.path.join(
            app.instance_path, "backups", f"wms-{datetime.now():%Y%m%d-%H%M%S}.sqlite3"
        )
        destination = os.path.abspath(destination)
        database_path = os.path.abspath(app.config["DATABASE"])
        if os.path.normcase(destination) == os.path.normcase(database_path):
            raise click.ClickException("File sao lưu phải khác cơ sở dữ liệu hiện hành.")
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        source = sqlite3.connect(database_path)
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        click.echo(f"Đã sao lưu: {destination}")

    @app.cli.command("restore-db")
    @click.option("--source", required=True, type=click.Path(exists=True, dir_okay=False))
    def restore_db_command(source):
        """Restore a valid SQLite backup into the configured database."""
        from database import validate_database

        source_path = os.path.abspath(source)
        target_path = os.path.abspath(app.config["DATABASE"])
        if os.path.normcase(source_path) == os.path.normcase(target_path):
            raise click.ClickException("File phục hồi phải khác cơ sở dữ liệu hiện hành.")
        source_db = sqlite3.connect(source_path)
        try:
            validate_database(source_db)
            target_db = sqlite3.connect(target_path)
            try:
                source_db.backup(target_db)
            finally:
                target_db.close()
        except click.ClickException:
            raise
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
        except sqlite3.DatabaseError as exc:
            raise click.ClickException(f"Không thể phục hồi file sao lưu: {exc}") from exc
        finally:
            source_db.close()
        click.echo(f"Đã phục hồi từ: {source}")

    init_database(app.config["DATABASE"])
    return app


def validate_receipt(data):
    if not isinstance(data, dict):
        return ["Dữ liệu phiếu nhập phải là một đối tượng JSON"]
    errors = []
    text_fields = {
        "supplier": "Nhà cung cấp",
        "warehouse": "Kho",
        "received_date": "Ngày nhập",
        "vehicle_no": "Biển số xe",
        "container_no": "Số container",
        "seal_no": "Số seal",
        "note": "Ghi chú",
    }
    for key, label in text_fields.items():
        value = data.get(key)
        if value is not None and not isinstance(value, str):
            errors.append(f"{label} phải là chuỗi")
    for key, label in (
        ("supplier", "Nhà cung cấp"),
        ("warehouse", "Kho"),
        ("received_date", "Ngày nhập"),
    ):
        if not clean_text(data.get(key)):
            errors.append(f"{label} là bắt buộc")
    try:
        datetime.fromisoformat(clean_text(data.get("received_date")))
    except (TypeError, ValueError):
        errors.append("Ngày nhập không đúng định dạng")
    items = data.get("items")
    if not isinstance(items, list) or not items:
        errors.append("Phiếu nhập phải có ít nhất một mặt hàng")
        return errors
    seen = set()
    seen_pallets = set()
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict):
            errors.append(f"Dòng {index}: dữ liệu hàng hóa không hợp lệ")
            continue
        try:
            product_id = positive_int(item.get("product_id"))
            qty = finite_float(item.get("planned_qty"))
            price = finite_float(item.get("unit_price", 0))
        except (TypeError, ValueError):
            errors.append(f"Dòng {index}: dữ liệu hàng hóa không hợp lệ")
            continue
        if product_id in seen:
            errors.append(f"Dòng {index}: mặt hàng bị trùng")
        seen.add(product_id)
        for key, label in (
            ("pallet_id", "pallet ID"),
            ("barcode", "barcode"),
            ("expiry_date", "hạn sử dụng"),
        ):
            if key in item and item[key] is not None and not isinstance(item[key], str):
                errors.append(f"Dòng {index}: {label} phải là chuỗi")
        pallet_id = clean_text(item.get("pallet_id")).upper()
        barcode = clean_text(item.get("barcode"))
        if not pallet_id:
            errors.append(f"Dòng {index}: pallet ID là bắt buộc")
        elif pallet_id in seen_pallets:
            errors.append(f"Dòng {index}: pallet ID bị trùng")
        seen_pallets.add(pallet_id)
        if not barcode:
            errors.append(f"Dòng {index}: barcode là bắt buộc")
        expiry_date = clean_text(item.get("expiry_date"))
        if expiry_date:
            try:
                date.fromisoformat(expiry_date)
            except ValueError:
                errors.append(f"Dòng {index}: hạn sử dụng không đúng định dạng")
        if qty <= 0:
            errors.append(f"Dòng {index}: số lượng phải lớn hơn 0")
        if price < 0:
            errors.append(f"Dòng {index}: đơn giá không được âm")
    return errors


def save_items(db, receipt_id, items):
    for item in items:
        product = db.execute(
            "SELECT barcode,unit FROM products WHERE id=? AND 1=1", (int(item["product_id"]),)
        ).fetchone()
        if not product:
            raise sqlite3.IntegrityError("product not found")
        submitted_barcode = str(item.get("barcode", "")).strip()
        if submitted_barcode != product["barcode"]:
            raise sqlite3.IntegrityError("barcode does not match product")
        db.execute(
            """INSERT INTO receipt_items
               (receipt_id,product_id,planned_qty,actual_qty,rejected_qty,rejection_reason,
                unit_price,pallet_id,barcode,unit,expiry_date)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                receipt_id,
                int(item["product_id"]),
                float(item["planned_qty"]),
                None,
                0,
                "",
                float(item.get("unit_price", 0)),
                str(item["pallet_id"]).strip().upper(),
                product["barcode"],
                product["unit"],
                str(item.get("expiry_date", "")).strip() or None,
            ),
        )


def validate_receipt_master(get_db, data):
    errors = []
    supplier = str(data.get("supplier", "")).strip()
    warehouse = str(data.get("warehouse", "")).strip()
    with get_db() as db:
        if supplier and not db.execute(
            "SELECT 1 FROM suppliers WHERE name=? AND status='active'", (supplier,)
        ).fetchone():
            errors.append("Nhà cung cấp không tồn tại hoặc đã ngừng hoạt động")
        if warehouse and not db.execute(
            "SELECT 1 FROM warehouses WHERE name=? AND status='active'", (warehouse,)
        ).fetchone():
            errors.append("Kho không tồn tại hoặc đã ngừng hoạt động")
    return errors


def make_receipt_code(db):
    prefix = f"NK-{date.today():%Y%m%d}-"
    last = db.execute(
        "SELECT code FROM receipts WHERE code LIKE ? ORDER BY code DESC LIMIT 1",
        (f"{prefix}%",),
    ).fetchone()
    sequence = int(last["code"].rsplit("-", 1)[-1]) + 1 if last else 1
    return f"{prefix}{sequence:03d}"


def json_object():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def clean_text(value):
    return value.strip() if isinstance(value, str) else ""


def finite_float(value):
    if isinstance(value, bool):
        raise ValueError("boolean is not a numeric quantity")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("number must be finite")
    return number


def positive_int(value):
    if isinstance(value, bool):
        raise ValueError("boolean is not an identifier")
    if isinstance(value, int):
        number = value
    elif isinstance(value, str) and value.strip().isdigit():
        number = int(value.strip())
    else:
        raise ValueError("identifier must be an integer")
    if number <= 0:
        raise ValueError("identifier must be positive")
    return number


def add_audit(db, action, entity_type, entity_id, details):
    actor_id = g.user["id"] if getattr(g, "user", None) else None
    db.execute(
        """INSERT INTO audit_logs
           (action,entity_type,entity_id,actor_user_id,details,created_at)
           VALUES(?,?,?,?,?,?)""",
        (
            action, entity_type, entity_id, actor_id, details,
            datetime.now().isoformat(timespec="seconds"),
        ),
    )


if __name__ == "__main__":
    application = create_app()
    application.run(debug=os.environ.get("FLASK_DEBUG") == "1")
