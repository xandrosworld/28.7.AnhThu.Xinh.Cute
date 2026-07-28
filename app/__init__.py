import os
import secrets
from pathlib import Path

from flask import Flask, jsonify, render_template, request
from dotenv import load_dotenv
from sqlalchemy import event
from sqlalchemy.engine import Engine

from . import db
from .extensions import db as orm
from .extensions import migrate


def _database_uri(app, config):
    """Resolve test ``DATABASE`` compatibility and production DATABASE_URL."""
    if config and config.get("SQLALCHEMY_DATABASE_URI"):
        return config["SQLALCHEMY_DATABASE_URI"]
    if config and config.get("DATABASE"):
        return f"sqlite:///{Path(config['DATABASE']).resolve().as_posix()}"
    value = os.environ.get("DATABASE_URL", "").strip()
    if value:
        # Heroku-style historical aliases are not accepted by SQLAlchemy 2.
        if value.startswith("mssql://") and "driver=" not in value.lower():
            separator = "&" if "?" in value else "?"
            value += f"{separator}driver=ODBC+Driver+18+for+SQL+Server&TrustServerCertificate=yes"
        return value
    return f"sqlite:///{Path(app.instance_path, 'dnp_wms.sqlite').as_posix()}"


def create_app(test_config=None):
    load_dotenv()
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY") or secrets.token_hex(32),
        DATABASE=os.path.join(app.instance_path, "dnp_wms.sqlite"),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=60 * 60 * 8,
        MAX_CONTENT_LENGTH=1024 * 1024,
        JSON_AS_ASCII=False,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True},
    )

    if test_config:
        app.config.update(test_config)
    app.config["SQLALCHEMY_DATABASE_URI"] = _database_uri(app, test_config)

    os.makedirs(app.instance_path, exist_ok=True)
    orm.init_app(app)
    migrate.init_app(app, orm)
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
            "style-src 'self' 'unsafe-inline'; font-src 'self'; "
            "img-src 'self' data: blob:; connect-src 'self'; "
            "media-src 'self' blob:; frame-ancestors 'none'"
        )
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
            # Public API contract: retain the historical top-level aliases used
            # by the frontend while always exposing predictable data/meta.
            if 200 <= response.status_code < 300 and response.is_json:
                payload = response.get_json(silent=True)
                if isinstance(payload, dict) and payload.get("ok", True):
                    if "data" not in payload:
                        if "item" in payload:
                            payload["data"] = payload["item"]
                        elif "items" in payload:
                            payload["data"] = payload["items"]
                        elif "user" in payload:
                            payload["data"] = payload["user"]
                        else:
                            payload["data"] = {
                                key: value for key, value in payload.items()
                                if key not in {"ok", "message", "meta"}
                            }
                    payload.setdefault("meta", payload.get("pagination") or {})
                    response.set_data(app.json.dumps(payload))
        return response

    @app.errorhandler(404)
    def not_found(error):
        if request.path.startswith("/api/"):
            return jsonify(ok=False, message="Không tìm thấy tài nguyên.",
                           error={"code": "not_found", "message": "Không tìm thấy tài nguyên.", "fields": {}}), 404
        return render_template(
            "error.html", code=404, message="Trang bạn tìm kiếm không tồn tại."
        ), 404

    @app.errorhandler(413)
    def payload_too_large(error):
        if request.path.startswith("/api/"):
            return jsonify(ok=False, message="Dữ liệu gửi lên vượt quá giới hạn.",
                           error={"code": "payload_too_large", "message": "Dữ liệu gửi lên vượt quá giới hạn.", "fields": {}}), 413
        return render_template(
            "error.html", code=413, message="Dữ liệu gửi lên vượt quá giới hạn."
        ), 413

    @app.errorhandler(500)
    def server_error(error):
        if request.path.startswith("/api/"):
            return jsonify(ok=False, message="Hệ thống gặp lỗi. Vui lòng thử lại.",
                           error={"code": "internal_error", "message": "Hệ thống gặp lỗi. Vui lòng thử lại.", "fields": {}}), 500
        return render_template(
            "error.html", code=500, message="Hệ thống gặp lỗi. Vui lòng thử lại."
        ), 500

    with app.app_context():
        from . import models  # noqa: F401

        database_uri = app.config["SQLALCHEMY_DATABASE_URI"]
        env_auto_init = os.environ.get("AUTO_INIT_DB")
        auto_init = app.config.get(
            "AUTO_INIT_DB",
            (
                env_auto_init.lower() in {"1", "true", "yes"}
                if env_auto_init is not None
                else False
            ),
        )
        sqlite_path = (
            database_uri.removeprefix("sqlite:///") if database_uri.startswith("sqlite:///") else None
        )
        if auto_init and sqlite_path and not os.path.exists(sqlite_path):
            db.init_database()

    return app
