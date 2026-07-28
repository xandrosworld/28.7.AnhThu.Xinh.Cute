import csv
import io
import json
import os
import sqlite3
from datetime import date, datetime

from flask import Flask, Response, jsonify, redirect, render_template, request, url_for


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
        JSON_AS_ASCII=False,
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

    @app.template_filter("number")
    def number_filter(value):
        return f"{float(value or 0):,.0f}".replace(",", ".")

    @app.context_processor
    def inject_globals():
        return {"today": date.today().isoformat()}

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

    @app.route("/")
    def index():
        return redirect(url_for("dashboard"))

    @app.route("/dashboard")
    def dashboard():
        return render_template("dashboard.html", active="dashboard", page="dashboard")

    @app.route("/receipts")
    def receipts_page():
        return render_template("receipts.html", active="receipts", page="receipts")

    @app.route("/receipts/new")
    def receipt_new():
        return render_template("receipt_form.html", active="receipts", page="receipt-form", receipt_id=None)

    @app.route("/receipts/<int:receipt_id>")
    def receipt_detail(receipt_id):
        return render_template("receipt_detail.html", active="receipts", page="receipt-detail", receipt_id=receipt_id)

    @app.route("/receipts/<int:receipt_id>/edit")
    def receipt_edit(receipt_id):
        return render_template("receipt_form.html", active="receipts", page="receipt-form", receipt_id=receipt_id)

    @app.route("/receipts/<int:receipt_id>/inspect")
    def receipt_inspect(receipt_id):
        return render_template("inspection.html", active="receipts", page="inspection", receipt_id=receipt_id)

    @app.route("/history")
    def history_page():
        return render_template("history.html", active="history", page="history")

    @app.route("/reports")
    def reports_page():
        return render_template("reports.html", active="reports", page="reports")

    @app.get("/api/products")
    def api_products():
        with get_db() as db:
            rows = db.execute(
                "SELECT id, sku, name, category, unit, current_stock, min_stock, unit_price "
                "FROM products ORDER BY name"
            ).fetchall()
        return jsonify([dict(row) for row in rows])

    @app.get("/api/dashboard")
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
    def api_receipts():
        search = request.args.get("q", "").strip()
        status = request.args.get("status", "").strip()
        start = request.args.get("start", "").strip()
        end = request.args.get("end", "").strip()
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
    def api_receipt_create():
        data = request.get_json(silent=True) or {}
        errors = validate_receipt(data)
        if errors:
            return jsonify(error="; ".join(errors)), 400
        now = datetime.now().isoformat(timespec="seconds")
        code = make_receipt_code(get_db)
        try:
            with get_db() as db:
                cursor = db.execute(
                    """
                    INSERT INTO receipts
                    (code,supplier,warehouse,received_date,status,vehicle_no,container_no,note,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        code,
                        data["supplier"].strip(),
                        data["warehouse"].strip(),
                        data["received_date"],
                        "pending",
                        data.get("vehicle_no", "").strip(),
                        data.get("container_no", "").strip(),
                        data.get("note", "").strip(),
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
    def api_receipt_detail(receipt_id):
        with get_db() as db:
            receipt = db.execute("SELECT * FROM receipts WHERE id=?", (receipt_id,)).fetchone()
            if not receipt:
                return jsonify(error="Không tìm thấy phiếu nhập."), 404
            items = db.execute(
                """
                SELECT i.*, p.sku, p.name, p.unit, p.current_stock
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
    def api_receipt_update(receipt_id):
        data = request.get_json(silent=True) or {}
        errors = validate_receipt(data)
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
                    container_no=?,note=?,updated_at=? WHERE id=?
                    """,
                    (
                        data["supplier"].strip(),
                        data["warehouse"].strip(),
                        data["received_date"],
                        data.get("vehicle_no", "").strip(),
                        data.get("container_no", "").strip(),
                        data.get("note", "").strip(),
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
    def api_inspection(receipt_id):
        data = request.get_json(silent=True) or {}
        checklist = data.get("checklist") or {}
        result = data.get("result")
        missing = [key for key in CHECKLIST_KEYS if checklist.get(key) not in {"pass", "fail"}]
        if missing:
            return jsonify(error="Vui lòng kiểm tra đủ 7 tiêu chí."), 400
        if result not in {"pass", "fail"}:
            return jsonify(error="Kết quả kiểm tra không hợp lệ."), 400
        if result == "pass" and any(value == "fail" for value in checklist.values()):
            return jsonify(error="Không thể chọn Đạt khi checklist còn tiêu chí không đạt."), 400
        actual_quantities = data.get("actual_quantities") or {}
        now = datetime.now().isoformat(timespec="seconds")
        with get_db() as db:
            receipt = db.execute("SELECT * FROM receipts WHERE id=?", (receipt_id,)).fetchone()
            if not receipt:
                return jsonify(error="Không tìm thấy phiếu nhập."), 404
            if receipt["status"] == "completed":
                return jsonify(error="Phiếu đã hoàn tất."), 409
            items = db.execute("SELECT id FROM receipt_items WHERE receipt_id=?", (receipt_id,)).fetchall()
            for item in items:
                raw = actual_quantities.get(str(item["id"]))
                try:
                    qty = float(raw)
                except (TypeError, ValueError):
                    return jsonify(error="Số lượng thực nhập phải là số dương."), 400
                if qty <= 0:
                    return jsonify(error="Số lượng thực nhập phải lớn hơn 0."), 400
                db.execute("UPDATE receipt_items SET actual_qty=? WHERE id=?", (qty, item["id"]))
            db.execute(
                """
                INSERT INTO inspections(receipt_id,checklist_json,result,note,inspected_by,inspected_at)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(receipt_id) DO UPDATE SET checklist_json=excluded.checklist_json,
                result=excluded.result,note=excluded.note,inspected_by=excluded.inspected_by,
                inspected_at=excluded.inspected_at
                """,
                (
                    receipt_id,
                    json.dumps(checklist, ensure_ascii=False),
                    result,
                    data.get("note", "").strip(),
                    "Nguyễn Văn A",
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
                "UPDATE receipts SET status='completed',completed_at=?,updated_at=? WHERE id=?",
                (now, now, receipt_id),
            )
            add_audit(db, "COMPLETE", "receipt", receipt_id, f"Hoàn tất nhập kho {receipt['code']}")
            db.commit()
        except sqlite3.IntegrityError:
            db.rollback()
            return jsonify(message="Phiếu đã được ghi nhận tồn kho.", already_completed=True)
        finally:
            db.close()
        return jsonify(message="Hoàn tất nhập kho và cập nhật tồn kho.", already_completed=False)

    @app.get("/api/history")
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
    def api_reports():
        start = request.args.get("start") or date.today().replace(day=1).isoformat()
        end = request.args.get("end") or date.today().isoformat()
        with get_db() as db:
            summary = db.execute(
                """
                SELECT COUNT(DISTINCT r.id) receipt_count,
                       COALESCE(SUM(i.actual_qty),0) total_quantity,
                       COALESCE(SUM(i.actual_qty*i.unit_price),0) total_value,
                       COUNT(DISTINCT r.supplier) supplier_count
                FROM receipts r LEFT JOIN receipt_items i ON i.receipt_id=r.id
                WHERE r.status='completed' AND date(r.completed_at) BETWEEN date(?) AND date(?)
                """,
                (start, end),
            ).fetchone()
            rows = db.execute(
                """
                SELECT r.code,r.supplier,r.warehouse,r.completed_at,
                       SUM(i.actual_qty) quantity,SUM(i.actual_qty*i.unit_price) value
                FROM receipts r JOIN receipt_items i ON i.receipt_id=r.id
                WHERE r.status='completed' AND date(r.completed_at) BETWEEN date(?) AND date(?)
                GROUP BY r.id ORDER BY r.completed_at DESC
                """,
                (start, end),
            ).fetchall()
            top_products = db.execute(
                """
                SELECT p.sku,p.name,SUM(i.actual_qty) quantity,p.unit
                FROM receipt_items i JOIN receipts r ON r.id=i.receipt_id
                JOIN products p ON p.id=i.product_id
                WHERE r.status='completed' AND date(r.completed_at) BETWEEN date(?) AND date(?)
                GROUP BY p.id ORDER BY quantity DESC LIMIT 5
                """,
                (start, end),
            ).fetchall()
        return jsonify(
            start=start,
            end=end,
            summary=dict(summary),
            receipts=[dict(row) for row in rows],
            top_products=[dict(row) for row in top_products],
        )

    @app.get("/reports/export.csv")
    def export_report():
        start = request.args.get("start") or "2000-01-01"
        end = request.args.get("end") or date.today().isoformat()
        with get_db() as db:
            rows = db.execute(
                """
                SELECT r.code,r.supplier,r.warehouse,r.completed_at,p.sku,p.name,
                       i.actual_qty,p.unit,i.unit_price,(i.actual_qty*i.unit_price) value
                FROM receipts r JOIN receipt_items i ON i.receipt_id=r.id
                JOIN products p ON p.id=i.product_id
                WHERE r.status='completed' AND date(r.completed_at) BETWEEN date(?) AND date(?)
                ORDER BY r.completed_at DESC,r.code,p.sku
                """,
                (start, end),
            ).fetchall()
        output = io.StringIO()
        output.write("\ufeff")
        writer = csv.writer(output)
        writer.writerow(
            ["Mã phiếu", "Nhà cung cấp", "Kho", "Hoàn tất", "SKU", "Hàng hóa", "Số lượng", "ĐVT", "Đơn giá", "Thành tiền"]
        )
        for row in rows:
            writer.writerow(list(row))
        return Response(
            output.getvalue(),
            mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename=bao-cao-nhap-kho-{start}-{end}.csv"},
        )

    from database import init_database

    init_database(app.config["DATABASE"])
    return app


def validate_receipt(data):
    errors = []
    for key, label in (("supplier", "Nhà cung cấp"), ("warehouse", "Kho"), ("received_date", "Ngày nhập")):
        if not str(data.get(key, "")).strip():
            errors.append(f"{label} là bắt buộc")
    try:
        datetime.fromisoformat(str(data.get("received_date", "")))
    except ValueError:
        errors.append("Ngày nhập không đúng định dạng")
    items = data.get("items")
    if not isinstance(items, list) or not items:
        errors.append("Phiếu nhập phải có ít nhất một mặt hàng")
        return errors
    seen = set()
    for index, item in enumerate(items, 1):
        try:
            product_id = int(item.get("product_id"))
            qty = float(item.get("planned_qty"))
            price = float(item.get("unit_price", 0))
        except (TypeError, ValueError):
            errors.append(f"Dòng {index}: dữ liệu hàng hóa không hợp lệ")
            continue
        if product_id in seen:
            errors.append(f"Dòng {index}: mặt hàng bị trùng")
        seen.add(product_id)
        if qty <= 0:
            errors.append(f"Dòng {index}: số lượng phải lớn hơn 0")
        if price < 0:
            errors.append(f"Dòng {index}: đơn giá không được âm")
    return errors


def save_items(db, receipt_id, items):
    for item in items:
        db.execute(
            "INSERT INTO receipt_items(receipt_id,product_id,planned_qty,actual_qty,unit_price) VALUES(?,?,?,?,?)",
            (
                receipt_id,
                int(item["product_id"]),
                float(item["planned_qty"]),
                None,
                float(item.get("unit_price", 0)),
            ),
        )


def make_receipt_code(get_db):
    prefix = f"NK-{date.today():%Y%m%d}-"
    with get_db() as db:
        last = db.execute(
            "SELECT code FROM receipts WHERE code LIKE ? ORDER BY code DESC LIMIT 1", (f"{prefix}%",)
        ).fetchone()
    sequence = int(last["code"].rsplit("-", 1)[-1]) + 1 if last else 1
    return f"{prefix}{sequence:03d}"


def add_audit(db, action, entity_type, entity_id, details):
    db.execute(
        "INSERT INTO audit_logs(action,entity_type,entity_id,details,created_at) VALUES(?,?,?,?,?)",
        (action, entity_type, entity_id, details, datetime.now().isoformat(timespec="seconds")),
    )


if __name__ == "__main__":
    application = create_app()
    application.run(debug=True)
