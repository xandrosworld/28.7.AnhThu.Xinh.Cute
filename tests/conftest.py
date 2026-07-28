import pytest

from app import create_app


@pytest.fixture()
def app():
    application = create_app({
        "TESTING": True,
        "DATABASE": ":memory:",
    })
    yield application


@pytest.fixture()
def client(app):
    client = app.test_client()
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "Admin@123"},
    )
    assert response.status_code == 200
    client.environ_base["HTTP_X_CSRF_TOKEN"] = response.json["csrf_token"]
    return client


@pytest.fixture()
def anonymous_client(app):
    return app.test_client()


@pytest.fixture()
def db(app):
    with app.app_context():
        yield app.get_db()
