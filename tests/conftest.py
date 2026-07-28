import os
import tempfile

import pytest

from app import create_app
from app.db import get_db, init_database
from app.extensions import db as orm


@pytest.fixture()
def app():
    test_database_url = os.environ.get("TEST_DATABASE_URL", "").strip()
    if test_database_url:
        app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-secret",
                "SQLALCHEMY_DATABASE_URI": test_database_url,
                "AUTO_INIT_DB": False,
            }
        )
        with app.app_context():
            init_database()
        yield app
        with app.app_context():
            orm.session.remove()
            orm.drop_all()
        return

    handle, database_path = tempfile.mkstemp(suffix=".sqlite")
    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "DATABASE": database_path,
            "AUTO_INIT_DB": False,
        }
    )
    with app.app_context():
        init_database()
    yield app
    with app.app_context():
        orm.session.remove()
        orm.engine.dispose()
    os.close(handle)
    os.unlink(database_path)


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def runner(app):
    return app.test_cli_runner()


@pytest.fixture()
def db(app):
    with app.app_context():
        yield get_db()


def login(client, username="admin", password="Admin@123"):
    response = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    data = response.get_json()
    return response, data.get("csrf_token") if data else None


@pytest.fixture()
def admin_login(client):
    return login(client)


@pytest.fixture()
def manager_login(client):
    return login(client, "quanlykho", "Kho@12345")


@pytest.fixture()
def staff_login(client):
    return login(client, "nhanvien", "NV@123456")
