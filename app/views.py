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


@bp.get("/settings")
@page_roles_required("admin")
def settings():
    return render_template("settings.html", page="settings")


@bp.get("/products")
@page_login_required
def products():
    return render_template("products.html", page="products")


@bp.get("/customers")
@page_login_required
def customers():
    return render_template("partners.html", page="customers", partner_type="customers")


@bp.get("/suppliers")
@page_login_required
def suppliers():
    return render_template("partners.html", page="suppliers", partner_type="suppliers")


@bp.get("/warehouses")
@page_login_required
def warehouses():
    return render_template("warehouses.html", page="warehouses")


@bp.get("/inbound-receipts")
@page_login_required
def inbound_receipts():
    return render_template("receipts.html", page="inbound", receipt_type="inbound")


@bp.get("/outbound-receipts")
@page_login_required
def outbound_receipts():
    return render_template("receipts.html", page="outbound", receipt_type="outbound")


@bp.get("/stocktakes")
@page_login_required
def stocktakes():
    return render_template("stocktakes.html", page="stocktakes")


@bp.get("/reports")
@page_login_required
def reports():
    return render_template("reports.html", page="reports")
