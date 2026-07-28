CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    full_name TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('ADMIN','CS','WAREHOUSE')),
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','locked')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS suppliers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','inactive'))
);

CREATE TABLE IF NOT EXISTS warehouses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','inactive'))
);

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    unit TEXT NOT NULL,
    barcode TEXT NOT NULL UNIQUE,
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
    seal_no TEXT DEFAULT '',
    created_by INTEGER REFERENCES users(id),
    completed_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS receipt_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    receipt_id INTEGER NOT NULL REFERENCES receipts(id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES products(id),
    planned_qty REAL NOT NULL CHECK(planned_qty > 0),
    actual_qty REAL CHECK(actual_qty >= 0),
    rejected_qty REAL NOT NULL DEFAULT 0 CHECK(rejected_qty >= 0),
    rejection_reason TEXT DEFAULT '',
    unit_price REAL NOT NULL DEFAULT 0 CHECK(unit_price >= 0),
    pallet_id TEXT NOT NULL UNIQUE,
    barcode TEXT NOT NULL,
    unit TEXT NOT NULL,
    expiry_date TEXT,
    UNIQUE(receipt_id, product_id)
);

CREATE TABLE IF NOT EXISTS inspections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    receipt_id INTEGER NOT NULL UNIQUE REFERENCES receipts(id) ON DELETE CASCADE,
    checklist_json TEXT NOT NULL,
    result TEXT NOT NULL CHECK(result IN ('pass','fail')),
    note TEXT DEFAULT '',
    inspected_by TEXT NOT NULL,
    inspected_by_user_id INTEGER REFERENCES users(id),
    inspected_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS inventory_lots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id),
    receipt_item_id INTEGER NOT NULL UNIQUE REFERENCES receipt_items(id),
    warehouse TEXT NOT NULL,
    pallet_id TEXT NOT NULL UNIQUE,
    barcode TEXT NOT NULL,
    unit TEXT NOT NULL,
    quantity REAL NOT NULL CHECK(quantity >= 0),
    received_at TEXT NOT NULL,
    expiry_date TEXT
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
    actor_user_id INTEGER REFERENCES users(id),
    details TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_receipts_status ON receipts(status);
CREATE INDEX IF NOT EXISTS idx_receipts_received_date ON receipts(received_date);
CREATE INDEX IF NOT EXISTS idx_audit_created_at ON audit_logs(created_at);
CREATE UNIQUE INDEX IF NOT EXISTS uq_products_barcode ON products(barcode);
CREATE UNIQUE INDEX IF NOT EXISTS uq_receipt_items_pallet ON receipt_items(pallet_id);
CREATE INDEX IF NOT EXISTS idx_inventory_lots_product ON inventory_lots(product_id);
