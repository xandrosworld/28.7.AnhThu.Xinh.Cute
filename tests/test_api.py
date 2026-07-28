def product_payload(**overrides):
    data = {
        "sku": "TST-001",
        "barcode": "8930000000001",
        "name": "Hàng kiểm thử",
        "description": "Dữ liệu dùng cho kiểm thử",
        "category_id": 1,
        "unit": "Cái",
        "location": "T-01-01",
        "quantity": 20,
        "min_stock": 5,
        "max_stock": 100,
        "unit_price": 125000,
    }
    data.update(overrides)
    return data


def order_payload(product_id=1, quantity=2):
    return {
        "outbound_date": "2026-07-28",
        "customer_name": "Khách hàng kiểm thử",
        "tax_code": "0401234567",
        "phone": "0905123456",
        "address": "Đà Nẵng",
        "container_no": "",
        "seal_no": "",
        "vehicle_no": "43C-123.45",
        "c_number": "",
        "note": "Phiếu do test tạo",
        "items": [{"product_id": product_id, "quantity": quantity}],
    }


def test_health_and_all_pages_render(client):
    assert client.get("/health").json["status"] == "ok"
    for path in (
        "/", "/hang-hoa", "/hang-hoa/them", "/hang-hoa/1",
        "/hang-hoa/1/sua", "/xuat-kho", "/xuat-kho/tao",
        "/xuat-kho/1", "/xuat-kho/1/sua",
        "/xuat-kho/1/kiem-tra", "/lich-su-xuat-kho",
    ):
        response = client.get(path)
        assert response.status_code == 200
        assert "DNP Logistics" in response.get_data(as_text=True)


def test_product_crud_search_filter_and_validation(client):
    created = client.post("/api/products", json=product_payload())
    assert created.status_code == 201
    product_id = created.json["id"]

    detail = client.get(f"/api/products/{product_id}")
    assert detail.status_code == 200
    assert detail.json["name"] == "Hàng kiểm thử"
    assert detail.json["inventory_value"] == 2_500_000

    search = client.get("/api/products?q=TST-001&category_id=1&status=in_stock")
    assert search.status_code == 200
    assert [item["id"] for item in search.json["items"]] == [product_id]

    updated = client.put(
        f"/api/products/{product_id}",
        json=product_payload(name="Hàng đã cập nhật", quantity=4),
    )
    assert updated.status_code == 200
    assert client.get(f"/api/products/{product_id}").json["status"] == "low_stock"

    invalid = client.post("/api/products", json=product_payload(
        sku="BAD", barcode="8930000000999", quantity=101, max_stock=100,
    ))
    assert invalid.status_code == 400

    deleted = client.delete(f"/api/products/{product_id}")
    assert deleted.status_code == 200
    assert client.get(f"/api/products/{product_id}").status_code == 404


def test_category_crud_and_prevent_delete_when_in_use(client):
    created = client.post("/api/categories", json={
        "code": "TST", "name": "Danh mục test", "description": "Mô tả",
    })
    assert created.status_code == 201
    category_id = created.json["id"]
    assert client.patch(f"/api/categories/{category_id}", json={
        "code": "TST2", "name": "Danh mục test mới", "description": "",
    }).status_code == 200
    product = client.post("/api/products", json=product_payload(
        sku="CAT-001", barcode="8930000000018", category_id=category_id,
    ))
    assert product.status_code == 201
    assert client.delete(f"/api/categories/{category_id}").status_code == 409


def test_order_edit_delete_and_duplicate_line_validation(client):
    duplicate = order_payload()
    duplicate["items"].append({"product_id": 1, "quantity": 1})
    assert client.post("/api/outbound-orders", json=duplicate).status_code == 400

    created = client.post("/api/outbound-orders", json=order_payload())
    assert created.status_code == 201
    order_id = created.json["id"]
    update = order_payload(quantity=3)
    update["customer_name"] = "Khách hàng đã sửa"
    assert client.put(f"/api/outbound-orders/{order_id}", json=update).status_code == 200
    assert client.get(f"/api/outbound-orders/{order_id}").json["total_quantity"] == 3
    assert client.delete(f"/api/outbound-orders/{order_id}").status_code == 200
    assert client.get(f"/api/outbound-orders/{order_id}").status_code == 404


