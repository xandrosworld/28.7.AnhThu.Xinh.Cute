import csv
import io
import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from flask import Blueprint, Response, g, jsonify, request
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

from .auth import csrf_required, login_required, roles_required
from .db import audit, get_db
from .extensions import db as orm
from .models import CustomerContractEmail, InventoryAdjustment
from .services import DomainError, available_quantity
from .services import confirm_receipt as confirm_receipt_service
from .services import confirm_stocktake as confirm_stocktake_service
from .services import picking_list as build_picking_list
from .services import set_stock

bp = Blueprint("api", __name__, url_prefix="/api")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{4,30}$")
CODE_RE = re.compile(r"^[A-Za-z0-9_-]{2,20}$")
ROLE_LABELS = {
    "admin": "Quản trị viên",
    "cs": "Chăm sóc khách hàng",
    "warehouse": "Nhân viên kho",
    "manager": "Chăm sóc khách hàng (tương thích)",
    "staff": "Nhân viên kho (tương thích)",
}
USER_ACTIVITY_COUNT_SQL = """
SELECT
  (SELECT COUNT(*) FROM inventory_adjustments WHERE created_by = ?)
  + (SELECT COUNT(*) FROM receipts WHERE created_by = ? OR confirmed_by = ?)
  + (SELECT COUNT(*) FROM stocktakes WHERE created_by = ? OR confirmed_by = ?)
  + (SELECT COUNT(*) FROM stock_movements WHERE created_by = ?)
  AS activity_count
"""


def error(message, status=400, errors=None):
    payload = {
        "ok": False,
        "message": message,
        "error": {
            "code": {
                400: "bad_request", 401: "unauthorized", 403: "forbidden",
                404: "not_found", 409: "conflict", 422: "validation_error",
            }.get(status, "request_failed"),
            "message": message,
            "fields": errors or {},
        },
    }
    if errors:
        payload["errors"] = errors
    return jsonify(payload), status


def text(value):
    return str(value or "").strip()


def to_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def default_warehouse_id(database=None):
    """Return the single operational warehouse used by the application UI."""
    database = database or get_db()
    row = database.execute(
        """SELECT id FROM warehouses
           WHERE status='active'
           ORDER BY CASE WHEN UPPER(code)='DN' THEN 0 ELSE 1 END, id"""
    ).fetchone()
    return row["id"] if row else None


def to_quantity(value, *, allow_zero=False):
    try:
        result = Decimal(str(value)).quantize(Decimal("0.001"))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not result.is_finite() or result < 0 or (result == 0 and not allow_zero):
        return None
    return int(result) if result == result.to_integral_value() else float(result)


def json_object():
    payload = request.get_json(silent=True)
    return payload if isinstance(payload, dict) else {}


def initials(full_name):
    parts = [item for item in full_name.split() if item]
    return "".join(item[0].upper() for item in parts[-2:]) or "DN"


def validate_date_range(from_date, to_date):
    errors = {}
    parsed_dates = {}
    for query_name, key, value in (
        ("from", "from_date", from_date),
        ("to", "to_date", to_date),
    ):
        if not value:
            continue
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            errors[query_name] = "Ngày phải có định dạng YYYY-MM-DD."
            continue
        try:
            parsed_dates[key] = date.fromisoformat(value)
        except ValueError:
            errors[query_name] = "Ngày không tồn tại."
    if (
        "from_date" in parsed_dates
        and "to_date" in parsed_dates
        and parsed_dates["from_date"] > parsed_dates["to_date"]
    ):
        errors["from"] = "Ngày bắt đầu không được sau ngày kết thúc."
        errors["to"] = "Ngày kết thúc không được trước ngày bắt đầu."
    return errors


def serialize_inventory(row):
    quantity = row["quantity"]
    minimum = row["min_quantity"]
    if quantity == 0:
        status = "out"
        status_label = "Hết hàng"
    elif quantity <= minimum:
        status = "low"
        status_label = "Sắp thiếu"
    else:
        status = "good"
        status_label = "Đủ hàng"
    return {
        "id": row["id"],
        "sku": row["sku"],
        "name": row["name"],
        "category_id": row["category_id"],
        "category_name": row["category_name"],
        "warehouse_id": row["warehouse_id"],
        "warehouse_name": row["warehouse_name"],
        "unit": row["unit"],
        "barcode": row["barcode"] or "",
        "quantity": quantity,
        "available_quantity": row["available_quantity"],
        "min_quantity": minimum,
        "location": row["location"],
        "description": row["description"],
        "updated_at": row["updated_at"],
        "status": status,
        "status_label": status_label,
    }


@bp.get("/dashboard")
@login_required
def dashboard():
    database = get_db()
    summary = database.execute(
        """
        SELECT COUNT(*) AS products,
               COALESCE(SUM(quantity), 0) AS total_quantity,
               SUM(CASE WHEN quantity = 0 THEN 1 ELSE 0 END) AS out_of_stock,
               SUM(CASE WHEN quantity > 0 AND quantity <= min_quantity THEN 1 ELSE 0 END) AS low_stock
        FROM inventory
        """
    ).fetchone()
    today = date.today().isoformat()
    receipt_summary = database.execute(
        """
        SELECT
          COALESCE(SUM(CASE
            WHEN receipt_type='inbound' AND status='completed'
             AND confirmed_at >= ? THEN 1 ELSE 0 END), 0) AS inbound_today,
          COALESCE(SUM(CASE
            WHEN receipt_type='outbound' AND status='completed'
             AND confirmed_at >= ? THEN 1 ELSE 0 END), 0) AS outbound_today,
          COALESCE(SUM(CASE
            WHEN status IN ('pending','picking') THEN 1 ELSE 0 END), 0)
            AS awaiting_processing
        FROM receipts
        """,
        (today, today),
    ).fetchone()
    summary_payload = dict(summary)
    summary_payload.update(dict(receipt_summary))
    recent = database.execute(
        """
        SELECT a.id, a.old_quantity, a.new_quantity, a.difference, a.reason,
               a.created_at, i.sku, i.name, u.full_name
        FROM inventory_adjustments a
        JOIN inventory i ON i.id = a.inventory_id
        JOIN users u ON u.id = a.created_by
        ORDER BY a.id DESC LIMIT 6
        """
    ).fetchall()
    categories = database.execute(
        """
        SELECT c.name,
               (SELECT COUNT(*) FROM inventory i WHERE i.category_id=c.id) AS product_count,
               COALESCE((SELECT SUM(i.quantity) FROM inventory i WHERE i.category_id=c.id),0) AS quantity
        FROM categories c
        WHERE c.status = 'active'
        ORDER BY quantity DESC
        """
    ).fetchall()
    return jsonify(
        ok=True,
        summary=summary_payload,
        recent_adjustments=[dict(row) for row in recent],
        category_distribution=[dict(row) for row in categories],
    )


@bp.get("/lookups")
@login_required
def lookups():
    database = get_db()
    categories = database.execute(
        "SELECT id, code, name FROM categories WHERE status = 'active' ORDER BY name"
    ).fetchall()
    warehouses = database.execute(
        "SELECT id, code, name FROM warehouses WHERE status = 'active' ORDER BY name"
    ).fetchall()
    return jsonify(
        ok=True,
        categories=[dict(row) for row in categories],
        warehouses=[dict(row) for row in warehouses],
    )


@bp.get("/roles")
@login_required
def role_list():
    return jsonify(
        ok=True,
        items=_list_rows(
            "SELECT id,code,name,description,status FROM roles ORDER BY id"
        ),
    )


@bp.get("/units")
@login_required
def unit_list():
    return jsonify(
        ok=True,
        items=_list_rows(
            "SELECT id,code,name,allow_break_pack,status FROM units ORDER BY name"
        ),
    )


def _master_status(value):
    return value if value in {"active", "inactive"} else None


@bp.post("/roles")
@roles_required("admin")
@csrf_required
def role_create():
    data = json_object()
    code = text(data.get("code")).upper()
    name = text(data.get("name"))
    description = text(data.get("description"))
    status = _master_status(text(data.get("status")) or "active")
    errors = {}
    if not CODE_RE.fullmatch(code):
        errors["code"] = "Mã vai trò không hợp lệ."
    if len(name) < 2:
        errors["name"] = "Tên vai trò phải có ít nhất 2 ký tự."
    if status is None:
        errors["status"] = "Trạng thái không hợp lệ."
    if errors:
        return error("Dữ liệu vai trò chưa hợp lệ.", 422, errors)
    database = get_db()
    try:
        cursor = database.execute(
            """INSERT INTO roles (code,name,description,status)
               VALUES (?,?,?,?)""",
            (code, name, description, status),
        )
        audit("CREATE", "role", cursor.lastrowid, {"code": code}, g.user["id"], request.remote_addr)
        database.commit()
    except IntegrityError:
        database.rollback()
        return error("Mã vai trò đã tồn tại.", 409)
    return jsonify(ok=True, id=cursor.lastrowid, message="Đã thêm vai trò."), 201


@bp.put("/roles/<int:role_id>")
@roles_required("admin")
@csrf_required
def role_update(role_id):
    data = json_object()
    database = get_db()
    current = database.execute("SELECT * FROM roles WHERE id=?", (role_id,)).fetchone()
    if current is None:
        return error("Không tìm thấy vai trò.", 404)
    code = text(data.get("code", current["code"])).upper()
    name = text(data.get("name", current["name"]))
    description = text(data.get("description", current["description"]))
    status = _master_status(text(data.get("status", current["status"])))
    errors = {}
    if not CODE_RE.fullmatch(code):
        errors["code"] = "Mã vai trò không hợp lệ."
    if len(name) < 2:
        errors["name"] = "Tên vai trò phải có ít nhất 2 ký tự."
    if status is None:
        errors["status"] = "Trạng thái không hợp lệ."
    active_users = database.execute(
        "SELECT COUNT(*) FROM users WHERE role_id=? AND status='active'", (role_id,)
    ).fetchone()[0]
    if status == "inactive" and active_users:
        errors["status"] = "Không thể ngừng vai trò đang có tài khoản hoạt động."
    if errors:
        return error("Dữ liệu vai trò chưa hợp lệ.", 422, errors)
    try:
        database.execute(
            "UPDATE roles SET code=?,name=?,description=?,status=? WHERE id=?",
            (code, name, description, status, role_id),
        )
        audit("UPDATE", "role", role_id, {"code": code}, g.user["id"], request.remote_addr)
        database.commit()
    except IntegrityError:
        database.rollback()
        return error("Mã vai trò đã tồn tại.", 409)
    return jsonify(ok=True, id=role_id, message="Đã cập nhật vai trò.")


@bp.post("/units")
@roles_required("admin")
@csrf_required
def unit_create():
    data = json_object()
    code = text(data.get("code")).upper()
    name = text(data.get("name"))
    status = _master_status(text(data.get("status")) or "active")
    errors = {}
    if not CODE_RE.fullmatch(code):
        errors["code"] = "Mã đơn vị không hợp lệ."
    if len(name) < 1:
        errors["name"] = "Tên đơn vị là bắt buộc."
    if status is None:
        errors["status"] = "Trạng thái không hợp lệ."
    if errors:
        return error("Dữ liệu đơn vị chưa hợp lệ.", 422, errors)
    database = get_db()
    try:
        cursor = database.execute(
            """INSERT INTO units (code,name,allow_break_pack,status)
               VALUES (?,?,?,?)""",
            (code, name, bool(data.get("allow_break_pack")), status),
        )
        audit("CREATE", "unit", cursor.lastrowid, {"code": code}, g.user["id"], request.remote_addr)
        database.commit()
    except IntegrityError:
        database.rollback()
        return error("Mã đơn vị đã tồn tại.", 409)
    return jsonify(ok=True, id=cursor.lastrowid, message="Đã thêm đơn vị."), 201


