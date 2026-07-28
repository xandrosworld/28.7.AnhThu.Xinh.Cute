import os
import tempfile
import unittest

from app import CHECKLIST_KEYS, create_app
from database import init_database


class WmsApiTestCase(unittest.TestCase):
    def setUp(self):
        handle, self.database_path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        init_database(self.database_path, seed=True)
        self.app = create_app({"TESTING": True, "DATABASE": self.database_path})
        self.client = self.app.test_client()

    def tearDown(self):
        os.unlink(self.database_path)

    def create_receipt(self):
        response = self.client.post(
            "/api/receipts",
            json={
                "supplier": "Nhà cung cấp kiểm thử",
                "warehouse": "Kho A",
                "received_date": "2026-07-28T10:30",
                "vehicle_no": "43C-000.01",
                "items": [{"product_id": 2, "planned_qty": 7, "unit_price": 1000}],
            },
        )
        self.assertEqual(response.status_code, 201)
        return response.get_json()["id"]

    def test_pages_and_dashboard_are_available(self):
        for path in ("/dashboard", "/receipts", "/receipts/new", "/history", "/reports"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(b"DNP", response.data)
        data = self.client.get("/api/dashboard").get_json()
        self.assertIn("kpis", data)
        self.assertGreaterEqual(data["kpis"]["total_products"], 1)

    def test_create_update_and_delete_receipt(self):
        receipt_id = self.create_receipt()
        detail = self.client.get(f"/api/receipts/{receipt_id}").get_json()
        self.assertEqual(detail["status"], "pending")
        self.assertEqual(len(detail["items"]), 1)

        detail["supplier"] = "Nhà cung cấp đã sửa"
        detail["items"] = [{"product_id": 3, "planned_qty": 9, "unit_price": 2000}]
        response = self.client.put(f"/api/receipts/{receipt_id}", json=detail)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.client.get(f"/api/receipts/{receipt_id}").get_json()["supplier"],
            "Nhà cung cấp đã sửa",
        )
        self.assertEqual(self.client.delete(f"/api/receipts/{receipt_id}").status_code, 200)
        self.assertEqual(self.client.get(f"/api/receipts/{receipt_id}").status_code, 404)

    def test_validation_rejects_invalid_and_duplicate_items(self):
        response = self.client.post(
            "/api/receipts",
            json={
                "supplier": "",
                "warehouse": "Kho A",
                "received_date": "bad-date",
                "items": [
                    {"product_id": 1, "planned_qty": 0, "unit_price": 1},
                    {"product_id": 1, "planned_qty": 2, "unit_price": -1},
                ],
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("bắt buộc", response.get_json()["error"])
        self.assertIn("bị trùng", response.get_json()["error"])

    def test_complete_requires_passing_inspection(self):
        receipt_id = self.create_receipt()
        response = self.client.post(f"/api/receipts/{receipt_id}/complete")
        self.assertEqual(response.status_code, 409)
        self.assertIn("kiểm tra", response.get_json()["error"])

    def test_completion_is_idempotent_and_stock_is_added_once(self):
        receipt_id = self.create_receipt()
        detail = self.client.get(f"/api/receipts/{receipt_id}").get_json()
        item_id = detail["items"][0]["id"]
        product_id = detail["items"][0]["product_id"]
        before = next(
            p["current_stock"] for p in self.client.get("/api/products").get_json() if p["id"] == product_id
        )
        inspection = self.client.post(
            f"/api/receipts/{receipt_id}/inspection",
            json={
                "checklist": {key: "pass" for key in CHECKLIST_KEYS},
                "result": "pass",
                "note": "Đủ tiêu chuẩn",
                "actual_quantities": {str(item_id): 6},
            },
        )
        self.assertEqual(inspection.status_code, 200)

        first = self.client.post(f"/api/receipts/{receipt_id}/complete")
        second = self.client.post(f"/api/receipts/{receipt_id}/complete")
        self.assertEqual(first.status_code, 200)
        self.assertFalse(first.get_json()["already_completed"])
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.get_json()["already_completed"])
        after = next(
            p["current_stock"] for p in self.client.get("/api/products").get_json() if p["id"] == product_id
        )
        self.assertEqual(after, before + 6)
        with self.app.get_db() as db:
            movement_count = db.execute(
                "SELECT COUNT(*) FROM stock_movements WHERE receipt_id=?", (receipt_id,)
            ).fetchone()[0]
        self.assertEqual(movement_count, 1)

    def test_failed_checklist_cannot_be_marked_pass(self):
        receipt_id = self.create_receipt()
        detail = self.client.get(f"/api/receipts/{receipt_id}").get_json()
        checklist = {key: "pass" for key in CHECKLIST_KEYS}
        checklist["seal"] = "fail"
        response = self.client.post(
            f"/api/receipts/{receipt_id}/inspection",
            json={
                "checklist": checklist,
                "result": "pass",
                "actual_quantities": {str(detail["items"][0]["id"]): 7},
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_report_and_utf8_csv_export(self):
        report = self.client.get("/api/reports?start=2000-01-01&end=2100-01-01")
        self.assertEqual(report.status_code, 200)
        self.assertGreaterEqual(report.get_json()["summary"]["receipt_count"], 1)
        export = self.client.get("/reports/export.csv?start=2000-01-01&end=2100-01-01")
        self.assertEqual(export.status_code, 200)
        self.assertTrue(export.data.startswith(b"\xef\xbb\xbf"))
        self.assertIn("text/csv", export.content_type)


if __name__ == "__main__":
    unittest.main()
