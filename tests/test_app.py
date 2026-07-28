import os
import sqlite3
import tempfile
import unittest

from app import CHECKLIST_KEYS, create_app
from database import init_database


class WmsAcceptanceTestCase(unittest.TestCase):
    def setUp(self):
        handle, self.database_path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        init_database(self.database_path, seed=True)
        self.app = create_app(
            {"TESTING": True, "DATABASE": self.database_path, "SECRET_KEY": "test-secret"}
        )
        self.client = self.app.test_client()

    def tearDown(self):
        os.unlink(self.database_path)

    def login(self, username="admin", password="Admin@123", client=None):
        client = client or self.client
        response = client.post(
            "/api/auth/login", json={"username": username, "password": password}
        )
        data = response.get_json() or {}
        return response, data.get("csrf_token")

    def product(self, product_id=2):
        return next(
            item
            for item in self.client.get("/api/products").get_json()
            if item["id"] == product_id
        )

    def receipt_payload(self, pallet_id="PLT-TEST-001", product_id=2):
        product = self.product(product_id)
        return {
            "supplier": "Công ty Thép Đông Á",
            "warehouse": "Kho A",
            "received_date": "2026-07-28T10:30",
            "vehicle_no": "43C-000.01",
            "container_no": "CONT-001",
            "seal_no": "SEAL-001",
            "items": [
                {
                    "product_id": product_id,
                    "planned_qty": 7,
                    "unit_price": 1000,
                    "pallet_id": pallet_id,
                    "barcode": product["barcode"],
                    "expiry_date": "2027-07-28",
                }
            ],
        }

    def create_receipt(self, csrf, pallet_id="PLT-TEST-001"):
        response = self.client.post(
            "/api/receipts",
            json=self.receipt_payload(pallet_id),
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()["id"]

    def inspect(self, receipt_id, csrf, accepted=6, rejected=1, reason="Móp bao bì"):
        detail = self.client.get(f"/api/receipts/{receipt_id}").get_json()
        item_id = detail["items"][0]["id"]
        return self.client.post(
            f"/api/receipts/{receipt_id}/inspection",
            headers={"X-CSRF-Token": csrf},
            json={
                "checklist": {key: "pass" for key in CHECKLIST_KEYS},
                "result": "pass",
                "note": "Đã kiểm đếm thực tế",
                "actual_quantities": {str(item_id): accepted},
                "rejected_quantities": {str(item_id): rejected},
                "rejection_reasons": {str(item_id): reason},
                "scanned_barcodes": {str(item_id): detail["items"][0]["barcode"]},
            },
        )

    def test_authentication_locked_account_and_protected_resources(self):
        self.assertEqual(self.client.get("/login").status_code, 200)
        self.assertEqual(self.client.get("/api/dashboard").status_code, 401)
        self.assertEqual(self.client.get("/dashboard").status_code, 302)
        self.assertEqual(self.client.post("/api/auth/login", json={}).status_code, 422)
        self.assertEqual(
            self.client.post(
                "/api/auth/login", json={"username": "admin", "password": "wrong"}
            ).status_code,
            401,
        )
        locked, _ = self.login("locked", "Locked@123")
        self.assertEqual(locked.status_code, 403)
        success, token = self.login()
        self.assertEqual(success.status_code, 200)
        self.assertTrue(token)
        self.assertEqual(self.client.get("/api/auth/me").get_json()["user"]["role"], "ADMIN")

    def test_pages_render_and_errors_have_expected_shape(self):
        self.login()
        for path in (
            "/dashboard", "/receipts", "/receipts/new", "/receipts/2",
            "/receipts/2/edit", "/receipts/2/inspect", "/history", "/reports",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(b"DNP", response.data)
        api_missing = self.client.get("/api/not-found")
        self.assertEqual(api_missing.status_code, 404)
        self.assertIn("error", api_missing.get_json())
        page_missing = self.client.get("/not-found")
        self.assertEqual(page_missing.status_code, 404)

    def test_csrf_logout_and_security_headers(self):
        _, token = self.login()
        no_token = self.client.post("/api/receipts", json={})
        self.assertEqual(no_token.status_code, 403)
        response = self.client.get("/api/dashboard")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        logout = self.client.post(
            "/api/auth/logout", json={}, headers={"X-CSRF-Token": token}
        )
        self.assertEqual(logout.status_code, 200)
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)

    def test_rbac_separates_cs_and_warehouse_duties(self):
        _, cs_token = self.login("cs", "CS@12345")
        receipt_id = self.create_receipt(cs_token)
        forbidden = self.inspect(receipt_id, cs_token)
        self.assertEqual(forbidden.status_code, 403)

        warehouse_client = self.app.test_client()
        _, warehouse_token = self.login(
            "warehouse", "Kho@12345", client=warehouse_client
        )
        create_forbidden = warehouse_client.post(
            "/api/receipts",
            json=self.receipt_payload("PLT-WH-001"),
            headers={"X-CSRF-Token": warehouse_token},
        )
        self.assertEqual(create_forbidden.status_code, 403)
        detail = warehouse_client.get(f"/api/receipts/{receipt_id}").get_json()
        item_id = detail["items"][0]["id"]
        inspection = warehouse_client.post(
            f"/api/receipts/{receipt_id}/inspection",
            headers={"X-CSRF-Token": warehouse_token},
            json={
                "checklist": {key: "pass" for key in CHECKLIST_KEYS},
                "result": "pass",
                "actual_quantities": {str(item_id): 7},
                "scanned_barcodes": {str(item_id): detail["items"][0]["barcode"]},
            },
        )
        self.assertEqual(inspection.status_code, 200, inspection.get_json())

    def test_master_data_and_inventory_search(self):
        self.login()
        master = self.client.get("/api/master-data")
        self.assertEqual(master.status_code, 200)
        self.assertGreaterEqual(len(master.get_json()["suppliers"]), 3)
        self.assertGreaterEqual(len(master.get_json()["warehouses"]), 3)
        products = self.client.get("/api/products").get_json()
        self.assertTrue(all(item["barcode"] and item["unit"] for item in products))
        lots = self.client.get("/api/inventory?q=PLT-NK-DEMO-001").get_json()
        self.assertEqual(len(lots), 1)
        self.assertEqual(lots[0]["unit"], "Cuộn")

    def test_create_update_delete_preserves_pallet_barcode_unit(self):
        _, token = self.login("cs", "CS@12345")
        receipt_id = self.create_receipt(token)
        detail = self.client.get(f"/api/receipts/{receipt_id}").get_json()
        self.assertEqual(detail["status"], "pending")
        self.assertEqual(detail["seal_no"], "SEAL-001")
        self.assertEqual(detail["items"][0]["pallet_id"], "PLT-TEST-001")
        self.assertEqual(detail["items"][0]["unit"], "Cuộn")

        payload = self.receipt_payload("PLT-TEST-UPDATED", product_id=3)
        payload["supplier"] = "Công ty Bao bì Tân Tiến"
        updated = self.client.put(
            f"/api/receipts/{receipt_id}",
            json=payload,
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(updated.status_code, 200, updated.get_json())
        self.assertEqual(
            self.client.get(f"/api/receipts/{receipt_id}").get_json()["items"][0]["unit"],
            "Thùng",
        )
        self.assertEqual(
            self.client.delete(
                f"/api/receipts/{receipt_id}", headers={"X-CSRF-Token": token}
            ).status_code,
            200,
        )

    def test_receipt_validation_and_database_uniqueness(self):
        _, token = self.login("cs", "CS@12345")
        payload = self.receipt_payload()
        payload["items"].append(dict(payload["items"][0]))
        invalid = self.client.post(
            "/api/receipts", json=payload, headers={"X-CSRF-Token": token}
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertIn("trùng", invalid.get_json()["error"])

        wrong_barcode = self.receipt_payload("PLT-WRONG")
        wrong_barcode["items"][0]["barcode"] = "0000000000000"
        mismatch = self.client.post(
            "/api/receipts", json=wrong_barcode, headers={"X-CSRF-Token": token}
        )
        self.assertEqual(mismatch.status_code, 409)

        unknown_supplier = self.receipt_payload("PLT-UNKNOWN-SUPPLIER")
        unknown_supplier["supplier"] = "Nhà cung cấp không có trong master"
        rejected_master = self.client.post(
            "/api/receipts", json=unknown_supplier, headers={"X-CSRF-Token": token}
        )
        self.assertEqual(rejected_master.status_code, 400)
        self.assertIn("không tồn tại", rejected_master.get_json()["error"])

        self.create_receipt(token, "PLT-UNIQUE")
        duplicate_pallet = self.client.post(
            "/api/receipts",
            json=self.receipt_payload("PLT-UNIQUE"),
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(duplicate_pallet.status_code, 409)

    def test_inspection_validates_checklist_and_rejection_reason(self):
        _, token = self.login()
        receipt_id = self.create_receipt(token)
        detail = self.client.get(f"/api/receipts/{receipt_id}").get_json()
        item_id = detail["items"][0]["id"]
        incomplete = self.client.post(
            f"/api/receipts/{receipt_id}/inspection",
            json={"checklist": {}, "result": "pass"},
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(incomplete.status_code, 400)
        no_reason = self.inspect(receipt_id, token, accepted=6, rejected=1, reason="")
        self.assertEqual(no_reason.status_code, 422)
        over = self.inspect(receipt_id, token, accepted=7, rejected=1)
        self.assertEqual(over.status_code, 400)
        mismatch = self.client.post(
            f"/api/receipts/{receipt_id}/inspection",
            headers={"X-CSRF-Token": token},
            json={
                "checklist": {key: "pass" for key in CHECKLIST_KEYS},
                "result": "pass",
                "actual_quantities": {str(item_id): 7},
                "scanned_barcodes": {str(item_id): "WRONG-BARCODE"},
            },
        )
        self.assertEqual(mismatch.status_code, 422)

    def test_completion_is_idempotent_and_only_accepted_stock_is_added(self):
        _, token = self.login()
        receipt_id = self.create_receipt(token)
        detail = self.client.get(f"/api/receipts/{receipt_id}").get_json()
        product_id = detail["items"][0]["product_id"]
        before = self.product(product_id)["current_stock"]
        self.assertEqual(self.inspect(receipt_id, token).status_code, 200)

        first = self.client.post(
            f"/api/receipts/{receipt_id}/complete",
            json={},
            headers={"X-CSRF-Token": token},
        )
        second = self.client.post(
            f"/api/receipts/{receipt_id}/complete",
            json={},
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(first.status_code, 200)
        self.assertFalse(first.get_json()["already_completed"])
        self.assertTrue(second.get_json()["already_completed"])
        self.assertEqual(self.product(product_id)["current_stock"], before + 6)
        with self.app.get_db() as db:
            movement_count = db.execute(
                "SELECT COUNT(*) FROM stock_movements WHERE receipt_id=?", (receipt_id,)
            ).fetchone()[0]
            lot = db.execute(
                "SELECT quantity,pallet_id,unit FROM inventory_lots WHERE receipt_item_id=?",
                (detail["items"][0]["id"],),
            ).fetchone()
        self.assertEqual(movement_count, 1)
        self.assertEqual(lot["quantity"], 6)
        self.assertEqual(lot["pallet_id"], "PLT-TEST-001")
        self.assertEqual(lot["unit"], detail["items"][0]["unit"])

    def test_failed_inspection_cannot_complete_or_change_stock(self):
        _, token = self.login()
        receipt_id = self.create_receipt(token)
        detail = self.client.get(f"/api/receipts/{receipt_id}").get_json()
        item_id = detail["items"][0]["id"]
        checklist = {key: "pass" for key in CHECKLIST_KEYS}
        checklist["seal"] = "fail"
        inspection = self.client.post(
            f"/api/receipts/{receipt_id}/inspection",
            headers={"X-CSRF-Token": token},
            json={
                "checklist": checklist,
                "result": "fail",
                "actual_quantities": {str(item_id): 0},
                "rejected_quantities": {str(item_id): 7},
                "rejection_reasons": {str(item_id): "Seal bị rách"},
            },
        )
        self.assertEqual(inspection.status_code, 200)
        complete = self.client.post(
            f"/api/receipts/{receipt_id}/complete",
            json={},
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(complete.status_code, 409)

    def test_completed_receipt_is_immutable(self):
        _, token = self.login()
        receipt_id = self.create_receipt(token)
        self.assertEqual(self.inspect(receipt_id, token, accepted=7, rejected=0).status_code, 200)
        self.client.post(
            f"/api/receipts/{receipt_id}/complete",
            json={},
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(
            self.client.put(
                f"/api/receipts/{receipt_id}",
                json=self.receipt_payload("PLT-OTHER"),
                headers={"X-CSRF-Token": token},
            ).status_code,
            409,
        )
        self.assertEqual(
            self.client.delete(
                f"/api/receipts/{receipt_id}", headers={"X-CSRF-Token": token}
            ).status_code,
            409,
        )

    def test_filters_reports_csv_and_audit_history(self):
        self.login()
        self.assertEqual(self.client.get("/api/receipts?status=unknown").status_code, 400)
        filtered = self.client.get("/api/receipts?q=NK-DEMO&status=completed")
        self.assertEqual(filtered.status_code, 200)
        self.assertTrue(filtered.get_json())
        self.assertEqual(
            self.client.get("/api/reports?start=2026-12-31&end=2026-01-01").status_code,
            400,
        )
        report = self.client.get("/api/reports?start=2000-01-01&end=2100-01-01")
        self.assertGreaterEqual(report.get_json()["summary"]["receipt_count"], 1)
        no_rows = self.client.get(
            "/api/reports?start=2000-01-01&end=2100-01-01&warehouse=Kho%20C"
        )
        self.assertEqual(no_rows.get_json()["summary"]["receipt_count"], 0)
        export = self.client.get("/reports/export.csv?start=2000-01-01&end=2100-01-01")
        self.assertTrue(export.data.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(export.content_type, "text/csv; charset=utf-8")
        decoded = export.data.decode("utf-8-sig")
        self.assertIn("Pallet ID", decoded)
        self.assertIn("Barcode", decoded)
        history = self.client.get("/api/history?action=COMPLETE")
        self.assertEqual(history.status_code, 200)
        self.assertTrue(history.get_json())

    def test_sql_constraints_reject_duplicate_barcode(self):
        self.login()
        with self.assertRaises(sqlite3.IntegrityError):
            with self.app.get_db() as db:
                db.execute(
                    """INSERT INTO products
                       (sku,name,category,unit,barcode,current_stock,min_stock,unit_price)
                       VALUES('X','X','X','Cái','8938500120012',0,0,0)"""
                )

    def test_backup_and_restore_cli_round_trip(self):
        backup = f"{self.database_path}.backup"
        try:
            runner = self.app.test_cli_runner()
            result = runner.invoke(args=["backup-db", "--destination", backup])
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertTrue(os.path.exists(backup))
            with self.app.get_db() as db:
                db.execute("UPDATE products SET current_stock=999 WHERE id=1")
            restored = runner.invoke(args=["restore-db", "--source", backup])
            self.assertEqual(restored.exit_code, 0, restored.output)
            with self.app.get_db() as db:
                self.assertNotEqual(
                    db.execute("SELECT current_stock FROM products WHERE id=1").fetchone()[0],
                    999,
                )
        finally:
            if os.path.exists(backup):
                os.unlink(backup)

    def test_malformed_payloads_numbers_dates_and_api_errors(self):
        _, token = self.login()
        headers = {"X-CSRF-Token": token}

        malformed = self.receipt_payload("PLT-MALFORMED")
        malformed["note"] = 17
        self.assertEqual(
            self.client.post("/api/receipts", json=malformed, headers=headers).status_code,
            400,
        )

        fractional_id = self.receipt_payload("PLT-FRACTIONAL")
        fractional_id["items"][0]["product_id"] = 2.9
        self.assertEqual(
            self.client.post("/api/receipts", json=fractional_id, headers=headers).status_code,
            400,
        )

        infinite = self.receipt_payload("PLT-INFINITE")
        infinite["items"][0]["planned_qty"] = float("inf")
        self.assertEqual(
            self.client.post("/api/receipts", json=infinite, headers=headers).status_code,
            400,
        )

        self.assertEqual(
            self.client.get("/api/receipts?start=not-a-date").status_code, 400
        )
        self.assertEqual(
            self.client.get(
                "/api/receipts?start=2026-12-31&end=2026-01-01"
            ).status_code,
            400,
        )
        wrong_method = self.client.post("/api/products", json={}, headers=headers)
        self.assertEqual(wrong_method.status_code, 405)
        self.assertTrue(wrong_method.is_json)

    def test_restore_rejects_non_wms_sqlite_without_touching_target(self):
        handle, wrong_database = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        try:
            db = sqlite3.connect(wrong_database)
            try:
                db.execute("CREATE TABLE unrelated(value TEXT)")
                db.commit()
            finally:
                db.close()
            with self.app.get_db() as db:
                before = db.execute("SELECT COUNT(*) FROM products").fetchone()[0]
            result = self.app.test_cli_runner().invoke(
                args=["restore-db", "--source", wrong_database]
            )
            self.assertNotEqual(result.exit_code, 0)
            with self.app.get_db() as db:
                after = db.execute("SELECT COUNT(*) FROM products").fetchone()[0]
            self.assertEqual(after, before)
        finally:
            os.unlink(wrong_database)

    def test_legacy_migration_allows_zero_accepted_quantity(self):
        handle, legacy_path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        legacy_schema = """
            CREATE TABLE products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sku TEXT NOT NULL UNIQUE, name TEXT NOT NULL, category TEXT NOT NULL,
                unit TEXT NOT NULL, current_stock REAL NOT NULL DEFAULT 0,
                min_stock REAL NOT NULL DEFAULT 0, unit_price REAL NOT NULL DEFAULT 0
            );
            CREATE TABLE receipts (
                id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL UNIQUE,
                supplier TEXT NOT NULL, warehouse TEXT NOT NULL, received_date TEXT NOT NULL,
                status TEXT NOT NULL, vehicle_no TEXT DEFAULT '', container_no TEXT DEFAULT '',
                note TEXT DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                completed_at TEXT
            );
            CREATE TABLE receipt_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                receipt_id INTEGER NOT NULL REFERENCES receipts(id) ON DELETE CASCADE,
                product_id INTEGER NOT NULL REFERENCES products(id),
                planned_qty REAL NOT NULL CHECK(planned_qty > 0),
                actual_qty REAL CHECK(actual_qty > 0),
                unit_price REAL NOT NULL DEFAULT 0 CHECK(unit_price >= 0),
                UNIQUE(receipt_id, product_id)
            );
        """
        try:
            db = sqlite3.connect(legacy_path)
            try:
                db.executescript(legacy_schema)
                db.execute(
                    """INSERT INTO products
                       (sku,name,category,unit,current_stock,min_stock,unit_price)
                       VALUES('LEG-1','Legacy','Legacy','Cái',0,0,1)"""
                )
                db.execute(
                    """INSERT INTO receipts
                       (code,supplier,warehouse,received_date,status,created_at,updated_at)
                       VALUES('LEG-R','Công ty Thép Đông Á','Kho A','2026-01-01',
                              'pending','2026-01-01','2026-01-01')"""
                )
                db.execute(
                    """INSERT INTO receipt_items
                       (receipt_id,product_id,planned_qty,actual_qty,unit_price)
                       VALUES(1,1,7,NULL,1)"""
                )
                db.commit()
            finally:
                db.close()
            init_database(legacy_path)
            db = sqlite3.connect(legacy_path)
            try:
                db.execute("UPDATE receipt_items SET actual_qty=0 WHERE id=1")
                pallet, barcode, unit = db.execute(
                    "SELECT pallet_id,barcode,unit FROM receipt_items WHERE id=1"
                ).fetchone()
                db.commit()
            finally:
                db.close()
            self.assertEqual(pallet, "LEGACY-PLT-1")
            self.assertEqual(barcode, "LEGACY-LEG-1")
            self.assertEqual(unit, "Cái")
        finally:
            os.unlink(legacy_path)


if __name__ == "__main__":
    unittest.main()
