CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    unit TEXT NOT NULL,
    current_stock REAL NOT NULL DEFAULT 0 CHECK(current_stock >= 0),
    min_stock REAL NOT NULL DEFAULT 0 CHECK(min_stock >= 0),
    unit_price REAL NOT NULL DEFAULT 0 CHECK(unit_price >= 0)
);

CREATE TABLE IF NOT EXISTS receipts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    supplier TEXT NOT NULL,
    warehouse TEXT NOT NULL,
    received_date TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('draft','pending','inspecting','completed','rejected')),
    vehicle_no TEXT DEFAULT '',
    container_no TEXT DEFAULT '',
    note TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS receipt_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    receipt_id INTEGER NOT NULL REFERENCES receipts(id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES products(id),
    planned_qty REAL NOT NULL CHECK(planned_qty > 0),
    actual_qty REAL CHECK(actual_qty > 0),
    unit_price REAL NOT NULL DEFAULT 0 CHECK(unit_price >= 0),
    UNIQUE(receipt_id, product_id)
);

CREATE TABLE IF NOT EXISTS inspections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    receipt_id INTEGER NOT NULL UNIQUE REFERENCES receipts(id) ON DELETE CASCADE,
    checklist_json TEXT NOT NULL,
    result TEXT NOT NULL CHECK(result IN ('pass','fail')),
    note TEXT DEFAULT '',
    inspected_by TEXT NOT NULL,
    inspected_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stock_movements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id),
    receipt_id INTEGER REFERENCES receipts(id),
    type TEXT NOT NULL CHECK(type IN ('IN','OUT','ADJUST')),
    quantity REAL NOT NULL,
    balance_after REAL NOT NULL CHECK(balance_after >= 0),
    reference_code TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(receipt_id, product_id, type)
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id INTEGER,
    details TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_receipts_status ON receipts(status);
CREATE INDEX IF NOT EXISTS idx_receipts_received_date ON receipts(received_date);
CREATE INDEX IF NOT EXISTS idx_audit_created_at ON audit_logs(created_at);
