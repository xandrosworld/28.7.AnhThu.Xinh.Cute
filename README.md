# DNP Logistics WMS — Nhánh Anh_Thu

Ứng dụng quản lý kho chạy bằng **Flask + SQLite**. Dữ liệu được lưu tại server, các thao tác nghiệp vụ có xác thực phiên, phân quyền, CSRF, transaction và nhật ký truy vết.

## Chức năng

- Dashboard và báo cáo lấy số liệu trực tiếp từ database.
- Quản lý hàng hóa, barcode, danh mục, khách hàng, nhà cung cấp và kho.
- Tra cứu tồn kho theo từ khóa, danh mục, kho, trạng thái; xem lịch sử và điều chỉnh tồn.
- Phiếu nhập: lập phiếu, kiểm nhận, xác nhận nhập, hủy và cập nhật tồn kho nguyên tử.
- Phiếu xuất: xác thực email hợp đồng, kiểm tra tồn gộp, picking list FEFO/FIFO, xác nhận xuất và hủy.
- Kiểm kê theo snapshot; từ chối xác nhận nếu tồn kho đã thay đổi sau khi lập phiếu.
- Quản lý người dùng, hồ sơ, mật khẩu và nhật ký hệ thống.
- API trả lỗi JSON có cấu trúc; giao diện responsive, hỗ trợ bàn phím và máy quét barcode/camera khi trình duyệt hỗ trợ.

## Vai trò và quyền

| Vai trò | Quyền chính |
|---|---|
| `admin` | Toàn bộ chức năng, người dùng và nhật ký |
| `cs` | Dữ liệu nền, đối tác, lập/hủy phiếu nhập xuất |
| `warehouse` | Xem vận hành, kiểm nhận, xác nhận phiếu, điều chỉnh và kiểm kê tồn |
| `manager` | Vai trò tương thích với `cs`, đồng thời giữ quyền xác nhận/điều chỉnh cũ |
| `staff` | Vai trò tương thích với `warehouse` cho các luồng xác nhận phiếu |

Các API ghi dữ liệu luôn kiểm tra quyền và header `X-CSRF-Token`.

## Cài đặt và chạy

Yêu cầu Python 3.10 trở lên. Trên PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:SECRET_KEY = "thay-bang-mot-chuoi-bi-mat-dai-va-ngau-nhien"
python run.py
```

Mở `http://127.0.0.1:5000`. Database demo được tạo tự động tại `instance/dnp_wms.sqlite` trong lần chạy đầu.

Các biến mẫu nằm trong `.env.example`. Ứng dụng đọc trực tiếp biến môi trường của tiến trình; file `.env` không được tự động nạp.

Khởi tạo lại dữ liệu demo:

```powershell
flask --app run.py init-db
```

> Lệnh này xóa dữ liệu trong database đang cấu hình rồi tạo lại schema và dữ liệu mẫu.

## Tài khoản demo

| Vai trò | Tên đăng nhập | Mật khẩu |
|---|---|---|
| Quản trị viên | `admin` | `Admin@123` |
| Chăm sóc khách hàng | `cs` | `Cs@123456` |
| Nhân viên kho | `warehouse` | `Kho@12345` |
| Tương thích CS | `quanlykho` | `Kho@12345` |
| Tương thích kho | `nhanvien` | `NV@123456` |

Tài khoản `khoatam` được seed ở trạng thái khóa để kiểm thử kiểm soát truy cập.

## Kiểm thử và coverage

```powershell
python -m pytest tests -q
python -m pytest --cov=app --cov-report=term-missing -q
```

Kết quả hiện tại: **33 tests passed**, tổng statement coverage của package `app` **87%**. Lệnh coverage yêu cầu cài thêm `pytest-cov` nếu môi trường chưa có (`python -m pip install pytest-cov`).

