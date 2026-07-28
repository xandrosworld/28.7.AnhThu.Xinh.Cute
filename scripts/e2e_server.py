"""Run an isolated, seeded WMS instance for Playwright.

The database lives in an operating-system temporary directory and is removed
when the web server exits, so E2E runs never mutate the developer demo data.
"""

import os
import signal
import sys
import tempfile
import threading
from pathlib import Path

from werkzeug.serving import make_server

# Running a file under ``scripts/`` puts that directory first on sys.path.
# Pin the repository root so an unrelated globally importable ``app`` module
# cannot shadow this project's Flask package.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import create_app
from app.db import init_database
from app.extensions import db as orm


def main():
    temp_dir = tempfile.TemporaryDirectory(prefix="dnp-wms-playwright-")
    database_path = Path(temp_dir.name, "e2e.sqlite")
    app = create_app(
        {
            "TESTING": False,
            "SECRET_KEY": "playwright-isolated-secret",
            "DATABASE": str(database_path),
            "AUTO_INIT_DB": False,
        }
    )
    with app.app_context():
        init_database()

    host = os.environ.get("E2E_HOST", "127.0.0.1")
    port = int(os.environ.get("E2E_PORT", "5000"))
    server = make_server(host, port, app, threaded=True)

    def stop_server(*_args):
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, stop_server)
    signal.signal(signal.SIGTERM, stop_server)
    try:
        server.serve_forever()
    finally:
        with app.app_context():
            orm.session.remove()
            orm.engine.dispose()
        temp_dir.cleanup()


if __name__ == "__main__":
    main()