@bp.put("/units/<int:unit_id>")
@roles_required("admin")
@csrf_required
def unit_update(unit_id):
    data = json_object()
    database = get_db()
    current = database.execute("SELECT * FROM units WHERE id=?", (unit_id,)).fetchone()
    if current is None:
        return error("Không tìm thấy đơn vị.", 404)
    code = text(data.get("code", current["code"])).upper()
    name = text(data.get("name", current["name"]))
    status = _master_status(text(data.get("status", current["status"])))
    errors = {}
    if not CODE_RE.fullmatch(code):
        errors["code"] = "Mã đơn vị không hợp lệ."
    if not name:
        errors["name"] = "Tên đơn vị là bắt buộc."
    if status is None:
        errors["status"] = "Trạng thái không hợp lệ."
    in_use = database.execute(
        "SELECT COUNT(*) FROM inventory WHERE unit_id=?", (unit_id,)
    ).fetchone()[0]
    if status == "inactive" and in_use:
        errors["status"] = "Không thể ngừng đơn vị đang được hàng hóa sử dụng."
    if errors:
        return error("Dữ liệu đơn vị chưa hợp lệ.", 422, errors)
    try:
        database.execute(
            """UPDATE units SET code=?,name=?,allow_break_pack=?,status=?
               WHERE id=?""",
            (
                code, name, bool(data.get("allow_break_pack", current["allow_break_pack"])),
                status, unit_id,
            ),
        )
        # Keep the public text alias synchronized with the authoritative FK.
        database.execute("UPDATE inventory SET unit=? WHERE unit_id=?", (name, unit_id))
        audit("UPDATE", "unit", unit_id, {"code": code}, g.user["id"], request.remote_addr)
        database.commit()
    except IntegrityError:
        database.rollback()
        return error("Mã đơn vị đã tồn tại.", 409)
    return jsonify(ok=True, id=unit_id, message="Đã cập nhật đơn vị.")


