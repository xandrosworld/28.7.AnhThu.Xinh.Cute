import csv
import io
import json
import re
import sqlite3

from flask import Blueprint, Response, g, jsonify, request
from werkzeug.security import check_password_hash, generate_password_hash

from .auth import csrf_required, login_required, roles_required
from .db import audit, get_db

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


def json_object():
    payload = request.get_json(silent=True)
    return payload if isinstance(payload, dict) else {}


def initials(full_name):
    parts = [item for item in full_name.split() if item]
    return "".join(item[0].upper() for item in parts[-2:]) or "DN"


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
        "quantity": quantity,
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
        SELECT c.name, COUNT(i.id) AS product_count, COALESCE(SUM(i.quantity), 0) AS quantity
        FROM categories c LEFT JOIN inventory i ON i.category_id = c.id
        WHERE c.status = 'active'
        GROUP BY c.id ORDER BY quantity DESC
        """
    ).fetchall()
    return jsonify(
        ok=True,
        summary=dict(summary),
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
        clauses.append("(i.sku LIKE ? OR i.name LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])
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
        SELECT i.*, c.name AS category_name, w.name AS warehouse_name
        FROM inventory i
        JOIN categories c ON c.id = i.category_id
        JOIN warehouses w ON w.id = i.warehouse_id
        WHERE {where}
        ORDER BY i.updated_at DESC, i.id DESC
        LIMIT ? OFFSET ?
        """,
        [*params, per_page, (page - 1) * per_page],
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
        SELECT i.*, c.name AS category_name, w.name AS warehouse_name
        FROM inventory i
        JOIN categories c ON c.id = i.category_id
        JOIN warehouses w ON w.id = i.warehouse_id
        WHERE i.id = ?
        """,
        (item_id,),
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
    return jsonify(
        ok=True,
        item=serialize_inventory(row),
        adjustments=[dict(entry) for entry in history],
    )


@bp.post("/inventory/<int:item_id>/adjustments")
@roles_required("admin", "manager", "warehouse")
@csrf_required
def adjust_inventory(item_id):
    data = json_object()
    new_quantity = to_int(data.get("new_quantity"))
    reason = text(data.get("reason"))
    note = text(data.get("note"))
    errors = {}
    if new_quantity is None or new_quantity < 0:
        errors["new_quantity"] = "Số lượng thực tế phải là số nguyên không âm."
    if reason not in {"Kiểm kê định kỳ", "Hàng hư hỏng", "Sai lệch chứng từ", "Điều chỉnh khác"}:
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
        database.execute("BEGIN")
        cursor = database.execute(
            """
            INSERT INTO inventory_adjustments
                (inventory_id, old_quantity, new_quantity, difference, reason, note, created_by)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item_id,
                item["quantity"],
                new_quantity,
                new_quantity - item["quantity"],
                reason,
                note,
                g.user["id"],
            ),
        )
        database.execute(
            "UPDATE inventory SET quantity = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (new_quantity, item_id),
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
        database.commit()
    except Exception:
        database.rollback()
        raise
    return jsonify(
        ok=True,
        message=f"Đã cập nhật tồn kho {item['sku']}.",
        adjustment_id=cursor.lastrowid,
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
        SELECT c.*, COUNT(i.id) AS product_count
        FROM categories c LEFT JOIN inventory i ON i.category_id = c.id
        {where}
        GROUP BY c.id ORDER BY c.id DESC
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
    except sqlite3.IntegrityError:
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
    except sqlite3.IntegrityError:
        return error("Mã hoặc tên danh mục đã tồn tại.", 409)
    return jsonify(ok=True, message="Đã cập nhật danh mục.")


@bp.delete("/categories/<int:category_id>")
@roles_required("admin")
@csrf_required
def category_delete(category_id):
    database = get_db()
    category = database.execute(
        """
        SELECT c.id, c.code, c.name, COUNT(i.id) AS product_count
        FROM categories c LEFT JOIN inventory i ON i.category_id = c.id
        WHERE c.id = ? GROUP BY c.id
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


@bp.post("/users")
@roles_required("admin")
@csrf_required
def user_create():
    values, errors = validate_user(json_object())
    if errors:
        return error("Dữ liệu người dùng chưa hợp lệ.", 422, errors)
    username, full_name, email, phone, role, status, password = values
    database = get_db()
    try:
        cursor = database.execute(
            """
            INSERT INTO users
                (username, password_hash, full_name, email, phone, role, status, avatar_initials)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                username,
                generate_password_hash(password),
                full_name,
                email,
                phone,
                role,
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
    except sqlite3.IntegrityError:
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
                    role = ?, status = ?, avatar_initials = ?, password_hash = ?,
                    updated_at = CURRENT_TIMESTAMP WHERE id = ?
                """,
                (
                    username,
                    full_name,
                    email,
                    phone,
                    role,
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
                    role = ?, status = ?, avatar_initials = ?,
                    updated_at = CURRENT_TIMESTAMP WHERE id = ?
                """,
                (
                    username,
                    full_name,
                    email,
                    phone,
                    role,
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
    except sqlite3.IntegrityError:
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
    has_activity = database.execute(
        """
        SELECT 1 FROM inventory_adjustments WHERE created_by = ?
        UNION ALL SELECT 1 FROM receipts WHERE created_by = ? OR confirmed_by = ?
        UNION ALL SELECT 1 FROM stocktakes WHERE created_by = ? OR confirmed_by = ?
        UNION ALL SELECT 1 FROM stock_movements WHERE created_by = ?
        LIMIT 1
        """,
        (user_id, user_id, user_id, user_id, user_id, user_id),
    ).fetchone()
    if has_activity:
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
    except sqlite3.IntegrityError:
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
    sku, name, unit = text(data.get("sku")).upper(), text(data.get("name")), text(data.get("unit"))
    category_id, warehouse_id = to_int(data.get("category_id")), to_int(data.get("warehouse_id"))
    errors = {}
    if not CODE_RE.fullmatch(sku):
        errors["sku"] = "Mã SKU gồm 2–20 chữ, số, gạch ngang hoặc gạch dưới."
    if len(name) < 2:
        errors["name"] = "Tên hàng hóa phải có ít nhất 2 ký tự."
    if not unit:
        errors["unit"] = "Vui lòng nhập đơn vị tính."
    if not category_id:
        errors["category_id"] = "Vui lòng chọn danh mục."
    if not warehouse_id:
        errors["warehouse_id"] = "Vui lòng chọn kho."
    status = text(data.get("status")) or "active"
    if status not in {"active", "inactive"}:
        errors["status"] = "Trạng thái không hợp lệ."
    if errors:
        return error("Dữ liệu hàng hóa chưa hợp lệ.", 422, errors)
    database = get_db()
    try:
        cursor = database.execute(
            """INSERT INTO inventory
               (sku, barcode, name, category_id, warehouse_id, unit, min_quantity,
                location, description, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                sku, text(data.get("barcode")) or None, name, category_id, warehouse_id,
                unit, max(to_int(data.get("min_quantity"), 0), 0),
                text(data.get("location")), text(data.get("description")),
                status,
            ),
        )
        audit("CREATE", "product", cursor.lastrowid, {"sku": sku}, g.user["id"], request.remote_addr)
        database.commit()
    except sqlite3.IntegrityError:
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
        else:
            cursor = database.execute(
                """INSERT INTO suppliers
                   (code,name,email,phone,address,status) VALUES (?,?,?,?,?,?)""",
                (code, name, email, text(data.get("phone")),
                 text(data.get("address")), status),
            )
        audit("CREATE", table[:-1], cursor.lastrowid, {"code": code}, g.user["id"], request.remote_addr)
        database.commit()
    except sqlite3.IntegrityError:
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


@bp.get("/warehouses")
@login_required
def warehouse_list():
    rows = _list_rows(
        """SELECT w.*, COUNT(i.id) AS product_count,
                  COALESCE(SUM(i.quantity),0) AS total_quantity
           FROM warehouses w LEFT JOIN inventory i ON i.warehouse_id=w.id
           GROUP BY w.id ORDER BY w.name"""
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
        f"""SELECT r.*, w.name AS warehouse_name, COUNT(ri.id) AS item_count,
                   COALESCE(SUM(ri.quantity),0) AS total_quantity
            FROM receipts r JOIN warehouses w ON w.id=r.warehouse_id
            LEFT JOIN receipt_items ri ON ri.receipt_id=r.id
            WHERE {' AND '.join(clauses)} GROUP BY r.id ORDER BY r.id DESC""",
        params,
    )
    stats = {key: 0 for key in ("draft", "pending", "picking", "completed", "cancelled")}
    for row in rows:
        stats[row["status"]] = stats.get(row["status"], 0) + 1
    return jsonify(ok=True, items=rows, stats=stats)


def _receipt_create(receipt_type):
    data = json_object()
    code = text(data.get("code")).upper()
    partner_id, warehouse_id = to_int(data.get("partner_id")), to_int(data.get("warehouse_id"))
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
    database = get_db()
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
        allowed = {
            email.strip().lower()
            for email in partner["contract_emails"].split(",")
            if email.strip()
        }
        if not EMAIL_RE.fullmatch(request_email) or request_email not in allowed:
            errors["request_email"] = "Email không thuộc danh sách email hợp đồng."
    normalized = []
    requested_by_product = {}
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            errors["items"] = f"Dòng {index + 1} chưa hợp lệ."
            break
        inventory_id, quantity = to_int(item.get("inventory_id")), to_int(item.get("quantity"))
        stock = database.execute(
            """SELECT id,quantity FROM inventory
               WHERE id=? AND warehouse_id=? AND status='active'""",
            (inventory_id, warehouse_id),
        ).fetchone()
        if stock is None or not quantity or quantity <= 0:
            errors["items"] = (
                f"Dòng {index + 1} chưa hợp lệ hoặc hàng hóa không thuộc kho đã chọn."
            )
            break
        requested_by_product[inventory_id] = (
            requested_by_product.get(inventory_id, 0) + quantity
        )
        if (
            receipt_type == "outbound"
            and requested_by_product[inventory_id] > stock["quantity"]
        ):
            errors["items"] = f"Dòng {index + 1} vượt tồn khả dụng."
            break
        normalized.append((inventory_id, quantity, text(item.get("pallet_id")),
                           text(item.get("barcode")), text(item.get("expiry_date")) or None))
    if errors:
        return error("Dữ liệu phiếu chưa hợp lệ.", 422, errors)
    try:
        database.execute("BEGIN")
        cursor = database.execute(
            """INSERT INTO receipts
               (code,receipt_type,partner_id,partner_name,warehouse_id,request_email,
                container_no,seal_no,status,note,created_by)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (code, receipt_type, partner_id, partner["name"], warehouse_id, request_email,
             text(data.get("container_no")), text(data.get("seal_no")),
             status, text(data.get("note")), g.user["id"]),
        )
        database.executemany(
            """INSERT INTO receipt_items
               (receipt_id,inventory_id,quantity,accepted_quantity,pallet_id,barcode,expiry_date)
               VALUES (?,?,?,?,?,?,?)""",
            [(cursor.lastrowid, product_id, qty, qty, pallet, barcode, expiry)
             for product_id, qty, pallet, barcode, expiry in normalized],
        )
        audit("CREATE", f"{receipt_type}_receipt", cursor.lastrowid, {"code": code}, g.user["id"], request.remote_addr)
        database.commit()
    except sqlite3.IntegrityError:
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
    database = get_db()
    receipt = database.execute(
        "SELECT * FROM receipts WHERE id=? AND receipt_type=?", (receipt_id, expected_type)
    ).fetchone()
    if receipt is None:
        return error("Không tìm thấy phiếu.", 404)
    if receipt["status"] == "completed":
        return jsonify(
            ok=True,
            message="Phiếu đã được xác nhận trước đó; tồn kho không thay đổi.",
            already_completed=True,
        )
    if receipt["status"] == "cancelled":
        return error("Không thể xác nhận phiếu đã hủy.", 409)
    if receipt["status"] == "rejected":
        return error("Không thể xác nhận phiếu đã từ chối.", 409)
    items = database.execute("SELECT * FROM receipt_items WHERE receipt_id=?", (receipt_id,)).fetchall()
    try:
        database.execute("BEGIN")
        for item in items:
            stock = database.execute("SELECT quantity FROM inventory WHERE id=?", (item["inventory_id"],)).fetchone()
            change = item["accepted_quantity"] if expected_type == "inbound" else -item["quantity"]
            if stock["quantity"] + change < 0:
                raise ValueError("Tồn kho không đủ để xác nhận phiếu.")
            balance = stock["quantity"] + change
            database.execute("UPDATE inventory SET quantity=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (balance, item["inventory_id"]))
            database.execute(
                """INSERT INTO stock_movements
                   (inventory_id,movement_type,reference_code,quantity_change,balance_after,pallet_id,created_by)
                   VALUES (?,?,?,?,?,?,?)""",
                (item["inventory_id"], expected_type, receipt["code"], change, balance, item["pallet_id"], g.user["id"]),
            )
        database.execute(
            """UPDATE receipts SET status='completed', confirmed_by=?,
               confirmed_at=CURRENT_TIMESTAMP WHERE id=?""", (g.user["id"], receipt_id)
        )
        audit("CONFIRM", f"{expected_type}_receipt", receipt_id, {"code": receipt["code"]}, g.user["id"], request.remote_addr)
        database.commit()
    except ValueError as exc:
        database.rollback()
        return error(str(exc), 409)
    except sqlite3.IntegrityError:
        database.rollback()
        return error("Phiếu đã được ghi nhận tồn kho.", 409)
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
        line_id, accepted = to_int(item.get("id")), to_int(item.get("accepted_quantity"))
        line = existing.get(line_id)
        if (
            line is None
            or line_id in submitted_ids
            or accepted is None
            or accepted < 0
            or accepted > line["quantity"]
        ):
            return error("Số lượng thực nhận không hợp lệ.", 422, {"items": "Kiểm tra lại từng dòng hàng."})
        submitted_ids.add(line_id)
        normalized.append((accepted, text(item.get("issue_note")), line_id))
    if submitted_ids != set(existing):
        return error("Cần kiểm nhận đầy đủ các dòng hàng.", 422, {"items": "Thiếu dòng kiểm nhận."})
    database.executemany(
        "UPDATE receipt_items SET accepted_quantity=?, issue_note=? WHERE id=?",
        normalized,
    )
    database.execute("UPDATE receipts SET status='pending' WHERE id=?", (receipt_id,))
    audit("INSPECT", "inbound_receipt", receipt_id, {}, g.user["id"], request.remote_addr)
    database.commit()
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
        available = item["available_quantity"]
        requested = requested_by_product[item["inventory_id"]]
        ok = available >= requested
        sufficient = sufficient and ok
        lines.append({
            "inventory_id": item["inventory_id"],
            "sku": item["sku"],
            "name": item["name"],
            "requested": requested,
            "available": available,
            "sufficient": ok,
        })
    return jsonify(ok=True, sufficient=sufficient, items=lines,
                   message="Đủ tồn để xuất." if sufficient else "Một số mặt hàng không đủ tồn.")


@bp.get("/outbound-receipts/<int:receipt_id>/picking-list")
@login_required
def outbound_picking_list(receipt_id):
    receipt = _receipt_payload(receipt_id)
    if receipt is None or receipt["receipt_type"] != "outbound":
        return error("Không tìm thấy phiếu xuất.", 404)
    items = sorted(
        receipt["items"],
        key=lambda item: (
            item["expiry_date"] is None,
            item["expiry_date"] or "9999-12-31",
            item["id"],
        ),
    )
    return jsonify(
        ok=True,
        receipt_code=receipt["code"],
        warehouse_name=receipt["warehouse_name"],
        strategy="FEFO khi có hạn dùng, FIFO cho hàng còn lại",
        items=items,
    )


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
    database.execute("UPDATE receipts SET status='cancelled' WHERE id=?", (receipt_id,))
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
        f"""SELECT s.*, w.name AS warehouse_name, COUNT(si.id) AS item_count,
                   COALESCE(SUM(si.counted_quantity-si.system_quantity),0) AS difference
            FROM stocktakes s JOIN warehouses w ON w.id=s.warehouse_id
            LEFT JOIN stocktake_items si ON si.stocktake_id=s.id
            {clause} GROUP BY s.id ORDER BY s.id DESC""", params
    )
    return jsonify(ok=True, items=rows)


@bp.post("/stocktakes")
@roles_required("admin", "manager", "warehouse")
@csrf_required
def stocktake_create():
    data = json_object()
    code, warehouse_id = text(data.get("code")).upper(), to_int(data.get("warehouse_id"))
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
    database = get_db()
    for item in items:
        if not isinstance(item, dict):
            errors["items"] = "Dòng kiểm kê không hợp lệ."
            break
        product_id, counted = to_int(item.get("inventory_id")), to_int(item.get("counted_quantity"))
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
    except sqlite3.IntegrityError:
        database.rollback()
        return error("Mã phiếu kiểm kê đã tồn tại.", 409)
    return jsonify(ok=True, message="Đã lưu phiếu kiểm kê.", id=cursor.lastrowid), 201


@bp.post("/stocktakes/<int:stocktake_id>/confirm")
@roles_required("admin", "manager", "warehouse")
@csrf_required
def stocktake_confirm(stocktake_id):
    database = get_db()
    stocktake = database.execute("SELECT * FROM stocktakes WHERE id=?", (stocktake_id,)).fetchone()
    if stocktake is None:
        return error("Không tìm thấy phiếu kiểm kê.", 404)
    if stocktake["status"] == "completed":
        return error("Phiếu đã được xác nhận.", 409)
    if stocktake["status"] == "cancelled":
        return error("Không thể xác nhận phiếu đã hủy.", 409)
    items = database.execute("SELECT * FROM stocktake_items WHERE stocktake_id=?", (stocktake_id,)).fetchall()
    try:
        database.execute("BEGIN")
        for item in items:
            current = database.execute("SELECT quantity FROM inventory WHERE id=?", (item["inventory_id"],)).fetchone()[0]
            if current != item["system_quantity"]:
                raise ValueError(
                    "Tồn kho đã thay đổi sau khi lập phiếu; vui lòng tạo phiếu kiểm kê mới."
                )
            change = item["counted_quantity"] - current
            database.execute("UPDATE inventory SET quantity=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (item["counted_quantity"], item["inventory_id"]))
            database.execute(
                """INSERT INTO stock_movements
                   (inventory_id,movement_type,reference_code,quantity_change,balance_after,created_by)
                   VALUES (?,?,?,?,?,?)""",
                (item["inventory_id"], "stocktake", stocktake["code"], change, item["counted_quantity"], g.user["id"]),
            )
        database.execute(
            "UPDATE stocktakes SET status='completed',confirmed_by=?,confirmed_at=CURRENT_TIMESTAMP WHERE id=?",
            (g.user["id"], stocktake_id),
        )
        audit("CONFIRM", "stocktake", stocktake_id, {"code": stocktake["code"]}, g.user["id"], request.remote_addr)
        database.commit()
    except ValueError as exc:
        database.rollback()
        return error(str(exc), 409)
    except sqlite3.IntegrityError:
        database.rollback()
        return error("Phiếu đã được cập nhật tồn kho.", 409)
    return jsonify(ok=True, message="Đã xác nhận kiểm kê và cập nhật chênh lệch.")


@bp.get("/reports/summary")
@login_required
def report_summary():
    database = get_db()
    from_date = text(request.args.get("from"))
    to_date = text(request.args.get("to"))
    warehouse_id = to_int(request.args.get("warehouse_id"))
    inventory_clauses, inventory_params = ["i.status='active'"], []
    if warehouse_id:
        inventory_clauses.append("i.warehouse_id=?")
        inventory_params.append(warehouse_id)
    summary = database.execute(
        f"""SELECT COUNT(*) AS products, COALESCE(SUM(i.quantity),0) AS stock,
                   SUM(CASE WHEN i.quantity<=i.min_quantity THEN 1 ELSE 0 END) AS alerts
            FROM inventory i WHERE {' AND '.join(inventory_clauses)}""",
        inventory_params,
    ).fetchone()
    movement_clauses, movement_params = ["1=1"], []
    if from_date:
        movement_clauses.append("date(sm.created_at)>=date(?)")
        movement_params.append(from_date)
    if to_date:
        movement_clauses.append("date(sm.created_at)<=date(?)")
        movement_params.append(to_date)
    if warehouse_id:
        movement_clauses.append("i.warehouse_id=?")
        movement_params.append(warehouse_id)
    movement_where = " AND ".join(movement_clauses)
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
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output)
    writer.writerow(["Thời gian", "Chứng từ", "SKU", "Hàng hóa", "Loại", "Thay đổi", "Tồn sau"])
    for row in _list_rows(
        """SELECT sm.created_at,sm.reference_code,i.sku,i.name,sm.movement_type,
                  sm.quantity_change,sm.balance_after
           FROM stock_movements sm JOIN inventory i ON i.id=sm.inventory_id
           ORDER BY sm.id DESC"""
    ):
        writer.writerow(row.values())
    return Response(output.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=bao-cao-kho.csv"})