def test_complete_requires_inspection_and_deducts_stock_once(client, db):
    before = db.execute("SELECT quantity FROM products WHERE id=1").fetchone()[0]
    order_id = client.post("/api/outbound-orders", json=order_payload(quantity=4)).json["id"]

    started = client.post(
        f"/api/outbound-orders/{order_id}/status", json={"status": "processing"},
    )
    assert started.status_code == 200
    no_inspection = client.post(
        f"/api/outbound-orders/{order_id}/status", json={"status": "completed"},
    )
    assert no_inspection.status_code == 409
    assert db.execute("SELECT quantity FROM products WHERE id=1").fetchone()[0] == before

    inspection = client.put(f"/api/outbound-orders/{order_id}/inspection", json={
        "items": [{"product_id": 1, "actual_quantity": 4, "condition_ok": True, "note": "Đủ"}],
    })
    assert inspection.status_code == 200
    assert inspection.json["passed"] is True

    completed = client.post(
        f"/api/outbound-orders/{order_id}/status", json={"status": "completed"},
    )
    assert completed.status_code == 200
    assert db.execute("SELECT quantity FROM products WHERE id=1").fetchone()[0] == before - 4
    movement = db.execute(
        "SELECT * FROM stock_movements WHERE order_id=?", (order_id,),
    ).fetchone()
    assert movement["quantity_before"] == before
    assert movement["quantity_after"] == before - 4
    assert movement["quantity_change"] == -4

    repeated = client.post(
        f"/api/outbound-orders/{order_id}/status", json={"status": "completed"},
    )
    assert repeated.status_code == 409
    assert db.execute("SELECT quantity FROM products WHERE id=1").fetchone()[0] == before - 4
    assert db.execute(
        "SELECT COUNT(*) FROM stock_movements WHERE order_id=?", (order_id,),
    ).fetchone()[0] == 1


def test_insufficient_stock_is_atomic_and_never_goes_negative(client, db):
    assert db.execute("SELECT quantity FROM products WHERE id=3").fetchone()[0] == 0
    order_id = client.post(
        "/api/outbound-orders", json=order_payload(product_id=3, quantity=1),
    ).json["id"]
    client.post(f"/api/outbound-orders/{order_id}/status", json={"status": "processing"})
    inspection = client.put(f"/api/outbound-orders/{order_id}/inspection", json={
        "items": [{"product_id": 3, "actual_quantity": 1, "condition_ok": True, "note": ""}],
    })
    assert inspection.status_code == 200
    assert inspection.json["passed"] is False
    completion = client.post(
        f"/api/outbound-orders/{order_id}/status", json={"status": "completed"},
    )
    assert completion.status_code == 409
    assert db.execute("SELECT quantity FROM products WHERE id=3").fetchone()[0] == 0
    assert db.execute(
        "SELECT COUNT(*) FROM stock_movements WHERE order_id=?", (order_id,),
    ).fetchone()[0] == 0
    assert client.get(f"/api/outbound-orders/{order_id}").json["status"] == "processing"


def test_cannot_modify_completed_order(client):
    order_id = client.post("/api/outbound-orders", json=order_payload()).json["id"]
    client.post(f"/api/outbound-orders/{order_id}/status", json={"status": "processing"})
    client.put(f"/api/outbound-orders/{order_id}/inspection", json={
        "items": [{"product_id": 1, "actual_quantity": 2, "condition_ok": True, "note": ""}],
    })
    client.post(f"/api/outbound-orders/{order_id}/status", json={"status": "completed"})
    assert client.put(f"/api/outbound-orders/{order_id}", json=order_payload()).status_code == 409
    assert client.delete(f"/api/outbound-orders/{order_id}").status_code == 409
