import json
import re
import sqlite3

from flask import Blueprint, g, jsonify, request
from werkzeug.security import check_password_hash, generate_password_hash

from .auth import csrf_required, login_required, roles_required
from .db import audit, get_db

bp = Blueprint("api", __name__, url_prefix="/api")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{4,30}$")
CODE_RE = re.compile(r"^[A-Za-z0-9_-]{2,20}$")
ROLE_LABELS = {"admin": "Quản trị viên", "manager": "Quản lý kho", "staff": "Nhân viên"}


def error(message, status=400, errors=None):
    payload = {"ok": False, "message": message}
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
@roles_required("admin", "manager")
@csrf_required
def adjust_inventory(item_id):
    data = request.get_json(silent=True) or {}
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
@roles_required("admin", "manager")
@csrf_required
def category_create():
    values, errors = validate_category(request.get_json(silent=True) or {})
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
@roles_required("admin", "manager")
@csrf_required
def category_update(category_id):
    values, errors = validate_category(request.get_json(silent=True) or {})
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
    values, errors = validate_user(request.get_json(silent=True) or {})
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
    data = request.get_json(silent=True) or {}
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
        "SELECT 1 FROM inventory_adjustments WHERE created_by = ? LIMIT 1", (user_id,)
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
    data = request.get_json(silent=True) or {}
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
    data = request.get_json(silent=True) or {}
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
