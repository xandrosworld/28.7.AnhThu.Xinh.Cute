"""Reproducible NFR-005 benchmark on an isolated SQLite database.

The benchmark never reads or changes the application's normal database. It
creates a temporary database, loads a substantial deterministic dataset, calls
common Flask API endpoints through the test client, prints latency statistics,
and exits non-zero if any measured request reaches the configured threshold.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import sqlite3
import statistics
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from importlib.metadata import version
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_app
from app.db import init_database
from app.extensions import db as orm


ENDPOINTS = (
    ("dashboard", "/api/dashboard"),
    ("inventory_page", "/api/inventory?page=1&per_page=50"),
    (
        "inventory_search",
        "/api/inventory?search=BENCH-SKU-000999&page=1&per_page=50",
    ),
    ("product_search", "/api/products?search=BENCH-SKU-000999"),
    (
        "report_summary",
        "/api/reports/summary?from=2020-01-01&to=2030-12-31",
    ),
    (
        "report_filtered",
        "/api/reports/summary?from=2020-01-01&to=2030-12-31"
        "&warehouse_id=1&product_id=1&customer_id=1",
    ),
    (
        "report_csv",
        "/api/reports/export.csv?from=2020-01-01&to=2030-12-31",
    ),
)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--products", type=int, default=1000)
    parser.add_argument("--lots", type=int, default=5000)
    parser.add_argument("--movements", type=int, default=5000)
    parser.add_argument("--warmups", type=int, default=2)
    parser.add_argument("--iterations", type=int, default=7)
    parser.add_argument("--threshold", type=float, default=5.0, help="Seconds")
    parser.add_argument(
        "--json-output",
        type=Path,
        help="Optional path for machine-readable evidence.",
    )
    args = parser.parse_args()
    if args.products < 1:
        parser.error("--products must be at least 1")
    if args.lots < 5000:
        parser.error("--lots must be at least 5000 for NFR-005 evidence")
    if args.movements < 5000:
        parser.error("--movements must be at least 5000 for NFR-005 evidence")
    if args.warmups < 0 or args.iterations < 3:
        parser.error("Use at least 0 warmups and 3 measured iterations")
    if args.threshold <= 0:
        parser.error("--threshold must be positive")
    return args


def seed_large_dataset(database_path: Path, products: int, lots: int, movements: int):
    started = perf_counter()
    timestamp = "2026-07-28 08:00:00"
    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        unit_id = connection.execute(
            "SELECT id FROM units WHERE code='THUNG'"
        ).fetchone()[0]
        warehouse_row = connection.execute(
            "SELECT id FROM warehouses WHERE UPPER(code)='DN' ORDER BY id"
        ).fetchone()
        if warehouse_row is None:
            raise RuntimeError("Benchmark requires the seeded Da Nang warehouse")
        warehouse_id = warehouse_row[0]
        connection.executemany(
            """
            INSERT INTO inventory
                (sku, barcode, name, category_id, warehouse_id, unit, unit_id, quantity,
                 min_quantity, location, description, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, 10, ?, ?, 'active', ?, ?)
            """,
            [
                (
                    f"BENCH-SKU-{index:06d}",
                    f"BENCH-BAR-{index:06d}",
                    f"Hàng benchmark {index:06d}",
                    (index % 6) + 1,
                    warehouse_id,
                    "Thùng",
                    unit_id,
                    "",
                    "Dữ liệu tổng hợp chỉ dùng cho benchmark NFR-005",
                    timestamp,
                    timestamp,
                )
                for index in range(1, products + 1)
            ],
        )
        product_rows = connection.execute(
            """
            SELECT id, warehouse_id, unit, quantity
            FROM inventory
            WHERE sku LIKE 'BENCH-SKU-%'
            ORDER BY id
            """
        ).fetchall()
        connection.executemany(
            """
            INSERT INTO inventory_lots
                (product_id, warehouse_id, unit, pallet_id, barcode, quantity,
                 expiry_date, received_at, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, NULL, 0, NULL, ?, 'depleted', ?, ?)
            """,
            [
                (
                    product_rows[index % len(product_rows)][0],
                    product_rows[index % len(product_rows)][1],
                    product_rows[index % len(product_rows)][2],
                    f"BENCH-PALLET-{index + 1:07d}",
                    timestamp,
                    timestamp,
                    timestamp,
                )
                for index in range(lots)
            ],
        )
        movement_start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        connection.executemany(
            """
            INSERT INTO stock_movements
                (inventory_id, movement_type, reference_code, quantity_change,
                 balance_after, pallet_id, reason, created_by, created_at)
            VALUES (?, 'adjustment', ?, 0, ?, ?, ?, 1, ?)
            """,
            [
                (
                    product_rows[index % len(product_rows)][0],
                    f"BENCH-MOV-{index + 1:07d}",
                    product_rows[index % len(product_rows)][3],
                    f"BENCH-MOV-PALLET-{index + 1:07d}",
                    "Benchmark hiệu năng NFR-005",
                    (
                        movement_start + timedelta(minutes=index)
                    ).strftime("%Y-%m-%d %H:%M:%S"),
                )
                for index in range(movements)
            ],
        )
        connection.commit()
        counts = {
            "products": connection.execute(
                "SELECT COUNT(*) FROM inventory"
            ).fetchone()[0],
            "benchmark_products": connection.execute(
                "SELECT COUNT(*) FROM inventory WHERE sku LIKE 'BENCH-SKU-%'"
            ).fetchone()[0],
            "lots": connection.execute(
                "SELECT COUNT(*) FROM inventory_lots"
            ).fetchone()[0],
            "movements": connection.execute(
                "SELECT COUNT(*) FROM stock_movements"
            ).fetchone()[0],
        }
    finally:
        connection.close()
    return counts, perf_counter() - started


def percentile_95(values):
    ordered = sorted(values)
    return ordered[max(math.ceil(len(ordered) * 0.95) - 1, 0)]


def measure_endpoint(client, name, path, warmups, iterations):
    for _ in range(warmups):
        response = client.get(path)
        if response.status_code != 200:
            raise RuntimeError(
                f"Warmup {name} returned HTTP {response.status_code}: "
                f"{response.get_data(as_text=True)[:300]}"
            )

    timings = []
    response_bytes = 0
    for _ in range(iterations):
        started = perf_counter()
        response = client.get(path)
        elapsed = perf_counter() - started
        if response.status_code != 200:
            raise RuntimeError(
                f"{name} returned HTTP {response.status_code}: "
                f"{response.get_data(as_text=True)[:300]}"
            )
        response_bytes = len(response.data)
        timings.append(elapsed)
    return {
        "name": name,
        "path": path,
        "samples_seconds": [round(value, 6) for value in timings],
        "min_seconds": round(min(timings), 6),
        "median_seconds": round(statistics.median(timings), 6),
        "p95_seconds": round(percentile_95(timings), 6),
        "max_seconds": round(max(timings), 6),
        "response_bytes": response_bytes,
    }


def print_report(evidence):
    print("DNP WMS - NFR-005 PERFORMANCE EVIDENCE")
    print(
        f"Python {evidence['environment']['python']} | "
        f"SQLite {evidence['environment']['sqlite']} | "
        f"CPUs {evidence['environment']['cpu_count']}"
    )
    print(f"Platform: {evidence['environment']['platform']}")
    print(
        "Dataset: "
        f"{evidence['dataset']['products']} products, "
        f"{evidence['dataset']['lots']} lots, "
        f"{evidence['dataset']['movements']} movements; "
        f"DB {evidence['database_bytes'] / 1024 / 1024:.2f} MiB"
    )
    print(
        f"Warmups: {evidence['warmups']} | "
        f"Measured iterations: {evidence['iterations']} | "
        f"Threshold: < {evidence['threshold_seconds']:.3f}s"
    )
    print("")
    print(
        f"{'Endpoint':20} {'median(ms)':>12} {'p95(ms)':>10} "
        f"{'max(ms)':>10} {'bytes':>10} {'result':>8}"
    )
    print("-" * 76)
    for item in evidence["results"]:
        result = "PASS" if item["max_seconds"] < evidence["threshold_seconds"] else "FAIL"
        print(
            f"{item['name']:20} "
            f"{item['median_seconds'] * 1000:12.2f} "
            f"{item['p95_seconds'] * 1000:10.2f} "
            f"{item['max_seconds'] * 1000:10.2f} "
            f"{item['response_bytes']:10d} {result:>8}"
        )
    print("")
    print(f"VERDICT: {evidence['verdict']}")


def main():
    args = parse_args()
    with tempfile.TemporaryDirectory(prefix="dnp-wms-benchmark-") as temp_directory:
        database_path = Path(temp_directory) / "benchmark.sqlite"
        app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "benchmark-only-secret",
                "DATABASE": str(database_path),
                "AUTO_INIT_DB": False,
            }
        )
        with app.app_context():
            init_database()
            orm.session.remove()
            orm.engine.dispose()

        counts, seed_seconds = seed_large_dataset(
            database_path, args.products, args.lots, args.movements
        )
        database_bytes = database_path.stat().st_size

        with app.test_client() as client:
            login = client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "Admin@123"},
            )
            if login.status_code != 200:
                raise RuntimeError(f"Benchmark login failed: HTTP {login.status_code}")
            results = [
                measure_endpoint(
                    client, name, path, args.warmups, args.iterations
                )
                for name, path in ENDPOINTS
            ]

        with app.app_context():
            orm.session.remove()
            orm.engine.dispose()

        passed = all(
            item["max_seconds"] < args.threshold for item in results
        )
        evidence = {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "environment": {
                "python": platform.python_version(),
                "python_implementation": platform.python_implementation(),
                "sqlite": sqlite3.sqlite_version,
                "flask": version("Flask"),
                "sqlalchemy": version("SQLAlchemy"),
                "platform": platform.platform(),
                "processor": platform.processor(),
                "cpu_count": os.cpu_count(),
            },
            "dataset": counts,
            "database_bytes": database_bytes,
            "seed_seconds": round(seed_seconds, 6),
            "warmups": args.warmups,
            "iterations": args.iterations,
            "threshold_seconds": args.threshold,
            "measurement_scope": (
                "Flask test client; includes routing, authentication, SQL, "
                "serialization and response construction; excludes network latency"
            ),
            "results": results,
            "verdict": "PASS" if passed else "FAIL",
        }
        print_report(evidence)
        if args.json_output:
            output = args.json_output.resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(f"JSON evidence: {output}")
        return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
