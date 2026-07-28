import secrets
from functools import wraps

from flask import Blueprint, g, jsonify, request, session
from werkzeug.security import check_password_hash

from .db import audit, get_db

bp = Blueprint("auth", __name__, url_prefix="/api/auth")


def _json_error(message, status=400, errors=None):
    payload = {"ok": False, "message": message}
    if errors:
        payload["errors"] = errors
    return jsonify(payload), status


def _user_payload(user):
    return {
        "id": user["id"],
        "username": user["username"],
        "full_name": user["full_name"],
        "email": user["email"],
        "phone": user["phone"],
        "role": user["role"],
        "status": user["status"],
        "avatar_initials": user["avatar_initials"],
    }


@bp.before_app_request
def load_logged_in_user():
    user_id = session.get("user_id")
    g.user = (
        get_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if user_id
        else None
    )
    if g.user and g.user["status"] != "active":
        session.clear()
        g.user = None


def login_required(view):
    @wraps(view)
    def wrapped_view(**kwargs):
        if g.user is None:
            return _json_error("Phiên đăng nhập đã hết hạn.", 401)
        return view(**kwargs)

    return wrapped_view


def roles_required(*roles):
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped_view(**kwargs):
            if g.user["role"] not in roles:
                return _json_error("Bạn không có quyền thực hiện thao tác này.", 403)
            return view(**kwargs)

        return wrapped_view

    return decorator


def csrf_required(view):
    @wraps(view)
    def wrapped_view(**kwargs):
        expected = session.get("csrf_token", "")
        supplied = request.headers.get("X-CSRF-Token", "")
        if not expected or not secrets.compare_digest(expected, supplied):
            return _json_error("Yêu cầu không hợp lệ. Vui lòng tải lại trang.", 403)
        return view(**kwargs)

    return wrapped_view


@bp.post("/login")
def login():
    data = request.get_json(silent=True) or {}
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))
    errors = {}
    if not username:
        errors["username"] = "Vui lòng nhập tên đăng nhập."
    if not password:
        errors["password"] = "Vui lòng nhập mật khẩu."
    if errors:
        return _json_error("Vui lòng kiểm tra thông tin đăng nhập.", 422, errors)

    database = get_db()
    user = database.execute(
        "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)
    ).fetchone()
    if user is None or not check_password_hash(user["password_hash"], password):
        return _json_error("Tên đăng nhập hoặc mật khẩu không đúng.", 401)
    if user["status"] != "active":
        return _json_error("Tài khoản đã bị khóa. Vui lòng liên hệ quản trị viên.", 403)

    session.clear()
    session["user_id"] = user["id"]
    session["csrf_token"] = secrets.token_urlsafe(32)
    session.permanent = True
    audit(
        "LOGIN",
        "auth",
        user_id=user["id"],
        details={"username": user["username"]},
        ip_address=request.remote_addr,
    )
    database.commit()
    return jsonify(
        ok=True,
        message="Đăng nhập thành công.",
        user=_user_payload(user),
        csrf_token=session["csrf_token"],
    )


@bp.post("/logout")
@login_required
@csrf_required
def logout():
    database = get_db()
    audit(
        "LOGOUT",
        "auth",
        user_id=g.user["id"],
        ip_address=request.remote_addr,
    )
    database.commit()
    session.clear()
    return jsonify(ok=True, message="Đã đăng xuất.")


@bp.get("/me")
@login_required
def me():
    return jsonify(
        ok=True, user=_user_payload(g.user), csrf_token=session.get("csrf_token")
    )