Bộ test bao phủ xác thực/session, tài khoản khóa, CSRF/RBAC, CRUD quản trị, hash mật khẩu, bộ lọc tồn, transaction/rollback, phiếu nhập xuất, email hợp đồng, kiểm nhận, kiểm tra tồn, picking, hủy/xác nhận lặp, kiểm kê, báo cáo và CLI. Review adversarial còn kiểm tra payload sai kiểu, snapshot kiểm kê cũ, trùng barcode/pallet, API không tồn tại và hợp đồng route/template/static.

## API chính

| Nhóm | Endpoint |
|---|---|
| Xác thực | `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/me` |
| Tổng quan | `GET /api/dashboard`, `GET /api/lookups` |
| Tồn kho | `GET /api/inventory`, `GET /api/inventory/:id`, `POST /api/inventory/:id/adjustments` |
| Hàng hóa | `GET/POST /api/products` |
| Danh mục | `GET/POST /api/categories`, `PUT/DELETE /api/categories/:id` |
| Đối tác | `GET/POST /api/customers`, `GET/POST /api/suppliers` |
| Kho | `GET /api/warehouses`, `GET /api/operations/lookups` |
| Phiếu nhập | `GET/POST /api/inbound-receipts`, `GET /api/inbound-receipts/:id`, `POST .../:id/inspect`, `POST .../:id/confirm`, `POST .../:id/cancel` |
| Phiếu xuất | `GET/POST /api/outbound-receipts`, `GET /api/outbound-receipts/:id`, `GET .../:id/check-stock`, `GET .../:id/picking-list`, `POST .../:id/confirm`, `POST .../:id/cancel` |
| Kiểm kê | `GET/POST /api/stocktakes`, `POST /api/stocktakes/:id/confirm` |
| Báo cáo | `GET /api/reports/summary`, `GET /api/reports/export.csv` |
| Quản trị | `GET/POST /api/users`, `PUT/DELETE /api/users/:id`, `GET /api/audit-logs` |
| Hồ sơ | `PUT /api/profile`, `PUT /api/profile/password` |

API lỗi trả dạng:

```json
{
  "ok": false,
  "message": "Thông báo cho người dùng",
  "error": {
    "code": "validation_error",
    "message": "Thông báo cho người dùng",
    "fields": {}
  }
}
```

## Tính toàn vẹn và bảo mật

- Mật khẩu được hash bằng Werkzeug `scrypt`; cookie phiên có `HttpOnly` và `SameSite=Lax`.
- Các thao tác ghi yêu cầu token CSRF lấy từ phản hồi đăng nhập hoặc `/api/auth/me`.
- Phiếu chỉ nhận hàng hóa thuộc đúng kho đã chọn; số xuất được cộng gộp theo hàng hóa trước khi so với tồn.
- Xác nhận phiếu và kiểm kê dùng transaction; lỗi giữa chừng rollback toàn bộ cập nhật tồn và movement.
- Xác nhận/hủy lặp không ghi movement lần hai; mã chứng từ, SKU, barcode và khóa dòng được bảo vệ bằng ràng buộc database.
- API không tồn tại và lỗi ứng dụng trả JSON; response API không được cache và có security headers.
- Khi chạy ngoài môi trường local, phải đặt `SECRET_KEY`, dùng HTTPS/reverse proxy và không commit `instance/`, `.env` hay thông tin xác thực thật.

## Cấu trúc dự án

```text
app/
├── __init__.py          # Application factory, cấu hình, security/error handlers
├── auth.py              # Session, CSRF và decorator phân quyền
├── api.py               # API quản trị, tồn kho và vận hành WMS
├── db.py                # Kết nối SQLite, seed và audit helper
├── schema.sql           # Schema, foreign key, index và CHECK constraint
├── static/              # CSS/JavaScript giao diện
└── templates/           # Jinja templates
tests/                   # 33 kiểm thử Flask API/DB
run.py                   # Điểm chạy ứng dụng
```
