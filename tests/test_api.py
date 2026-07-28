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
    for path in (
        "/xuat-kho/tao", "/xuat-kho/1/sua", "/xuat-kho/1/kiem-tra",
    ):
        assert "Quy trình phiếu xuất cũ đã ngừng" in client.get(path).get_data(as_text=True)


def test_product_crud_search_filter_and_validation(client, db):
    created = client.post("/api/products", json=product_payload())
    assert created.status_code == 201
    product_id = created.json["id"]
    assert db.execute(
        "SELECT SUM(quantity) FROM inventory_lots WHERE product_id=? AND active=1",
        (product_id,),
    ).fetchone()[0] == 20

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
    assert db.execute(
        "SELECT SUM(quantity) FROM inventory_lots WHERE product_id=? AND active=1",
        (product_id,),
    ).fetchone()[0] == 4

    invalid = client.post("/api/products", json=product_payload(
        sku="BAD", barcode="8930000000999", quantity=101, max_stock=100,
    ))
    assert invalid.status_code == 400

    deleted = client.delete(f"/api/products/{product_id}")
    assert deleted.status_code == 200
    assert client.get(f"/api/products/{product_id}").status_code == 404
    assert db.execute(
        "SELECT COUNT(*) FROM inventory_lots WHERE product_id=?", (product_id,)
    ).fetchone()[0] == 0


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


def test_legacy_outbound_get_endpoints_remain_read_only(client):
    listing = client.get("/api/outbound-orders")
    assert listing.status_code == 200
    assert listing.json["items"]
    order_id = listing.json["items"][0]["id"]
    assert client.get("/api/outbound-orders/stats").status_code == 200
    assert client.get(f"/api/outbound-orders/{order_id}").status_code == 200
    assert client.get("/api/outbound-history").status_code == 200


def test_legacy_outbound_mutations_return_410_without_writes(client, db):
    order_id = db.execute("SELECT id FROM outbound_orders ORDER BY id LIMIT 1").fetchone()[0]
    before = {
        "orders": db.execute("SELECT COUNT(*) FROM outbound_orders").fetchone()[0],
        "status": db.execute(
            "SELECT status FROM outbound_orders WHERE id=?", (order_id,)
        ).fetchone()[0],
        "quantity": db.execute("SELECT quantity FROM products WHERE id=1").fetchone()[0],
        "inspections": db.execute("SELECT COUNT(*) FROM outbound_inspections").fetchone()[0],
        "movements": db.execute("SELECT COUNT(*) FROM stock_movements").fetchone()[0],
    }
    attempts = [
        client.post("/api/outbound-orders", json=order_payload()),
        client.put(f"/api/outbound-orders/{order_id}", json=order_payload()),
        client.delete(f"/api/outbound-orders/{order_id}"),
        client.post(f"/api/outbound-orders/{order_id}/validate-stock"),
        client.put(f"/api/outbound-orders/{order_id}/inspection", json={"items": []}),
        client.post(
            f"/api/outbound-orders/{order_id}/status",
            json={"status": "completed"},
        ),
    ]
    for response in attempts:
        assert response.status_code == 410
        assert response.is_json
        assert response.json["error"]["code"] == "USE_COMPLIANT_WORKFLOW"
        assert response.json["error"]["fields"]["workflow"] == "/api/outbound-receipts"

    assert db.execute("SELECT COUNT(*) FROM outbound_orders").fetchone()[0] == before["orders"]
    assert db.execute(
        "SELECT status FROM outbound_orders WHERE id=?", (order_id,)
    ).fetchone()[0] == before["status"]
    assert db.execute("SELECT quantity FROM products WHERE id=1").fetchone()[0] == before["quantity"]
    assert db.execute("SELECT COUNT(*) FROM outbound_inspections").fetchone()[0] == before["inspections"]
    assert db.execute("SELECT COUNT(*) FROM stock_movements").fetchone()[0] == before["movements"]
