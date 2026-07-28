import os

from flask import Flask, jsonify, render_template, request

from . import db


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-only-change-me"),
        DATABASE=os.path.join(app.instance_path, "dnp_wms.sqlite"),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=60 * 60 * 8,
        MAX_CONTENT_LENGTH=1024 * 1024,
        JSON_AS_ASCII=False,
    )

    if test_config:
        app.config.update(test_config)

    os.makedirs(app.instance_path, exist_ok=True)
    db.init_app(app)

    from .auth import bp as auth_bp
    from .api import bp as api_bp
    from .views import bp as views_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(views_bp)

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        )
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(404)
    def not_found(error):
        if request.path.startswith("/api/"):
            return jsonify(ok=False, message="Không tìm thấy tài nguyên."), 404
        return render_template(
            "error.html", code=404, message="Trang bạn tìm kiếm không tồn tại."
        ), 404

    @app.errorhandler(413)
    def payload_too_large(error):
        if request.path.startswith("/api/"):
            return jsonify(ok=False, message="Dữ liệu gửi lên vượt quá giới hạn."), 413
        return render_template(
            "error.html", code=413, message="Dữ liệu gửi lên vượt quá giới hạn."
        ), 413

    @app.errorhandler(500)
    def server_error(error):
        if request.path.startswith("/api/"):
            return jsonify(ok=False, message="Hệ thống gặp lỗi. Vui lòng thử lại."), 500
        return render_template(
            "error.html", code=500, message="Hệ thống gặp lỗi. Vui lòng thử lại."
        ), 500

    with app.app_context():
        if not os.path.exists(app.config["DATABASE"]):
            db.init_database()

    return app
