import os
import sqlite3
from datetime import datetime, timedelta


def init_database(path, seed=True):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    with open(schema_path, encoding="utf-8") as schema_file:
        connection.executescript(schema_file.read())
    if seed and connection.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0:
        seed_database(connection)
    connection.commit()
    connection.close()


def seed_database(db):
    products = [
        ("ST-COIL-012", "Thép cuộn mạ kẽm", "Nguyên vật liệu", "Cuộn", 35, 10, 12500000),
        ("EL-CABLE-025", "Cáp điện công nghiệp", "Điện tử", "Cuộn", 18, 20, 2450000),
        ("PK-BOX-100", "Thùng carton 5 lớp", "Bao bì", "Thùng", 240, 80, 32000),
        ("MC-BEAR-620", "Vòng bi công nghiệp 6205", "Linh kiện", "Cái", 42, 25, 185000),
        ("SP-OIL-020", "Dầu thủy lực ISO VG 46", "Máy móc", "Can", 9, 12, 950000),
        ("EL-SENS-014", "Cảm biến quang E3Z", "Điện tử", "Cái", 55, 15, 870000),
    ]
    db.executemany(
        "INSERT INTO products(sku,name,category,unit,current_stock,min_stock,unit_price) VALUES(?,?,?,?,?,?,?)",
        products,
    )
    now = datetime.now().replace(microsecond=0)
    receipts = [
        ("NK-DEMO-001", "Công ty Thép Đông Á", "Kho A", now - timedelta(days=20), "completed", "43C-123.45", "TCNU8291024", "Lô thép tháng này"),
        ("NK-DEMO-002", "Samsung SDS Việt Nam", "Kho B", now - timedelta(days=2), "pending", "51D-889.10", "SEGU4812040", ""),
        ("NK-DEMO-003", "Công ty Bao bì Tân Tiến", "Kho A", now, "pending", "60C-456.78", "", "Giao trước 10 giờ"),
    ]
    for code, supplier, warehouse, received_date, status, vehicle, container, note in receipts:
        cursor = db.execute(
            """
            INSERT INTO receipts(code,supplier,warehouse,received_date,status,vehicle_no,container_no,note,created_at,updated_at,completed_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                code,
                supplier,
                warehouse,
                received_date.isoformat(),
                status,
                vehicle,
                container,
                note,
                (received_date - timedelta(days=1)).isoformat(),
                received_date.isoformat(),
                received_date.isoformat() if status == "completed" else None,
            ),
        )
        receipt_id = cursor.lastrowid
        product_id = 1 if code.endswith("001") else (2 if code.endswith("002") else 3)
        qty = 30 if code.endswith("001") else (50 if code.endswith("002") else 120)
        price = products[product_id - 1][-1]
        db.execute(
            "INSERT INTO receipt_items(receipt_id,product_id,planned_qty,actual_qty,unit_price) VALUES(?,?,?,?,?)",
            (receipt_id, product_id, qty, qty if status == "completed" else None, price),
        )
        if status == "completed":
            db.execute(
                "INSERT INTO inspections(receipt_id,checklist_json,result,note,inspected_by,inspected_at) VALUES(?,?,?,?,?,?)",
                (receipt_id, '{"container":"pass","seal":"pass","goods":"pass","packaging":"pass","barcode":"pass","quantity":"pass","condition":"pass"}', "pass", "", "Nguyễn Văn A", received_date.isoformat()),
            )
            db.execute(
                "INSERT INTO stock_movements(product_id,receipt_id,type,quantity,balance_after,reference_code,created_at) VALUES(?,?,'IN',?,?,?,?)",
                (product_id, receipt_id, qty, products[product_id - 1][4], code, received_date.isoformat()),
            )
        db.execute(
            "INSERT INTO audit_logs(action,entity_type,entity_id,details,created_at) VALUES(?,?,?,?,?)",
            (
                "COMPLETE" if status == "completed" else "CREATE",
                "receipt",
                receipt_id,
                ("Hoàn tất nhập kho " if status == "completed" else "Tạo phiếu nhập ") + code,
                received_date.isoformat(),
            ),
        )


if __name__ == "__main__":
    init_database(os.path.join(os.path.dirname(__file__), "instance", "wms.sqlite3"))
    print("WMS database initialized successfully.")
