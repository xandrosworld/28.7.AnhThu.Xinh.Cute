from functools import wraps

from flask import Blueprint, g, redirect, render_template, url_for

bp = Blueprint("views", __name__)


def page_login_required(view):
    @wraps(view)
    def wrapped_view(**kwargs):
        if g.user is None:
            return redirect(url_for("views.login"))
        return view(**kwargs)

    return wrapped_view


def page_roles_required(*roles):
    def decorator(view):
        @wraps(view)
        @page_login_required
        def wrapped_view(**kwargs):
            if g.user["role"] not in roles:
                return render_template(
                    "error.html",
                    code=403,
                    message="Bạn không có quyền truy cập trang này.",
                ), 403
            return view(**kwargs)

        return wrapped_view

    return decorator


@bp.get("/")
@bp.get("/index.html")
def login():
    if g.user is not None:
        return redirect(url_for("views.dashboard"))
    return render_template("login.html")


@bp.get("/dashboard")
@page_login_required
def dashboard():
    return render_template("dashboard.html", page="dashboard")


@bp.get("/inventory")
@bp.get("/tonkho.html")
@page_login_required
def inventory():
    return render_template("inventory.html", page="inventory")


@bp.get("/categories")
@bp.get("/danhmuc.html")
@page_login_required
def categories():
    return render_template("categories.html", page="categories")


@bp.get("/users")
@bp.get("/quanlynguoidung.html")
@page_roles_required("admin")
def users():
    return render_template("users.html", page="users")


@bp.get("/profile")
@bp.get("/hosocanhan.html")
@page_login_required
def profile():
    return render_template("profile.html", page="profile")


@bp.get("/audit-logs")
@page_roles_required("admin")
def audit_logs():
    return render_template("audit.html", page="audit")
