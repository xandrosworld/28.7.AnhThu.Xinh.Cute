def test_category_crud_and_duplicate_guard(client, admin_login):
    _, csrf = admin_login
    headers = {"X-CSRF-Token": csrf}
    created = client.post(
        "/api/categories",
        json={
            "code": "ATLD",
            "name": "An toàn lao động",
            "description": "Thiết bị bảo hộ",
            "status": "active",
        },
        headers=headers,
    )
    assert created.status_code == 201
    category_id = created.get_json()["id"]

    duplicate = client.post(
        "/api/categories",
        json={"code": "ATLD", "name": "Tên khác", "status": "active"},
        headers=headers,
    )
    assert duplicate.status_code == 409

    updated = client.put(
        f"/api/categories/{category_id}",
        json={
            "code": "ATLD",
            "name": "Bảo hộ lao động",
            "description": "",
            "status": "active",
        },
        headers=headers,
    )
    assert updated.status_code == 200
    assert client.delete(
        f"/api/categories/{category_id}", headers=headers
    ).status_code == 200


def test_cannot_delete_category_in_use(client, admin_login):
    _, csrf = admin_login
    response = client.delete("/api/categories/1", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 409


def test_user_crud_password_is_hashed(client, admin_login, db):
    _, csrf = admin_login
    headers = {"X-CSRF-Token": csrf}
    created = client.post(
        "/api/users",
        json={
            "username": "newstaff",
            "password": "Demo@1234",
            "full_name": "Nhân viên Mới",
            "email": "newstaff@dnp.vn",
            "phone": "0909999999",
            "role": "staff",
            "status": "active",
        },
        headers=headers,
    )
    assert created.status_code == 201
    user_id = created.get_json()["id"]
    stored = db.execute(
        "SELECT password_hash FROM users WHERE id = ?", (user_id,)
    ).fetchone()[0]
    assert stored != "Demo@1234"
    assert stored.startswith("scrypt:")

    updated = client.put(
        f"/api/users/{user_id}",
        json={
            "username": "newstaff",
            "password": "",
            "full_name": "Nhân viên Đã Sửa",
            "email": "newstaff@dnp.vn",
            "phone": "",
            "role": "staff",
            "status": "locked",
        },
        headers=headers,
    )
    assert updated.status_code == 200
    assert client.delete(f"/api/users/{user_id}", headers=headers).status_code == 200


def test_admin_cannot_lock_or_delete_self(client, admin_login):
    _, csrf = admin_login
    headers = {"X-CSRF-Token": csrf}
    lock = client.put(
        "/api/users/1",
        json={
            "username": "admin",
            "password": "",
            "full_name": "Nguyễn Anh Thư",
            "email": "admin@dnp.vn",
            "phone": "",
            "role": "staff",
            "status": "locked",
        },
        headers=headers,
    )
    assert lock.status_code == 422
    assert client.delete("/api/users/1", headers=headers).status_code == 422


def test_profile_update_and_change_password(client, staff_login):
    _, csrf = staff_login
    headers = {"X-CSRF-Token": csrf}
    profile = client.put(
        "/api/profile",
        json={
            "full_name": "Lê Hoàng Nam Mới",
            "email": "nam.moi@dnp.vn",
            "phone": "0912345678",
        },
        headers=headers,
    )
    assert profile.status_code == 200

    wrong = client.put(
        "/api/profile/password",
        json={
            "current_password": "wrong",
            "new_password": "Newpass123",
            "confirm_password": "Newpass123",
        },
        headers=headers,
    )
    assert wrong.status_code == 422

    changed = client.put(
        "/api/profile/password",
        json={
            "current_password": "NV@123456",
            "new_password": "Newpass123",
            "confirm_password": "Newpass123",
        },
        headers=headers,
    )
    assert changed.status_code == 200


def test_audit_log_is_admin_only(client, staff_login):
    assert client.get("/api/audit-logs").status_code == 403
