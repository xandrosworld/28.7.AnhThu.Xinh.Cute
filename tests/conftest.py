import pytest

from app import create_app


@pytest.fixture()
def app(tmp_path):
    application = create_app({
        "TESTING": True,
        "DATABASE": str(tmp_path / "test.sqlite3"),
    })
    yield application


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def db(app):
    with app.app_context():
        yield app.get_db()