@bp.get("/inventory")
@login_required
def inventory_list():
    database = get_db()
    page = max(to_int(request.args.get("page"), 1), 1)
    per_page = min(max(to_int(request.args.get("per_page"), 10), 5), 50)
    search = text(request.args.get("search"))
    category_id = to_int(request.args.get("category_id"))
    warehouse_id = to_int(request.args.get("warehouse_id"))
    stock_status = text(request.args.get("status"))

    clauses = ["1=1"]
    params = []
    if search:
        clauses.append(
            """(i.sku LIKE ? OR i.name LIKE ? OR COALESCE(i.barcode,'') LIKE ?
                OR i.unit LIKE ? OR EXISTS (
                    SELECT 1 FROM inventory_lots lot
                    WHERE lot.product_id=i.id
                      AND (lot.pallet_id LIKE ? OR COALESCE(lot.barcode,'') LIKE ?)
                ))"""
        )
        params.extend([f"%{search}%"] * 6)
    if category_id:
        clauses.append("i.category_id = ?")
        params.append(category_id)
    if warehouse_id:
        clauses.append("i.warehouse_id = ?")
        params.append(warehouse_id)
    if stock_status == "out":
        clauses.append("i.quantity = 0")
    elif stock_status == "low":
        clauses.append("i.quantity > 0 AND i.quantity <= i.min_quantity")
    elif stock_status == "good":
        clauses.append("i.quantity > i.min_quantity")

    where = " AND ".join(clauses)
    total = database.execute(
        f"SELECT COUNT(*) FROM inventory i WHERE {where}", params
    ).fetchone()[0]
    rows = database.execute(
        f"""
        SELECT i.*, c.name AS category_name, w.name AS warehouse_name,
               COALESCE((
                   SELECT SUM(lot.quantity) FROM inventory_lots lot
                   WHERE lot.product_id=i.id AND lot.warehouse_id=i.warehouse_id
                     AND lot.status='active' AND lot.quantity>0
                     AND (lot.expiry_date IS NULL OR lot.expiry_date>=?)
               ),0) AS available_quantity
        FROM inventory i
        JOIN categories c ON c.id = i.category_id
        JOIN warehouses w ON w.id = i.warehouse_id
        WHERE {where}
        ORDER BY i.updated_at DESC, i.id DESC
        LIMIT ? OFFSET ?
        """,
        [date.today().isoformat(), *params, per_page, (page - 1) * per_page],
    ).fetchall()
    return jsonify(
        ok=True,
        items=[serialize_inventory(row) for row in rows],
        pagination={
            "page": page,
            "per_page": per_page,
            "total": total,
            "pages": max((total + per_page - 1) // per_page, 1),
        },
    )


@bp.get("/inventory/<int:item_id>")
@login_required
def inventory_detail(item_id):
    database = get_db()
    row = database.execute(
        """
        SELECT i.*, c.name AS category_name, w.name AS warehouse_name,
               COALESCE((
                   SELECT SUM(lot.quantity) FROM inventory_lots lot
                   WHERE lot.product_id=i.id AND lot.warehouse_id=i.warehouse_id
                     AND lot.status='active' AND lot.quantity>0
                     AND (lot.expiry_date IS NULL OR lot.expiry_date>=?)
               ),0) AS available_quantity
        FROM inventory i
        JOIN categories c ON c.id = i.category_id
        JOIN warehouses w ON w.id = i.warehouse_id
        WHERE i.id = ?
        """,
        (date.today().isoformat(), item_id),
    ).fetchone()
    if row is None:
        return error("Không tìm thấy hàng hóa.", 404)
    history = database.execute(
        """
        SELECT a.id, a.old_quantity, a.new_quantity, a.difference, a.reason,
               a.note, a.created_at, u.full_name AS created_by_name
        FROM inventory_adjustments a
        JOIN users u ON u.id = a.created_by
        WHERE a.inventory_id = ?
        ORDER BY a.id DESC LIMIT 10
        """,
        (item_id,),
    ).fetchall()
    movements = database.execute(
        """
        SELECT sm.*, u.full_name AS created_by_name
        FROM stock_movements sm
        JOIN users u ON u.id=sm.created_by
        WHERE sm.inventory_id=?
        ORDER BY sm.id DESC LIMIT 20
        """,
        (item_id,),
    ).fetchall()
    return jsonify(
        ok=True,
        item=serialize_inventory(row),
        adjustments=[dict(entry) for entry in history],
        movements=[dict(entry) for entry in movements],
    )


@bp.get("/stock-movements")
@login_required
def stock_movement_list():
    page = max(to_int(request.args.get("page"), 1), 1)
    per_page = min(max(to_int(request.args.get("per_page"), 20), 1), 100)
    search = text(request.args.get("search"))
    reference = text(
        request.args.get("reference") or request.args.get("reference_code")
    )
    movement_type = text(request.args.get("movement_type"))
    warehouse_id = to_int(request.args.get("warehouse_id"))
    product_arg = request.args.get("product_id")
    inventory_arg = request.args.get("inventory_id")
    product_id = to_int(product_arg if product_arg not in (None, "") else inventory_arg)
    from_date = text(request.args.get("from"))
    to_date = text(request.args.get("to"))
    errors = validate_date_range(from_date, to_date)
    if movement_type and movement_type not in {
        "inbound", "outbound", "stocktake", "adjustment"
    }:
        errors["movement_type"] = "Loại biến động không hợp lệ."
    if product_arg not in (None, "") and to_int(product_arg) is None:
        errors["product_id"] = "Mã hàng hóa phải là số nguyên."
    if inventory_arg not in (None, "") and to_int(inventory_arg) is None:
        errors["inventory_id"] = "Mã hàng hóa phải là số nguyên."
    if (
        product_arg not in (None, "")
        and inventory_arg not in (None, "")
        and to_int(product_arg) != to_int(inventory_arg)
    ):
        errors["inventory_id"] = "Hai tham số hàng hóa phải cùng giá trị."
    if errors:
        return error("Bộ lọc biến động kho chưa hợp lệ.", 422, errors)

    clauses, params = ["1=1"], []
    if search:
        clauses.append(
            """(sm.reference_code LIKE ? OR i.sku LIKE ? OR i.name LIKE ?
                OR sm.pallet_id LIKE ?)"""
        )
        params.extend([f"%{search}%"] * 4)
    if reference:
        clauses.append("sm.reference_code LIKE ?")
        params.append(f"%{reference}%")
    if product_id:
        clauses.append("sm.inventory_id=?")
        params.append(product_id)
    if warehouse_id:
        clauses.append("i.warehouse_id=?")
        params.append(warehouse_id)
    if movement_type:
        clauses.append("sm.movement_type=?")
        params.append(movement_type)
    if from_date:
        clauses.append("date(sm.created_at)>=date(?)")
        params.append(from_date)
    if to_date:
        clauses.append("date(sm.created_at)<=date(?)")
        params.append(to_date)
    where = " AND ".join(clauses)
    database = get_db()
    total = database.execute(
        f"""SELECT COUNT(*) FROM stock_movements sm
            JOIN inventory i ON i.id=sm.inventory_id WHERE {where}""",
        params,
    ).fetchone()[0]
    rows = database.execute(
        f"""SELECT sm.*,i.sku,i.name,i.unit,i.warehouse_id,
                   w.name AS warehouse_name,u.full_name AS created_by_name
            FROM stock_movements sm
            JOIN inventory i ON i.id=sm.inventory_id
            JOIN warehouses w ON w.id=i.warehouse_id
            JOIN users u ON u.id=sm.created_by
            WHERE {where}
            ORDER BY sm.id DESC LIMIT ? OFFSET ?""",
        [*params, per_page, (page - 1) * per_page],
    ).fetchall()
    return jsonify(
        ok=True,
        items=[dict(row) for row in rows],
        pagination={
            "page": page,
            "per_page": per_page,
            "total": total,
            "pages": max((total + per_page - 1) // per_page, 1),
        },
    )


@bp.post("/inventory/<int:item_id>/adjustments")
@roles_required("admin", "manager", "warehouse")
@csrf_required
def adjust_inventory(item_id):
    data = json_object()
    new_quantity = to_quantity(data.get("new_quantity"), allow_zero=True)
    reason = text(data.get("reason"))
    note = text(data.get("note"))
    errors = {}
    if new_quantity is None or new_quantity < 0:
        errors["new_quantity"] = "Số lượng thực tế phải là số nguyên không âm."
    if reason not in {
        "Cập nhật định kỳ",
        "Kiểm kê định kỳ",
        "Hàng hư hỏng",
        "Sai lệch chứng từ",
        "Điều chỉnh khác",
    }:
        errors["reason"] = "Vui lòng chọn lý do hợp lệ."
    if reason == "Điều chỉnh khác" and len(note) < 5:
        errors["note"] = "Vui lòng mô tả lý do điều chỉnh (tối thiểu 5 ký tự)."
    if len(note) > 500:
        errors["note"] = "Ghi chú không được vượt quá 500 ký tự."
    if errors:
        return error("Dữ liệu điều chỉnh chưa hợp lệ.", 422, errors)

    database = get_db()
    item = database.execute(
        "SELECT id, sku, name, quantity FROM inventory WHERE id = ?", (item_id,)
    ).fetchone()
    if item is None:
        return error("Không tìm thấy hàng hóa.", 404)
    if new_quantity == item["quantity"]:
        return error("Số lượng mới không thay đổi so với tồn kho hiện tại.", 422)

    try:
        adjustment = InventoryAdjustment(
            inventory_id=item_id,
            old_quantity=item["quantity"],
            new_quantity=new_quantity,
            difference=new_quantity - item["quantity"],
            reason=reason,
            note=note,
            created_by=g.user["id"],
        )
        orm.session.add(adjustment)
        orm.session.flush()
        set_stock(
            item_id,
            new_quantity,
            g.user["id"],
            f"ADJ-{adjustment.id}",
            f"{reason}: {note}".strip(": "),
            expected=item["quantity"],
        )
        audit(
            "ADJUST_STOCK",
            "inventory",
            item_id,
            {
                "sku": item["sku"],
                "old_quantity": item["quantity"],
                "new_quantity": new_quantity,
                "reason": reason,
            },
            g.user["id"],
            request.remote_addr,
        )
        orm.session.commit()
    except DomainError as exc:
        orm.session.rollback()
        return error(str(exc), 409)
    except IntegrityError:
        orm.session.rollback()
        return error("Điều chỉnh đã được ghi nhận.", 409)
    return jsonify(
        ok=True,
        message=f"Đã cập nhật tồn kho {item['sku']}.",
        adjustment_id=adjustment.id,
    )


@bp.get("/categories")
@login_required
def category_list():
    search = text(request.args.get("search"))
    params = []
    where = ""
    if search:
        where = "WHERE c.code LIKE ? OR c.name LIKE ?"
        params = [f"%{search}%", f"%{search}%"]
    rows = get_db().execute(
        f"""
        SELECT c.*,
               (SELECT COUNT(*) FROM inventory i WHERE i.category_id=c.id) AS product_count
        FROM categories c
        {where}
        ORDER BY c.id DESC
        """,
        params,
    ).fetchall()
    return jsonify(ok=True, items=[dict(row) for row in rows])


def validate_category(data):
    code = text(data.get("code")).upper()
    name = text(data.get("name"))
    description = text(data.get("description"))
    status = text(data.get("status")) or "active"
    errors = {}
    if not CODE_RE.fullmatch(code):
        errors["code"] = "Mã gồm 2–20 chữ cái, số, gạch ngang hoặc gạch dưới."
    if not 2 <= len(name) <= 80:
        errors["name"] = "Tên danh mục phải có từ 2 đến 80 ký tự."
    if len(description) > 300:
        errors["description"] = "Mô tả không được vượt quá 300 ký tự."
    if status not in {"active", "inactive"}:
        errors["status"] = "Trạng thái không hợp lệ."
    return (code, name, description, status), errors


@bp.post("/categories")
@roles_required("admin", "manager", "cs")
@csrf_required
def category_create():
    values, errors = validate_category(json_object())
    if errors:
        return error("Dữ liệu danh mục chưa hợp lệ.", 422, errors)
    database = get_db()
    try:
        cursor = database.execute(
            "INSERT INTO categories (code, name, description, status) VALUES (?, ?, ?, ?)",
            values,
        )
        audit(
            "CREATE",
            "category",
            cursor.lastrowid,
            {"code": values[0], "name": values[1]},
            g.user["id"],
            request.remote_addr,
        )
        database.commit()
    except IntegrityError:
        return error("Mã hoặc tên danh mục đã tồn tại.", 409)
    return jsonify(ok=True, message="Đã thêm danh mục.", id=cursor.lastrowid), 201


@bp.put("/categories/<int:category_id>")
@roles_required("admin", "manager", "cs")
@csrf_required
def category_update(category_id):
    values, errors = validate_category(json_object())
    if errors:
        return error("Dữ liệu danh mục chưa hợp lệ.", 422, errors)
    database = get_db()
    current = database.execute(
        "SELECT id FROM categories WHERE id = ?", (category_id,)
    ).fetchone()
    if current is None:
        return error("Không tìm thấy danh mục.", 404)
    try:
        database.execute(
            """
            UPDATE categories SET code = ?, name = ?, description = ?, status = ?,
                                  updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (*values, category_id),
        )
        audit(
            "UPDATE",
            "category",
            category_id,
            {"code": values[0], "name": values[1]},
            g.user["id"],
            request.remote_addr,
        )
        database.commit()
    except IntegrityError:
        return error("Mã hoặc tên danh mục đã tồn tại.", 409)
    return jsonify(ok=True, message="Đã cập nhật danh mục.")


@bp.delete("/categories/<int:category_id>")
@roles_required("admin")
@csrf_required
def category_delete(category_id):
    database = get_db()
    category = database.execute(
        """
        SELECT c.id, c.code, c.name,
               (SELECT COUNT(*) FROM inventory i WHERE i.category_id=c.id) AS product_count
        FROM categories c WHERE c.id = ?
        """,
        (category_id,),
    ).fetchone()
    if category is None:
        return error("Không tìm thấy danh mục.", 404)
    if category["product_count"]:
        return error("Không thể xóa danh mục đang có hàng hóa.", 409)
    database.execute("DELETE FROM categories WHERE id = ?", (category_id,))
    audit(
        "DELETE",
        "category",
        category_id,
        {"code": category["code"], "name": category["name"]},
        g.user["id"],
        request.remote_addr,
    )
    database.commit()
    return jsonify(ok=True, message="Đã xóa danh mục.")


def serialize_user(row):
    return {
        "id": row["id"],
        "username": row["username"],
        "full_name": row["full_name"],
        "email": row["email"],
        "phone": row["phone"],
        "role": row["role"],
        "role_label": ROLE_LABELS[row["role"]],
        "status": row["status"],
        "avatar_initials": row["avatar_initials"],
        "created_at": row["created_at"],
    }


@bp.get("/users")
@roles_required("admin")
def user_list():
    search = text(request.args.get("search"))
    params = []
    where = ""
    if search:
        where = "WHERE username LIKE ? OR full_name LIKE ? OR email LIKE ?"
        params = [f"%{search}%"] * 3
    rows = get_db().execute(
        f"SELECT * FROM users {where} ORDER BY id DESC", params
    ).fetchall()
    return jsonify(ok=True, items=[serialize_user(row) for row in rows])


def validate_user(data, creating=True):
    username = text(data.get("username"))
    full_name = text(data.get("full_name"))
    email = text(data.get("email")).lower()
    phone = text(data.get("phone"))
    role = text(data.get("role"))
    status = text(data.get("status")) or "active"
    password = str(data.get("password", ""))
    errors = {}
    if not USERNAME_RE.fullmatch(username):
        errors["username"] = "Tên đăng nhập gồm 4–30 ký tự hợp lệ."
    if not 2 <= len(full_name) <= 80:
        errors["full_name"] = "Họ tên phải có từ 2 đến 80 ký tự."
    if not EMAIL_RE.fullmatch(email):
        errors["email"] = "Email không đúng định dạng."
    if len(phone) > 20:
        errors["phone"] = "Số điện thoại không được vượt quá 20 ký tự."
    if role not in ROLE_LABELS:
        errors["role"] = "Vai trò không hợp lệ."
    if status not in {"active", "locked"}:
        errors["status"] = "Trạng thái không hợp lệ."
    if creating and len(password) < 8:
        errors["password"] = "Mật khẩu phải có ít nhất 8 ký tự."
    return (username, full_name, email, phone, role, status, password), errors


def _role_id(database, role):
    row = database.execute(
        "SELECT id FROM roles WHERE code = ? AND status='active'", (role.upper(),)
    ).fetchone()
    return row["id"] if row else None


@bp.post("/users")
@roles_required("admin")
@csrf_required
def user_create():
    values, errors = validate_user(json_object())
    if errors:
        return error("Dữ liệu người dùng chưa hợp lệ.", 422, errors)
    username, full_name, email, phone, role, status, password = values
    database = get_db()
    role_id = _role_id(database, role)
    if role_id is None:
        return error("Vai trò chưa được cấu hình.", 409)
    try:
        cursor = database.execute(
            """
            INSERT INTO users
                (username, password_hash, full_name, email, phone, role, role_id,
                 status, avatar_initials)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                username,
                generate_password_hash(password),
                full_name,
                email,
                phone,
                role,
                role_id,
                status,
                initials(full_name),
            ),
        )
        audit(
            "CREATE",
            "user",
            cursor.lastrowid,
            {"username": username, "role": role},
            g.user["id"],
            request.remote_addr,
        )
        database.commit()
    except IntegrityError:
        return error("Tên đăng nhập hoặc email đã tồn tại.", 409)
    return jsonify(ok=True, message="Đã thêm người dùng.", id=cursor.lastrowid), 201


@bp.put("/users/<int:user_id>")
@roles_required("admin")
@csrf_required
def user_update(user_id):
    data = json_object()
    values, errors = validate_user(data, creating=False)
    if errors:
        return error("Dữ liệu người dùng chưa hợp lệ.", 422, errors)
    username, full_name, email, phone, role, status, password = values
    if user_id == g.user["id"] and (status != "active" or role != "admin"):
        return error("Bạn không thể khóa hoặc hạ quyền tài khoản đang đăng nhập.", 422)
    database = get_db()
    if database.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone() is None:
        return error("Không tìm thấy người dùng.", 404)
    role_id = _role_id(database, role)
    if role_id is None:
        return error("Vai trò chưa được cấu hình.", 409)
    try:
        if password:
            if len(password) < 8:
                return error(
                    "Dữ liệu người dùng chưa hợp lệ.",
                    422,
                    {"password": "Mật khẩu mới phải có ít nhất 8 ký tự."},
                )
            database.execute(
                """
                UPDATE users SET username = ?, full_name = ?, email = ?, phone = ?,
                    role = ?, role_id = ?, status = ?, avatar_initials = ?, password_hash = ?,
                    updated_at = CURRENT_TIMESTAMP WHERE id = ?
                """,
                (
                    username,
                    full_name,
                    email,
                    phone,
                    role,
                    role_id,
                    status,
                    initials(full_name),
                    generate_password_hash(password),
                    user_id,
                ),
            )
        else:
            database.execute(
                """
                UPDATE users SET username = ?, full_name = ?, email = ?, phone = ?,
                    role = ?, role_id = ?, status = ?, avatar_initials = ?,
                    updated_at = CURRENT_TIMESTAMP WHERE id = ?
                """,
                (
                    username,
                    full_name,
                    email,
                    phone,
                    role,
                    role_id,
                    status,
                    initials(full_name),
                    user_id,
                ),
            )
        audit(
            "UPDATE",
            "user",
            user_id,
            {"username": username, "role": role, "status": status},
            g.user["id"],
            request.remote_addr,
        )
        database.commit()
    except IntegrityError:
        return error("Tên đăng nhập hoặc email đã tồn tại.", 409)
    return jsonify(ok=True, message="Đã cập nhật người dùng.")


@bp.delete("/users/<int:user_id>")
@roles_required("admin")
@csrf_required
def user_delete(user_id):
    if user_id == g.user["id"]:
        return error("Bạn không thể xóa tài khoản đang đăng nhập.", 422)
    database = get_db()
    user = database.execute(
        "SELECT id, username FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    if user is None:
        return error("Không tìm thấy người dùng.", 404)
    activity_count = database.execute(
        USER_ACTIVITY_COUNT_SQL,
        (user_id, user_id, user_id, user_id, user_id, user_id),
    ).fetchone()[0]
    if activity_count:
        return error("Tài khoản đã có lịch sử nghiệp vụ; hãy chuyển sang trạng thái khóa.", 409)
    database.execute("DELETE FROM users WHERE id = ?", (user_id,))
    audit(
        "DELETE",
        "user",
        user_id,
        {"username": user["username"]},
        g.user["id"],
        request.remote_addr,
    )
    database.commit()
    return jsonify(ok=True, message="Đã xóa người dùng.")


@bp.put("/profile")
@login_required
@csrf_required
def profile_update():
    data = json_object()
    full_name = text(data.get("full_name"))
    email = text(data.get("email")).lower()
    phone = text(data.get("phone"))
    errors = {}
    if not 2 <= len(full_name) <= 80:
        errors["full_name"] = "Họ tên phải có từ 2 đến 80 ký tự."
    if not EMAIL_RE.fullmatch(email):
        errors["email"] = "Email không đúng định dạng."
    if len(phone) > 20:
        errors["phone"] = "Số điện thoại không được vượt quá 20 ký tự."
    if errors:
        return error("Thông tin hồ sơ chưa hợp lệ.", 422, errors)
    database = get_db()
    try:
        database.execute(
            """
            UPDATE users SET full_name = ?, email = ?, phone = ?, avatar_initials = ?,
                             updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (full_name, email, phone, initials(full_name), g.user["id"]),
        )
        audit(
            "UPDATE_PROFILE",
            "user",
            g.user["id"],
            {"full_name": full_name, "email": email},
            g.user["id"],
            request.remote_addr,
        )
        database.commit()
    except IntegrityError:
        return error("Email này đang được tài khoản khác sử dụng.", 409)
    return jsonify(ok=True, message="Đã cập nhật hồ sơ.")


@bp.put("/profile/password")
@login_required
@csrf_required
def password_update():
    data = json_object()
    current_password = str(data.get("current_password", ""))
    new_password = str(data.get("new_password", ""))
    confirm_password = str(data.get("confirm_password", ""))
    errors = {}
    if not check_password_hash(g.user["password_hash"], current_password):
        errors["current_password"] = "Mật khẩu hiện tại không đúng."
    if len(new_password) < 8 or not re.search(r"[A-Za-z]", new_password) or not re.search(r"\d", new_password):
        errors["new_password"] = "Mật khẩu mới cần ít nhất 8 ký tự, gồm chữ và số."
    if new_password != confirm_password:
        errors["confirm_password"] = "Xác nhận mật khẩu không khớp."
    if current_password and current_password == new_password:
        errors["new_password"] = "Mật khẩu mới phải khác mật khẩu hiện tại."
    if errors:
        return error("Không thể đổi mật khẩu.", 422, errors)
    database = get_db()
    database.execute(
        "UPDATE users SET password_hash = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (generate_password_hash(new_password), g.user["id"]),
    )
    audit(
        "CHANGE_PASSWORD",
        "user",
        g.user["id"],
        user_id=g.user["id"],
        ip_address=request.remote_addr,
    )
    database.commit()
    return jsonify(ok=True, message="Đổi mật khẩu thành công.")


@bp.get("/audit-logs")
@roles_required("admin")
def audit_logs():
    page = max(to_int(request.args.get("page"), 1), 1)
    per_page = 15
    database = get_db()
    total = database.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0]
    rows = database.execute(
        """
        SELECT a.*, u.full_name, u.username
        FROM audit_logs a LEFT JOIN users u ON u.id = a.user_id
        ORDER BY a.id DESC LIMIT ? OFFSET ?
        """,
        (per_page, (page - 1) * per_page),
    ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        try:
            item["details"] = json.loads(item["details"])
        except json.JSONDecodeError:
            item["details"] = {}
        items.append(item)
    return jsonify(
        ok=True,
        items=items,
        pagination={
            "page": page,
            "per_page": per_page,
            "total": total,
            "pages": max((total + per_page - 1) // per_page, 1),
        },
    )


# --- Extended WMS modules -------------------------------------------------

def _list_rows(sql, params=()):
    return [dict(row) for row in get_db().execute(sql, params).fetchall()]


def _active_lookups():
    database = get_db()
    return {
        "products": _list_rows(
            """SELECT i.id, i.sku, i.name, i.unit, i.quantity, i.barcode,
                      i.warehouse_id, w.name AS warehouse_name
               FROM inventory i JOIN warehouses w ON w.id=i.warehouse_id
               WHERE i.status='active' ORDER BY i.name"""
        ),
        "warehouses": _list_rows(
            "SELECT id, code, name FROM warehouses WHERE status='active' ORDER BY name"
        ),
        "units": _list_rows(
            """SELECT id, code, name, allow_break_pack
               FROM units WHERE status='active' ORDER BY name"""
        ),
    }


@bp.get("/products")
@login_required
def product_list():
    search = text(request.args.get("search"))
    category_id = to_int(request.args.get("category_id"))
    warehouse_id = to_int(request.args.get("warehouse_id"))
    params, clauses = [], []
    if search:
        clauses.append("(i.sku LIKE ? OR i.name LIKE ? OR COALESCE(i.barcode,'') LIKE ?)")
        params = [f"%{search}%"] * 3
    if category_id:
        clauses.append("i.category_id=?")
        params.append(category_id)
    if warehouse_id:
        clauses.append("i.warehouse_id=?")
        params.append(warehouse_id)
    clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = _list_rows(
        f"""SELECT i.*, c.name AS category_name, w.name AS warehouse_name
            FROM inventory i JOIN categories c ON c.id=i.category_id
            JOIN warehouses w ON w.id=i.warehouse_id {clause}
            ORDER BY i.id DESC""",
        params,
    )
    return jsonify(ok=True, items=rows)


@bp.post("/products")
@roles_required("admin", "manager", "cs")
@csrf_required
def product_create():
    data = json_object()
    database = get_db()
    sku, name, unit = text(data.get("sku")).upper(), text(data.get("name")), text(data.get("unit"))
    category_id = to_int(data.get("category_id"))
    warehouse_id = to_int(data.get("warehouse_id")) or default_warehouse_id(database)
    unit_id = to_int(data.get("unit_id"))
    if unit_id:
        unit_row = database.execute(
            "SELECT id,name FROM units WHERE id=? AND status='active'", (unit_id,)
        ).fetchone()
    else:
        unit_row = database.execute(
            """SELECT id,name FROM units WHERE status='active'
               AND (LOWER(code)=LOWER(?) OR LOWER(name)=LOWER(?))""",
            (unit, unit),
        ).fetchone()
    if unit_row:
        unit_id, unit = unit_row["id"], unit_row["name"]
    errors = {}
    if not CODE_RE.fullmatch(sku):
        errors["sku"] = "Mã SKU gồm 2–20 chữ, số, gạch ngang hoặc gạch dưới."
    if len(name) < 2:
        errors["name"] = "Tên hàng hóa phải có ít nhất 2 ký tự."
    if not unit_row:
        errors["unit"] = "Vui lòng chọn đơn vị tính đang hoạt động."
    if not category_id:
        errors["category_id"] = "Vui lòng chọn danh mục."
    if not warehouse_id:
        errors["warehouse_id"] = "Vui lòng chọn kho."
    status = text(data.get("status")) or "active"
    if status not in {"active", "inactive"}:
        errors["status"] = "Trạng thái không hợp lệ."
    if errors:
        return error("Dữ liệu hàng hóa chưa hợp lệ.", 422, errors)
    try:
        cursor = database.execute(
            """INSERT INTO inventory
               (sku, barcode, name, category_id, warehouse_id, unit, unit_id, min_quantity,
                location, description, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                sku, text(data.get("barcode")) or None, name, category_id, warehouse_id,
                unit, unit_id,
                to_quantity(data.get("min_quantity", 0), allow_zero=True) or 0,
                "", text(data.get("description")),
                status,
            ),
        )
        audit("CREATE", "product", cursor.lastrowid, {"sku": sku}, g.user["id"], request.remote_addr)
        database.commit()
    except IntegrityError:
        return error("Mã SKU hoặc barcode đã tồn tại.", 409)
    return jsonify(ok=True, message="Đã thêm hàng hóa.", id=cursor.lastrowid), 201


def _partner_list(table):
    search = text(request.args.get("search"))
    params, clause = [], ""
    if search:
        clause = "WHERE code LIKE ? OR name LIKE ? OR email LIKE ?"
        params = [f"%{search}%"] * 3
    return jsonify(ok=True, items=_list_rows(
        f"SELECT * FROM {table} {clause} ORDER BY id DESC", params
    ))


@bp.get("/customers")
@login_required
def customer_list():
    return _partner_list("customers")


@bp.get("/suppliers")
@login_required
def supplier_list():
    return _partner_list("suppliers")


def _create_partner(table):
    data = json_object()
    code, name = text(data.get("code")).upper(), text(data.get("name"))
    email = text(data.get("email")).lower()
    status = text(data.get("status")) or "active"
    errors = {}
    if not CODE_RE.fullmatch(code):
        errors["code"] = "Mã đối tác không hợp lệ."
    if len(name) < 2:
        errors["name"] = "Tên đối tác phải có ít nhất 2 ký tự."
    if email and not EMAIL_RE.fullmatch(email):
        errors["email"] = "Email không đúng định dạng."
    if status not in {"active", "inactive"}:
        errors["status"] = "Trạng thái không hợp lệ."
    contract_emails = [
        value.strip().lower()
        for value in text(data.get("contract_emails")).split(",")
        if value.strip()
    ]
    if table == "customers":
        if not contract_emails:
            errors["contract_emails"] = "Cần ít nhất một email hợp đồng."
        elif any(not EMAIL_RE.fullmatch(value) for value in contract_emails):
            errors["contract_emails"] = "Danh sách email hợp đồng chưa hợp lệ."
    if errors:
        return error("Dữ liệu đối tác chưa hợp lệ.", 422, errors)
    database = get_db()
    try:
        if table == "customers":
            cursor = database.execute(
                """INSERT INTO customers
                   (code,name,email,phone,contract_emails,status) VALUES (?,?,?,?,?,?)""",
                (code, name, email, text(data.get("phone")),
                 ",".join(contract_emails), status),
            )
            database.executemany(
                """INSERT INTO customer_contract_emails
                   (customer_id,email,normalized_email,status)
                   VALUES (?,?,?,'active')""",
                [
                    (cursor.lastrowid, value, value.casefold())
                    for value in dict.fromkeys(contract_emails)
                ],
            )
        else:
            cursor = database.execute(
                """INSERT INTO suppliers
                   (code,name,email,phone,address,status) VALUES (?,?,?,?,?,?)""",
                (code, name, email, text(data.get("phone")),
                 text(data.get("address")), status),
            )
        audit("CREATE", table[:-1], cursor.lastrowid, {"code": code}, g.user["id"], request.remote_addr)
        database.commit()
    except IntegrityError:
        return error("Mã đối tác đã tồn tại.", 409)
    return jsonify(ok=True, message="Đã thêm đối tác.", id=cursor.lastrowid), 201


@bp.post("/customers")
@roles_required("admin", "manager", "cs")
@csrf_required
def customer_create():
    return _create_partner("customers")


@bp.post("/suppliers")
@roles_required("admin", "manager", "cs")
@csrf_required
def supplier_create():
    return _create_partner("suppliers")


@bp.put("/products/<int:product_id>")
@roles_required("admin", "manager", "cs")
@csrf_required
def product_update(product_id):
    data = json_object()
    database = get_db()
    current = database.execute(
        "SELECT * FROM inventory WHERE id=?", (product_id,)
    ).fetchone()
    if current is None:
        return error("Không tìm thấy hàng hóa.", 404)
    sku = text(data.get("sku", current["sku"])).upper()
    name = text(data.get("name", current["name"]))
    unit = text(data.get("unit", current["unit"]))
    unit_id = to_int(data.get("unit_id"), current["unit_id"])
    unit_row = database.execute(
        """SELECT id,name FROM units WHERE status='active' AND
           (id=? OR LOWER(code)=LOWER(?) OR LOWER(name)=LOWER(?))""",
        (unit_id, unit, unit),
    ).fetchone()
    if unit_row:
        unit_id, unit = unit_row["id"], unit_row["name"]
    category_id = to_int(data.get("category_id"), current["category_id"])
    warehouse_id = to_int(data.get("warehouse_id"), current["warehouse_id"])
    status = text(data.get("status", current["status"]))
    errors = {}
    if not CODE_RE.fullmatch(sku):
        errors["sku"] = "Mã SKU không hợp lệ."
    if len(name) < 2:
        errors["name"] = "Tên hàng hóa phải có ít nhất 2 ký tự."
    if not unit_row:
        errors["unit"] = "Đơn vị tính phải thuộc danh mục đang hoạt động."
    if status not in {"active", "inactive"}:
        errors["status"] = "Trạng thái không hợp lệ."
    if database.execute("SELECT id FROM categories WHERE id=?", (category_id,)).fetchone() is None:
        errors["category_id"] = "Danh mục không tồn tại."
    if database.execute("SELECT id FROM warehouses WHERE id=?", (warehouse_id,)).fetchone() is None:
        errors["warehouse_id"] = "Kho không tồn tại."
    # Moving a product with stock/history would invalidate lot ownership.
    if warehouse_id != current["warehouse_id"] and current["quantity"] != 0:
        errors["warehouse_id"] = "Không thể chuyển kho khi hàng hóa còn tồn."
    if errors:
        return error("Dữ liệu hàng hóa chưa hợp lệ.", 422, errors)
    try:
        database.execute(
            """UPDATE inventory SET sku=?,barcode=?,name=?,category_id=?,warehouse_id=?,
               unit=?,unit_id=?,min_quantity=?,location=?,description=?,status=?,
               updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (
                sku, text(data.get("barcode", current["barcode"])) or None, name,
                category_id, warehouse_id, unit, unit_id,
                to_quantity(
                    data.get("min_quantity", current["min_quantity"]),
                    allow_zero=True,
                ),
                "",
                text(data.get("description", current["description"])),
                status, product_id,
            ),
        )
        audit("UPDATE", "product", product_id, {"sku": sku}, g.user["id"], request.remote_addr)
        database.commit()
    except IntegrityError:
        database.rollback()
        return error("Mã SKU hoặc barcode đã tồn tại.", 409)
    return jsonify(ok=True, message="Đã cập nhật hàng hóa.", id=product_id)


def _update_partner(table, partner_id):
    data = json_object()
    database = get_db()
    current = database.execute(f"SELECT * FROM {table} WHERE id=?", (partner_id,)).fetchone()
    if current is None:
        return error("Không tìm thấy đối tác.", 404)
    code = text(data.get("code", current["code"])).upper()
    name = text(data.get("name", current["name"]))
    email = text(data.get("email", current["email"])).lower()
    status = text(data.get("status", current["status"]))
    errors = {}
    if not CODE_RE.fullmatch(code):
        errors["code"] = "Mã đối tác không hợp lệ."
    if len(name) < 2:
        errors["name"] = "Tên đối tác phải có ít nhất 2 ký tự."
    if email and not EMAIL_RE.fullmatch(email):
        errors["email"] = "Email không đúng định dạng."
    if status not in {"active", "inactive"}:
        errors["status"] = "Trạng thái không hợp lệ."
    contract_emails = []
    if table == "customers":
        contract_emails = [
            value.strip().casefold()
            for value in text(
                data.get("contract_emails", current["contract_emails"])
            ).split(",")
            if value.strip()
        ]
        if not contract_emails or any(not EMAIL_RE.fullmatch(value) for value in contract_emails):
            errors["contract_emails"] = "Cần ít nhất một email hợp đồng hợp lệ."
    if errors:
        return error("Dữ liệu đối tác chưa hợp lệ.", 422, errors)
    try:
        if table == "customers":
            database.execute(
                """UPDATE customers SET code=?,name=?,email=?,phone=?,
                   contract_emails=?,status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (
                    code, name, email, text(data.get("phone", current["phone"])),
                    ",".join(dict.fromkeys(contract_emails)), status, partner_id,
                ),
            )
            database.execute(
                "DELETE FROM customer_contract_emails WHERE customer_id=?", (partner_id,)
            )
            database.executemany(
                """INSERT INTO customer_contract_emails
                   (customer_id,email,normalized_email,status)
                   VALUES (?,?,?,'active')""",
                [(partner_id, value, value) for value in dict.fromkeys(contract_emails)],
            )
        else:
            database.execute(
                """UPDATE suppliers SET code=?,name=?,email=?,phone=?,address=?,
                   status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (
                    code, name, email, text(data.get("phone", current["phone"])),
                    text(data.get("address", current["address"])), status, partner_id,
                ),
            )
        audit("UPDATE", table[:-1], partner_id, {"code": code}, g.user["id"], request.remote_addr)
        database.commit()
    except IntegrityError:
        database.rollback()
        return error("Mã đối tác hoặc email hợp đồng đã tồn tại.", 409)
    return jsonify(ok=True, message="Đã cập nhật đối tác.", id=partner_id)


@bp.put("/customers/<int:partner_id>")
@roles_required("admin", "manager", "cs")
@csrf_required
def customer_update(partner_id):
    return _update_partner("customers", partner_id)


@bp.put("/suppliers/<int:partner_id>")
@roles_required("admin", "manager", "cs")
@csrf_required
def supplier_update(partner_id):
    return _update_partner("suppliers", partner_id)


def _warehouse_values(data, current=None):
    current = current or {}
    code = text(data.get("code", current.get("code", ""))).upper()
    name = text(data.get("name", current.get("name", "")))
    address = text(data.get("address", current.get("address", "")))
    status = text(data.get("status", current.get("status", "active")))
    errors = {}
    if not CODE_RE.fullmatch(code):
        errors["code"] = "Mã kho không hợp lệ."
    if len(name) < 2:
        errors["name"] = "Tên kho phải có ít nhất 2 ký tự."
    if status not in {"active", "inactive"}:
        errors["status"] = "Trạng thái không hợp lệ."
    return (code, name, address, status), errors


@bp.post("/warehouses")
@roles_required("admin")
@csrf_required
def warehouse_create():
    values, errors = _warehouse_values(json_object())
    if errors:
        return error("Dữ liệu kho chưa hợp lệ.", 422, errors)
    database = get_db()
    try:
        cursor = database.execute(
            "INSERT INTO warehouses (code,name,address,status) VALUES (?,?,?,?)", values
        )
        audit("CREATE", "warehouse", cursor.lastrowid, {"code": values[0]}, g.user["id"], request.remote_addr)
        database.commit()
    except IntegrityError:
        database.rollback()
        return error("Mã kho đã tồn tại.", 409)
    return jsonify(ok=True, message="Đã thêm kho.", id=cursor.lastrowid), 201


@bp.put("/warehouses/<int:warehouse_id>")
@roles_required("admin")
@csrf_required
def warehouse_update(warehouse_id):
    database = get_db()
    row = database.execute("SELECT * FROM warehouses WHERE id=?", (warehouse_id,)).fetchone()
    if row is None:
        return error("Không tìm thấy kho.", 404)
    values, errors = _warehouse_values(json_object(), dict(row))
    if errors:
        return error("Dữ liệu kho chưa hợp lệ.", 422, errors)
    try:
        database.execute(
            "UPDATE warehouses SET code=?,name=?,address=?,status=? WHERE id=?",
            (*values, warehouse_id),
        )
        audit("UPDATE", "warehouse", warehouse_id, {"code": values[0]}, g.user["id"], request.remote_addr)
        database.commit()
    except IntegrityError:
        database.rollback()
        return error("Mã kho đã tồn tại.", 409)
    return jsonify(ok=True, message="Đã cập nhật kho.", id=warehouse_id)


@bp.get("/warehouses")
@login_required
def warehouse_list():
    rows = _list_rows(
        """SELECT w.*,
                  (SELECT COUNT(*) FROM inventory i WHERE i.warehouse_id=w.id) AS product_count,
                  COALESCE((SELECT SUM(i.quantity) FROM inventory i WHERE i.warehouse_id=w.id),0) AS total_quantity
           FROM warehouses w ORDER BY w.name"""
    )
    return jsonify(ok=True, items=rows)


@bp.get("/operations/lookups")
@login_required
def operation_lookups():
    payload = _active_lookups()
    payload["customers"] = _list_rows(
        "SELECT id,code,name,email,contract_emails FROM customers WHERE status='active' ORDER BY name"
    )
    payload["suppliers"] = _list_rows(
        "SELECT id,code,name,email FROM suppliers WHERE status='active' ORDER BY name"
    )
    return jsonify(ok=True, **payload)


def _receipt_payload(receipt_id):
    row = get_db().execute(
        """SELECT r.*, w.name AS warehouse_name, u.full_name AS created_by_name,
                  confirmer.full_name AS confirmed_by_name
           FROM receipts r JOIN warehouses w ON w.id=r.warehouse_id
           JOIN users u ON u.id=r.created_by
           LEFT JOIN users confirmer ON confirmer.id=r.confirmed_by
           WHERE r.id=?""", (receipt_id,)
    ).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["items"] = _list_rows(
        """SELECT ri.*, i.sku, i.name, i.unit, i.quantity AS available_quantity
           FROM receipt_items ri JOIN inventory i ON i.id=ri.inventory_id
           WHERE ri.receipt_id=? ORDER BY ri.id""", (receipt_id,)
    )
    return result


@bp.get("/inbound-receipts")
@login_required
def inbound_list():
    return _receipt_list("inbound")


@bp.get("/outbound-receipts")
@login_required
def outbound_list():
    return _receipt_list("outbound")


def _receipt_list(receipt_type):
    search, status = text(request.args.get("search")), text(request.args.get("status"))
    clauses, params = ["r.receipt_type=?"], [receipt_type]
    if search:
        clauses.append("(r.code LIKE ? OR r.partner_name LIKE ?)")
        params.extend([f"%{search}%"] * 2)
    if status:
        clauses.append("r.status=?")
        params.append(status)
    rows = _list_rows(
        f"""SELECT r.*, w.name AS warehouse_name,
                   (SELECT COUNT(*) FROM receipt_items ri WHERE ri.receipt_id=r.id) AS item_count,
                   COALESCE((SELECT SUM(ri.quantity) FROM receipt_items ri WHERE ri.receipt_id=r.id),0) AS total_quantity
            FROM receipts r JOIN warehouses w ON w.id=r.warehouse_id
            WHERE {' AND '.join(clauses)} ORDER BY r.id DESC""",
        params,
    )
    stats = {key: 0 for key in ("draft", "pending", "picking", "completed", "cancelled")}
    for row in rows:
        stats[row["status"]] = stats.get(row["status"], 0) + 1
    return jsonify(ok=True, items=rows, stats=stats)


def _receipt_create(receipt_type):
    data = json_object()
    database = get_db()
    code = text(data.get("code")).upper()
    partner_id = to_int(data.get("partner_id"))
    warehouse_id = to_int(data.get("warehouse_id")) or default_warehouse_id(database)
    items = data.get("items") if isinstance(data.get("items"), list) else []
    status = text(data.get("status")) or "draft"
    errors = {}
    if not code:
        errors["code"] = "Vui lòng nhập mã phiếu."
    if not partner_id:
        errors["partner_id"] = "Vui lòng chọn đối tác."
    if not warehouse_id:
        errors["warehouse_id"] = "Vui lòng chọn kho."
    if not items:
        errors["items"] = "Phiếu phải có ít nhất một dòng hàng."
    if status not in {"draft", "pending"}:
        errors["status"] = "Trạng thái phiếu không hợp lệ."
    warehouse = database.execute(
        "SELECT id FROM warehouses WHERE id=? AND status='active'", (warehouse_id,)
    ).fetchone()
    if warehouse is None and warehouse_id:
        errors["warehouse_id"] = "Kho không còn hoạt động."
    table = "suppliers" if receipt_type == "inbound" else "customers"
    partner = database.execute(f"SELECT * FROM {table} WHERE id=? AND status='active'", (partner_id,)).fetchone()
    if partner is None and partner_id:
        errors["partner_id"] = "Đối tác không còn hoạt động."
    request_email = text(data.get("request_email")).lower()
    if receipt_type == "outbound" and partner is not None:
        allowed = set(
            orm.session.scalars(
                orm.select(CustomerContractEmail.normalized_email).where(
                    CustomerContractEmail.customer_id == partner_id,
                    CustomerContractEmail.status == "active",
                )
            )
        )
        allowed.update(
            email.strip().casefold()
            for email in partner["contract_emails"].split(",")
            if email.strip()
        )
        if not EMAIL_RE.fullmatch(request_email) or request_email.casefold() not in allowed:
            errors["request_email"] = "Email không thuộc danh sách email hợp đồng."
    normalized = []
    requested_by_product = {}
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            errors["items"] = f"Dòng {index + 1} chưa hợp lệ."
            break
        inventory_id, quantity = to_int(item.get("inventory_id")), to_quantity(item.get("quantity"))
        expiry = text(item.get("expiry_date"))
        if expiry:
            try:
                date.fromisoformat(expiry)
            except ValueError:
                errors["items"] = f"Hạn dùng ở dòng {index + 1} không đúng định dạng YYYY-MM-DD."
                break
        stock = database.execute(
            """SELECT id,quantity FROM inventory
               WHERE id=? AND warehouse_id=? AND status='active'""",
            (inventory_id, warehouse_id),
        ).fetchone()
        if stock is None or quantity is None:
            errors["items"] = (
                f"Dòng {index + 1} chưa hợp lệ hoặc hàng hóa không thuộc kho đã chọn."
            )
            break
        requested_by_product[inventory_id] = (
            requested_by_product.get(inventory_id, 0) + quantity
        )
        if (
            receipt_type == "outbound"
            and requested_by_product[inventory_id]
            > available_quantity(inventory_id, warehouse_id)
        ):
            errors["items"] = f"Dòng {index + 1} vượt tồn khả dụng."
            break
        normalized.append((inventory_id, quantity, text(item.get("pallet_id")),
                           text(item.get("barcode")), expiry or None))
    if errors:
        return error("Dữ liệu phiếu chưa hợp lệ.", 422, errors)
    try:
        database.execute("BEGIN")
        cursor = database.execute(
            """INSERT INTO receipts
               (code,receipt_type,partner_id,customer_id,supplier_id,partner_name,warehouse_id,request_email,
                container_no,seal_no,status,note,created_by)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (code, receipt_type, partner_id,
             partner_id if receipt_type == "outbound" else None,
             partner_id if receipt_type == "inbound" else None,
             partner["name"], warehouse_id, request_email,
             text(data.get("container_no")), text(data.get("seal_no")),
             status, text(data.get("note")), g.user["id"]),
        )
        database.executemany(
            """INSERT INTO receipt_items
               (receipt_id,inventory_id,quantity,accepted_quantity,pallet_id,barcode,expiry_date)
               VALUES (?,?,?,?,?,?,?)""",
            [(cursor.lastrowid, product_id, qty, 0, pallet, barcode, expiry)
             for product_id, qty, pallet, barcode, expiry in normalized],
        )
        audit("CREATE", f"{receipt_type}_receipt", cursor.lastrowid, {"code": code}, g.user["id"], request.remote_addr)
        database.commit()
    except IntegrityError:
        database.rollback()
        return error("Mã phiếu hoặc dòng hàng đã tồn tại.", 409)
    return jsonify(ok=True, message="Đã lưu phiếu.", id=cursor.lastrowid), 201


@bp.post("/inbound-receipts")
@roles_required("admin", "manager", "cs")
@csrf_required
def inbound_create():
    return _receipt_create("inbound")


@bp.post("/outbound-receipts")
@roles_required("admin", "manager", "cs")
@csrf_required
def outbound_create():
    return _receipt_create("outbound")


def _receipt_update(receipt_id, receipt_type):
    data = json_object()
    database = get_db()
    receipt = database.execute(
        "SELECT * FROM receipts WHERE id=? AND receipt_type=?",
        (receipt_id, receipt_type),
    ).fetchone()
    if receipt is None:
        return error("Không tìm thấy phiếu.", 404)
    if receipt["status"] != "draft":
        return error("Chỉ phiếu nháp mới được chỉnh sửa.", 409)
    code = text(data.get("code", receipt["code"])).upper()
    partner_id = to_int(data.get("partner_id"), receipt["partner_id"])
    warehouse_id = to_int(data.get("warehouse_id"), receipt["warehouse_id"])
    new_status = text(data.get("status", "draft"))
    submitted = data.get("items")
    if not isinstance(submitted, list):
        current_items = database.execute(
            "SELECT * FROM receipt_items WHERE receipt_id=? ORDER BY id", (receipt_id,)
        ).fetchall()
        submitted = [dict(item) for item in current_items]
    errors = {}
    if not code:
        errors["code"] = "Mã phiếu là bắt buộc."
    if new_status not in {"draft", "pending"}:
        errors["status"] = "Trạng thái không hợp lệ."
    partner_table = "suppliers" if receipt_type == "inbound" else "customers"
    partner = database.execute(
        f"SELECT * FROM {partner_table} WHERE id=? AND status='active'", (partner_id,)
    ).fetchone()
    if partner is None:
        errors["partner_id"] = "Đối tác không còn hoạt động."
    if database.execute(
        "SELECT id FROM warehouses WHERE id=? AND status='active'", (warehouse_id,)
    ).fetchone() is None:
        errors["warehouse_id"] = "Kho không còn hoạt động."
    request_email = text(data.get("request_email", receipt["request_email"])).casefold()
    if receipt_type == "outbound" and partner is not None:
        allowed = {
            value.strip().casefold()
            for value in partner["contract_emails"].split(",") if value.strip()
        }
        allowed.update(
            orm.session.scalars(
                orm.select(CustomerContractEmail.normalized_email).where(
                    CustomerContractEmail.customer_id == partner_id,
                    CustomerContractEmail.status == "active",
                )
            )
        )
        if not EMAIL_RE.fullmatch(request_email) or request_email not in allowed:
            errors["request_email"] = "Email không thuộc danh sách email hợp đồng."
    normalized, totals = [], {}
    if not submitted:
        errors["items"] = "Phiếu phải có ít nhất một dòng hàng."
    for index, item in enumerate(submitted):
        if not isinstance(item, dict):
            errors["items"] = f"Dòng {index + 1} không hợp lệ."
            break
        product_id = to_int(item.get("inventory_id"))
        amount = to_quantity(item.get("quantity"))
        expiry = text(item.get("expiry_date"))
        if expiry:
            try:
                date.fromisoformat(expiry)
            except ValueError:
                errors["items"] = f"Hạn dùng ở dòng {index + 1} không hợp lệ."
                break
        product = database.execute(
            """SELECT id,quantity FROM inventory
               WHERE id=? AND warehouse_id=? AND status='active'""",
            (product_id, warehouse_id),
        ).fetchone()
        if product is None or amount is None:
            errors["items"] = f"Dòng {index + 1} không hợp lệ hoặc sai kho."
            break
        totals[product_id] = totals.get(product_id, 0) + amount
        if (
            receipt_type == "outbound"
            and totals[product_id] > available_quantity(product_id, warehouse_id)
        ):
            errors["items"] = f"Dòng {index + 1} vượt tồn khả dụng."
            break
        normalized.append(
            (
                product_id, amount, 0, text(item.get("pallet_id")),
                text(item.get("barcode")), expiry or None,
            )
        )
    if errors:
        return error("Dữ liệu phiếu chưa hợp lệ.", 422, errors)
    try:
        updated = database.execute(
            """UPDATE receipts SET code=?,partner_id=?,customer_id=?,supplier_id=?,
               partner_name=?,warehouse_id=?,
               request_email=?,container_no=?,seal_no=?,status=?,note=?,
               updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='draft'""",
            (
                code, partner_id,
                partner_id if receipt_type == "outbound" else None,
                partner_id if receipt_type == "inbound" else None,
                partner["name"], warehouse_id, request_email,
                text(data.get("container_no", receipt["container_no"])),
                text(data.get("seal_no", receipt["seal_no"])), new_status,
                text(data.get("note", receipt["note"])), receipt_id,
            ),
        )
        if updated.rowcount != 1:
            database.rollback()
            return error("Phiếu vừa thay đổi trạng thái; không thể chỉnh sửa.", 409)
        database.execute("DELETE FROM receipt_items WHERE receipt_id=?", (receipt_id,))
        database.executemany(
            """INSERT INTO receipt_items
               (receipt_id,inventory_id,quantity,accepted_quantity,pallet_id,barcode,expiry_date)
               VALUES (?,?,?,?,?,?,?)""",
            [(receipt_id, *item) for item in normalized],
        )
        audit("UPDATE", f"{receipt_type}_receipt", receipt_id, {"code": code}, g.user["id"], request.remote_addr)
        database.commit()
    except IntegrityError:
        database.rollback()
        return error("Mã phiếu hoặc dòng hàng đã tồn tại.", 409)
    return jsonify(ok=True, message="Đã cập nhật phiếu.", id=receipt_id)


@bp.put("/inbound-receipts/<int:receipt_id>")
@roles_required("admin", "manager", "cs")
@csrf_required
def inbound_update(receipt_id):
    return _receipt_update(receipt_id, "inbound")


@bp.put("/outbound-receipts/<int:receipt_id>")
@roles_required("admin", "manager", "cs")
@csrf_required
def outbound_update(receipt_id):
    return _receipt_update(receipt_id, "outbound")


@bp.get("/inbound-receipts/<int:receipt_id>")
@login_required
def inbound_detail(receipt_id):
    return _receipt_detail(receipt_id, "inbound")


@bp.get("/outbound-receipts/<int:receipt_id>")
@login_required
def outbound_detail(receipt_id):
    return _receipt_detail(receipt_id, "outbound")


def _receipt_detail(receipt_id, expected_type):
    receipt = _receipt_payload(receipt_id)
    if receipt is None or receipt["receipt_type"] != expected_type:
        return error("Không tìm thấy phiếu.", 404)
    return jsonify(ok=True, item=receipt)


def _confirm_receipt(receipt_id, expected_type):
    try:
        receipt, already_completed = confirm_receipt_service(
            receipt_id, expected_type, g.user["id"]
        )
        if already_completed:
            return jsonify(
                ok=True,
                message="Phiếu đã được xác nhận trước đó; tồn kho không thay đổi.",
                already_completed=True,
            )
        audit(
            "CONFIRM", f"{expected_type}_receipt", receipt_id,
            {"code": receipt.code}, g.user["id"], request.remote_addr,
        )
        orm.session.commit()
    except LookupError:
        orm.session.rollback()
        return error("Không tìm thấy phiếu.", 404)
    except DomainError as exc:
        orm.session.rollback()
        return error(str(exc), 409)
    except IntegrityError:
        orm.session.rollback()
        return error("Phiếu đã được ghi nhận hoặc pallet/barcode bị trùng.", 409)
    return jsonify(ok=True, message="Đã xác nhận phiếu và cập nhật tồn kho.")


@bp.post("/inbound-receipts/<int:receipt_id>/confirm")
@roles_required("admin", "manager", "staff", "warehouse")
@csrf_required
def inbound_confirm(receipt_id):
    return _confirm_receipt(receipt_id, "inbound")


@bp.post("/outbound-receipts/<int:receipt_id>/confirm")
@roles_required("admin", "manager", "staff", "warehouse")
@csrf_required
def outbound_confirm(receipt_id):
    return _confirm_receipt(receipt_id, "outbound")


@bp.post("/inbound-receipts/<int:receipt_id>/inspect")
@roles_required("admin", "manager", "staff", "warehouse")
@csrf_required
def inbound_inspect(receipt_id):
    data = json_object()
    submitted = data.get("items") if isinstance(data.get("items"), list) else []
    database = get_db()
    receipt = database.execute(
        "SELECT * FROM receipts WHERE id=? AND receipt_type='inbound'", (receipt_id,)
    ).fetchone()
    if receipt is None:
        return error("Không tìm thấy phiếu nhập.", 404)
    if receipt["status"] in {"completed", "cancelled", "rejected"}:
        return error("Phiếu đã khóa, không thể kiểm nhận.", 409)
    existing = {
        row["id"]: row
        for row in database.execute(
            "SELECT * FROM receipt_items WHERE receipt_id=?", (receipt_id,)
        ).fetchall()
    }
    normalized = []
    submitted_ids = set()
    for item in submitted:
        if not isinstance(item, dict):
            return error("Dòng kiểm nhận không hợp lệ.", 422, {"items": "Kiểm tra lại từng dòng hàng."})
        line_id = to_int(item.get("id"))
        accepted_value = item.get("accepted_quantity")
        try:
            accepted_decimal = Decimal(str(accepted_value)).quantize(Decimal("0.001"))
            accepted = (
                int(accepted_decimal)
                if accepted_decimal == accepted_decimal.to_integral_value()
                else float(accepted_decimal)
            )
        except (InvalidOperation, TypeError, ValueError):
            accepted_decimal, accepted = Decimal("-1"), None
        line = existing.get(line_id)
        if (
            line is None
            or line_id in submitted_ids
            or accepted is None
            or not accepted_decimal.is_finite()
            or accepted < 0
            or accepted > line["quantity"]
        ):
            return error("Số lượng thực nhận không hợp lệ.", 422, {"items": "Kiểm tra lại từng dòng hàng."})
        issue_note = text(item.get("issue_note"))
        if accepted < line["quantity"] and len(issue_note) < 3:
            return error(
                "Dòng có hàng thiếu hoặc hỏng phải ghi rõ lý do.",
                422,
                {"items": "Vui lòng nhập lý do cho số lượng bị từ chối."},
            )
        submitted_ids.add(line_id)
        normalized.append((accepted, line["quantity"] - accepted, issue_note, line_id))
    if submitted_ids != set(existing):
        return error("Cần kiểm nhận đầy đủ các dòng hàng.", 422, {"items": "Thiếu dòng kiểm nhận."})
    try:
        locked = database.execute(
            """UPDATE receipts SET status='pending',updated_at=CURRENT_TIMESTAMP
               WHERE id=? AND status IN ('draft','pending')""",
            (receipt_id,),
        )
        if locked.rowcount != 1:
            database.rollback()
            return error("Phiếu vừa được khóa; không thể kiểm nhận.", 409)
        database.executemany(
            """UPDATE receipt_items SET accepted_quantity=?, rejected_quantity=?,
               issue_note=? WHERE id=?""",
            normalized,
        )
        for accepted, rejected, issue_note, line_id in normalized:
            inspection = database.execute(
                """UPDATE inbound_inspections
                   SET accepted_quantity=?,rejected_quantity=?,issue_note=?,
                       inspected_by=?,updated_at=CURRENT_TIMESTAMP
                   WHERE receipt_item_id=?""",
                (accepted, rejected, issue_note, g.user["id"], line_id),
            )
            if inspection.rowcount == 0:
                database.execute(
                    """INSERT INTO inbound_inspections
                       (receipt_item_id,accepted_quantity,rejected_quantity,
                        issue_note,inspected_by)
                       VALUES (?,?,?,?,?)""",
                    (line_id, accepted, rejected, issue_note, g.user["id"]),
                )
        audit("INSPECT", "inbound_receipt", receipt_id, {}, g.user["id"], request.remote_addr)
        database.commit()
    except IntegrityError:
        database.rollback()
        return error("Không thể lưu kiểm nhận do dữ liệu đã thay đổi.", 409)
    return jsonify(ok=True, message="Đã lưu kết quả kiểm nhận.")


@bp.get("/outbound-receipts/<int:receipt_id>/check-stock")
@login_required
def outbound_check_stock(receipt_id):
    receipt = _receipt_payload(receipt_id)
    if receipt is None or receipt["receipt_type"] != "outbound":
        return error("Không tìm thấy phiếu xuất.", 404)
    lines, sufficient = [], True
    requested_by_product = {}
    for item in receipt["items"]:
        requested_by_product[item["inventory_id"]] = (
            requested_by_product.get(item["inventory_id"], 0) + item["quantity"]
        )
    for item in receipt["items"]:
        available = available_quantity(
            item["inventory_id"], receipt["warehouse_id"]
        )
        available_value = (
            int(available)
            if available == available.to_integral_value()
            else float(available)
        )
        requested = requested_by_product[item["inventory_id"]]
        ok = available >= requested
        sufficient = sufficient and ok
        lines.append({
            "inventory_id": item["inventory_id"],
            "sku": item["sku"],
            "name": item["name"],
            "requested": requested,
            "available": available_value,
            "sufficient": ok,
        })
    return jsonify(ok=True, sufficient=sufficient, items=lines,
                   message="Đủ tồn để xuất." if sufficient else "Một số mặt hàng không đủ tồn.")


@bp.get("/outbound-receipts/<int:receipt_id>/picking-list")
@login_required
def outbound_picking_list(receipt_id):
    try:
        receipt, items = build_picking_list(receipt_id)
    except LookupError:
        return error("Không tìm thấy phiếu xuất.", 404)
    except DomainError as exc:
        return error(str(exc), 409)
    warehouse = get_db().execute(
        "SELECT name FROM warehouses WHERE id=?", (receipt.warehouse_id,)
    ).fetchone()
    return jsonify(
        ok=True,
        receipt_code=receipt.code,
        warehouse_name=warehouse["name"] if warehouse else "",
        strategy="FEFO khi có hạn dùng, FIFO cho hàng còn lại",
        items=items,
    )


@bp.post("/outbound-receipts/<int:receipt_id>/start-picking")
@roles_required("admin", "manager", "staff", "warehouse")
@csrf_required
def outbound_start_picking(receipt_id):
    try:
        receipt, items = build_picking_list(receipt_id)
        if receipt.status == "picking":
            return jsonify(
                ok=True,
                message="Phiếu đã ở trạng thái đang lấy hàng.",
                already_picking=True,
                item_count=len(items),
            )
        if receipt.status != "pending":
            raise DomainError(
                "Chỉ phiếu chờ xuất mới có thể bắt đầu lấy hàng."
            )
        receipt.status = "picking"
        audit(
            "START_PICKING",
            "outbound_receipt",
            receipt_id,
            {"code": receipt.code, "item_count": len(items)},
            g.user["id"],
            request.remote_addr,
        )
        orm.session.commit()
    except LookupError:
        orm.session.rollback()
        return error("Không tìm thấy phiếu xuất.", 404)
    except DomainError as exc:
        orm.session.rollback()
        return error(str(exc), 409)
    return jsonify(
        ok=True,
        message="Đã chuyển phiếu sang trạng thái đang lấy hàng.",
        item_count=len(items),
    )


@bp.post("/outbound-receipts/<int:receipt_id>/reject")
@roles_required("admin", "manager", "staff", "warehouse")
@csrf_required
def outbound_reject(receipt_id):
    reason = text(json_object().get("reason"))
    if len(reason) < 3:
        return error(
            "Phải ghi rõ lý do từ chối phiếu xuất.",
            422,
            {"reason": "Lý do phải có ít nhất 3 ký tự."},
        )
    database = get_db()
    receipt = database.execute(
        "SELECT * FROM receipts WHERE id=? AND receipt_type='outbound'",
        (receipt_id,),
    ).fetchone()
    if receipt is None:
        return error("Không tìm thấy phiếu xuất.", 404)
    if receipt["status"] == "rejected":
        return jsonify(
            ok=True,
            message="Phiếu đã bị từ chối trước đó.",
            already_rejected=True,
        )
    if receipt["status"] not in {"pending", "picking"}:
        return error(
            "Chỉ phiếu chờ xuất hoặc đang lấy hàng mới có thể bị từ chối.",
            409,
        )
    updated = database.execute(
        """UPDATE receipts SET status='rejected',note=?,
           updated_at=CURRENT_TIMESTAMP
           WHERE id=? AND status IN ('pending','picking')""",
        (reason, receipt_id),
    )
    if updated.rowcount != 1:
        database.rollback()
        return error(
            "Trạng thái phiếu vừa thay đổi; vui lòng tải lại dữ liệu.",
            409,
        )
    audit(
        "REJECT",
        "outbound_receipt",
        receipt_id,
        {"code": receipt["code"], "reason": reason},
        g.user["id"],
        request.remote_addr,
    )
    database.commit()
    return jsonify(ok=True, message="Đã từ chối phiếu xuất.")


def _cancel_receipt(receipt_id, expected_type):
    database = get_db()
    receipt = database.execute(
        "SELECT * FROM receipts WHERE id=? AND receipt_type=?", (receipt_id, expected_type)
    ).fetchone()
    if receipt is None:
        return error("Không tìm thấy phiếu.", 404)
    if receipt["status"] == "completed":
        return error("Không thể hủy phiếu đã hoàn tất.", 409)
    if receipt["status"] == "cancelled":
        return jsonify(ok=True, message="Phiếu đã được hủy trước đó.", already_cancelled=True)
    updated = database.execute(
        """UPDATE receipts SET status='cancelled',updated_at=CURRENT_TIMESTAMP
           WHERE id=? AND status NOT IN ('completed','cancelled')""",
        (receipt_id,),
    )
    if updated.rowcount != 1:
        database.rollback()
        latest = database.execute("SELECT status FROM receipts WHERE id=?", (receipt_id,)).fetchone()
        if latest and latest["status"] == "cancelled":
            return jsonify(ok=True, message="Phiếu đã được hủy trước đó.", already_cancelled=True)
        return error("Phiếu vừa được xác nhận; không thể hủy.", 409)
    audit("CANCEL", f"{expected_type}_receipt", receipt_id, {"code": receipt["code"]}, g.user["id"], request.remote_addr)
    database.commit()
    return jsonify(ok=True, message="Đã hủy phiếu.")


@bp.post("/inbound-receipts/<int:receipt_id>/cancel")
@roles_required("admin", "manager", "cs")
@csrf_required
def inbound_cancel(receipt_id):
    return _cancel_receipt(receipt_id, "inbound")


@bp.post("/outbound-receipts/<int:receipt_id>/cancel")
@roles_required("admin", "manager", "cs")
@csrf_required
def outbound_cancel(receipt_id):
    return _cancel_receipt(receipt_id, "outbound")


@bp.get("/stocktakes")
@login_required
def stocktake_list():
    search = text(request.args.get("search"))
    clause, params = "", []
    if search:
        clause, params = "WHERE s.code LIKE ?", [f"%{search}%"]
    rows = _list_rows(
        f"""SELECT s.*, w.name AS warehouse_name,
                   (SELECT COUNT(*) FROM stocktake_items si WHERE si.stocktake_id=s.id) AS item_count,
                   COALESCE((SELECT SUM(si.counted_quantity-si.system_quantity)
                             FROM stocktake_items si WHERE si.stocktake_id=s.id),0) AS difference
            FROM stocktakes s JOIN warehouses w ON w.id=s.warehouse_id
            {clause} ORDER BY s.id DESC""", params
    )
    return jsonify(ok=True, items=rows)


@bp.get("/stocktakes/<int:stocktake_id>")
@login_required
def stocktake_detail(stocktake_id):
    database = get_db()
    stocktake = database.execute(
        """SELECT s.*,w.name AS warehouse_name,creator.full_name AS created_by_name,
                  confirmer.full_name AS confirmed_by_name
           FROM stocktakes s JOIN warehouses w ON w.id=s.warehouse_id
           JOIN users creator ON creator.id=s.created_by
           LEFT JOIN users confirmer ON confirmer.id=s.confirmed_by
           WHERE s.id=?""",
        (stocktake_id,),
    ).fetchone()
    if stocktake is None:
        return error("Không tìm thấy phiếu kiểm kê.", 404)
    payload = dict(stocktake)
    payload["items"] = _list_rows(
        """SELECT si.*,i.sku,i.name,i.unit,
                  (si.counted_quantity-si.system_quantity) AS difference
           FROM stocktake_items si JOIN inventory i ON i.id=si.inventory_id
           WHERE si.stocktake_id=? ORDER BY si.id""",
        (stocktake_id,),
    )
    return jsonify(ok=True, item=payload)


@bp.post("/stocktakes")
@roles_required("admin", "manager", "warehouse")
@csrf_required
def stocktake_create():
    data = json_object()
    database = get_db()
    code = text(data.get("code")).upper()
    warehouse_id = to_int(data.get("warehouse_id")) or default_warehouse_id(database)
    items = data.get("items") if isinstance(data.get("items"), list) else []
    errors = {}
    if not code:
        errors["code"] = "Vui lòng nhập mã phiếu."
    if not warehouse_id:
        errors["warehouse_id"] = "Vui lòng chọn kho."
    if not items:
        errors["items"] = "Cần ít nhất một hàng hóa."
    normalized = []
    product_ids = set()
    for item in items:
        if not isinstance(item, dict):
            errors["items"] = "Dòng kiểm kê không hợp lệ."
            break
        product_id = to_int(item.get("inventory_id"))
        counted = to_quantity(item.get("counted_quantity"), allow_zero=True)
        stock = database.execute(
            "SELECT quantity FROM inventory WHERE id=? AND warehouse_id=?", (product_id, warehouse_id)
        ).fetchone()
        if stock is None or product_id in product_ids or counted is None or counted < 0:
            errors["items"] = "Hàng hóa hoặc số thực đếm không hợp lệ."
            break
        if counted != stock["quantity"] and len(text(item.get("reason"))) < 3:
            errors["items"] = "Dòng có chênh lệch phải ghi rõ lý do."
            break
        normalized.append((product_id, stock["quantity"], counted, text(item.get("reason"))))
        product_ids.add(product_id)
    if errors:
        return error("Dữ liệu kiểm kê chưa hợp lệ.", 422, errors)
    try:
        database.execute("BEGIN")
        cursor = database.execute(
            "INSERT INTO stocktakes (code,warehouse_id,note,created_by) VALUES (?,?,?,?)",
            (code, warehouse_id, text(data.get("note")), g.user["id"]),
        )
        database.executemany(
            """INSERT INTO stocktake_items
               (stocktake_id,inventory_id,system_quantity,counted_quantity,reason)
               VALUES (?,?,?,?,?)""",
            [(cursor.lastrowid, *item) for item in normalized],
        )
        audit("CREATE", "stocktake", cursor.lastrowid, {"code": code}, g.user["id"], request.remote_addr)
        database.commit()
    except IntegrityError:
        database.rollback()
        return error("Mã phiếu kiểm kê đã tồn tại.", 409)
    return jsonify(ok=True, message="Đã lưu phiếu kiểm kê.", id=cursor.lastrowid), 201


@bp.post("/stocktakes/<int:stocktake_id>/confirm")
@roles_required("admin", "manager", "warehouse")
@csrf_required
def stocktake_confirm(stocktake_id):
    try:
        stocktake, already_completed = confirm_stocktake_service(
            stocktake_id, g.user["id"]
        )
        if already_completed:
            return jsonify(
                ok=True,
                message="Phiếu đã được xác nhận trước đó; tồn kho không thay đổi.",
                already_completed=True,
            )
        audit("CONFIRM", "stocktake", stocktake_id, {"code": stocktake.code}, g.user["id"], request.remote_addr)
        orm.session.commit()
    except LookupError:
        orm.session.rollback()
        return error("Không tìm thấy phiếu kiểm kê.", 404)
    except DomainError as exc:
        orm.session.rollback()
        return error(str(exc), 409)
    except IntegrityError:
        orm.session.rollback()
        return error("Phiếu đã được cập nhật tồn kho.", 409)
    return jsonify(ok=True, message="Đã xác nhận kiểm kê và cập nhật chênh lệch.")


@bp.post("/stocktakes/<int:stocktake_id>/cancel")
@roles_required("admin", "manager", "warehouse")
@csrf_required
def stocktake_cancel(stocktake_id):
    database = get_db()
    stocktake = database.execute(
        "SELECT * FROM stocktakes WHERE id=?", (stocktake_id,)
    ).fetchone()
    if stocktake is None:
        return error("Không tìm thấy phiếu kiểm kê.", 404)
    if stocktake["status"] == "completed":
        return error("Không thể hủy phiếu kiểm kê đã hoàn tất.", 409)
    if stocktake["status"] == "cancelled":
        return jsonify(
            ok=True, message="Phiếu đã được hủy trước đó.", already_cancelled=True
        )
    updated = database.execute(
        """UPDATE stocktakes SET status='cancelled',updated_at=CURRENT_TIMESTAMP
           WHERE id=? AND status='draft'""",
        (stocktake_id,),
    )
    if updated.rowcount != 1:
        database.rollback()
        latest = database.execute("SELECT status FROM stocktakes WHERE id=?", (stocktake_id,)).fetchone()
        if latest and latest["status"] == "cancelled":
            return jsonify(ok=True, message="Phiếu đã được hủy trước đó.", already_cancelled=True)
        return error("Phiếu vừa được xác nhận; không thể hủy.", 409)
    audit("CANCEL", "stocktake", stocktake_id, {"code": stocktake["code"]}, g.user["id"], request.remote_addr)
    database.commit()
    return jsonify(ok=True, message="Đã hủy phiếu kiểm kê.")


def _report_filter_values():
    filters = {
        "from_date": text(request.args.get("from")),
        "to_date": text(request.args.get("to")),
        "warehouse_id": to_int(request.args.get("warehouse_id")),
        "product_id": to_int(request.args.get("product_id")),
        "customer_id": to_int(request.args.get("customer_id")),
    }
    errors = validate_date_range(filters["from_date"], filters["to_date"])
    return filters, errors


def _report_movement_filters(filters):
    clauses, params = ["1=1"], []
    if filters["from_date"]:
        clauses.append("date(sm.created_at)>=date(?)")
        params.append(filters["from_date"])
    if filters["to_date"]:
        clauses.append("date(sm.created_at)<=date(?)")
        params.append(filters["to_date"])
    if filters["warehouse_id"]:
        clauses.append("i.warehouse_id=?")
        params.append(filters["warehouse_id"])
    if filters["product_id"]:
        clauses.append("i.id=?")
        params.append(filters["product_id"])
    if filters["customer_id"]:
        clauses.append(
            """EXISTS (SELECT 1 FROM receipts r
                       WHERE r.code=sm.reference_code
                         AND r.receipt_type='outbound' AND r.partner_id=?)"""
        )
        params.append(filters["customer_id"])
    return " AND ".join(clauses), params


@bp.get("/reports/summary")
@login_required
def report_summary():
    database = get_db()
    filters, filter_errors = _report_filter_values()
    if filter_errors:
        return error(
            "Khoảng thời gian báo cáo chưa hợp lệ.", 422, filter_errors
        )
    from_date = filters["from_date"]
    to_date = filters["to_date"]
    warehouse_id = filters["warehouse_id"]
    product_id = filters["product_id"]
    customer_id = filters["customer_id"]
    inventory_clauses, inventory_params = ["i.status='active'"], []
    if warehouse_id:
        inventory_clauses.append("i.warehouse_id=?")
        inventory_params.append(warehouse_id)
    if product_id:
        inventory_clauses.append("i.id=?")
        inventory_params.append(product_id)
    summary = database.execute(
        f"""SELECT COUNT(*) AS products, COALESCE(SUM(i.quantity),0) AS stock,
                   SUM(CASE WHEN i.quantity<=i.min_quantity THEN 1 ELSE 0 END) AS alerts
            FROM inventory i WHERE {' AND '.join(inventory_clauses)}""",
        inventory_params,
    ).fetchone()
    movement_where, movement_params = _report_movement_filters(filters)
    movement_totals = _list_rows(
        f"""SELECT sm.movement_type, COUNT(*) AS transactions,
                   COALESCE(SUM(ABS(sm.quantity_change)),0) AS quantity
            FROM stock_movements sm JOIN inventory i ON i.id=sm.inventory_id
            WHERE {movement_where} GROUP BY sm.movement_type""",
        movement_params,
    )
    movements = _list_rows(
        f"""SELECT sm.*, i.sku, i.name
           FROM stock_movements sm JOIN inventory i ON i.id=sm.inventory_id
           WHERE {movement_where} ORDER BY sm.id DESC LIMIT 30""",
        movement_params,
    )
    alerts = _list_rows(
        f"""SELECT i.sku,i.name,i.quantity,i.min_quantity,i.unit FROM inventory i
            WHERE i.quantity<=i.min_quantity AND {' AND '.join(inventory_clauses)}
            ORDER BY i.quantity ASC LIMIT 10""",
        inventory_params,
    )
    receipt_clauses, receipt_params = ["status='completed'"], []
    if from_date:
        receipt_clauses.append("date(confirmed_at)>=date(?)")
        receipt_params.append(from_date)
    if to_date:
        receipt_clauses.append("date(confirmed_at)<=date(?)")
        receipt_params.append(to_date)
    if warehouse_id:
        receipt_clauses.append("warehouse_id=?")
        receipt_params.append(warehouse_id)
    if product_id:
        receipt_clauses.append(
            "EXISTS (SELECT 1 FROM receipt_items ri WHERE ri.receipt_id=receipts.id AND ri.inventory_id=?)"
        )
        receipt_params.append(product_id)
    if customer_id:
        receipt_clauses.append("receipt_type='outbound' AND partner_id=?")
        receipt_params.append(customer_id)
    receipt_counts = database.execute(
        f"""SELECT
              SUM(CASE WHEN receipt_type='inbound' THEN 1 ELSE 0 END) inbound,
              SUM(CASE WHEN receipt_type='outbound' THEN 1 ELSE 0 END) outbound
            FROM receipts WHERE {' AND '.join(receipt_clauses)}""",
        receipt_params,
    ).fetchone()
    return jsonify(ok=True, summary=dict(summary), receipt_counts=dict(receipt_counts),
                   movement_totals=movement_totals, movements=movements, alerts=alerts)


@bp.get("/reports/export.csv")
@login_required
def report_export():
    filters, filter_errors = _report_filter_values()
    if filter_errors:
        return error(
            "Khoảng thời gian báo cáo chưa hợp lệ.", 422, filter_errors
        )
    movement_where, movement_params = _report_movement_filters(
        filters
    )
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output)
    writer.writerow(["Thời gian", "Chứng từ", "SKU", "Hàng hóa", "Loại", "Thay đổi", "Tồn sau"])
    for row in _list_rows(
        f"""SELECT sm.created_at,sm.reference_code,i.sku,i.name,sm.movement_type,
                   sm.quantity_change,sm.balance_after
            FROM stock_movements sm JOIN inventory i ON i.id=sm.inventory_id
            WHERE {movement_where}
            ORDER BY sm.id DESC""",
        movement_params,
    ):
        writer.writerow(row.values())
    return Response(output.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=bao-cao-kho.csv"})
