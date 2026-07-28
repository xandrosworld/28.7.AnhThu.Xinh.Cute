# DNP Logistics WMS — Nhánh Anh_Thu

Phân hệ quản trị nền tảng cho hệ thống quản lý kho DNP Logistics. Phiên bản này thay thế hoàn toàn dữ liệu giả lập phía trình duyệt bằng ứng dụng **Flask + SQLite**, có xác thực phiên, phân quyền, transaction và nhật ký truy vết.

## Chức năng hoàn thiện

- Đăng nhập/đăng xuất bằng session; mật khẩu lưu bằng `scrypt` hash.
- Ba vai trò: quản trị viên, quản lý kho và nhân viên.
- Dashboard lấy số liệu trực tiếp từ database.
- Tra cứu tồn kho theo từ khóa, danh mục, kho và trạng thái; có phân trang.
- Xem chi tiết và lịch sử điều chỉnh của từng hàng hóa.
- Kiểm kê/điều chỉnh tồn kho trong transaction, lưu chênh lệch và người thực hiện.
- CRUD danh mục có kiểm tra trùng và ngăn xóa khi đang được sử dụng.
- CRUD người dùng, khóa tài khoản, ngăn tự khóa/hạ quyền/xóa và bảo toàn lịch sử nghiệp vụ.
- Cập nhật hồ sơ, đổi mật khẩu có kiểm tra mật khẩu hiện tại.
- Nhật ký hệ thống dành cho quản trị viên.
- Validation ở API, CSRF cho thao tác ghi, giao diện responsive và hỗ trợ bàn phím cơ bản.

## Yêu cầu

- Python 3.10 trở lên.
- Không cần cài SQLite riêng vì Python đã tích hợp sẵn.

## Cài đặt và chạy

Trên PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:SECRET_KEY = "thay-bang-mot-chuoi-bi-mat-dai-va-ngau-nhien"
python run.py
```

Mở `http://127.0.0.1:5000`. Database và dữ liệu demo được tạo tự động tại `instance/dnp_wms.sqlite` trong lần chạy đầu.

Để khởi tạo lại dữ liệu demo:

```powershell
flask --app run.py init-db
```

> Lệnh này xóa dữ liệu hiện tại trong database rồi tạo lại dữ liệu mẫu.

## Tài khoản demo

| Vai trò | Tên đăng nhập | Mật khẩu | Quyền chính |
|---|---|---|---|
| Quản trị viên | `admin` | `Admin@123` | Toàn bộ chức năng, người dùng, nhật ký |
| Quản lý kho | `quanlykho` | `Kho@12345` | Danh mục, kiểm kê tồn kho |
| Nhân viên | `nhanvien` | `NV@123456` | Xem dashboard, tồn kho, hồ sơ |

Tài khoản `khoatam` được seed ở trạng thái khóa để minh họa kiểm soát truy cập.

## Kiểm thử

```powershell
python -m pytest -q
```

Bộ test bao phủ:

- đăng nhập đúng/sai, tài khoản khóa, logout và session;
- bảo vệ API, CSRF và phân quyền trang/API;
- lọc, xem chi tiết và transaction điều chỉnh tồn;
- validation nghiệp vụ;
- CRUD danh mục/người dùng và các ràng buộc xóa;
- hash mật khẩu, hồ sơ, đổi mật khẩu, audit log;
- lệnh CLI khởi tạo database.

## Cấu trúc dự án

```text
app/
├── __init__.py          # Application factory, cấu hình và error handler
├── auth.py              # Session, CSRF, decorator phân quyền
├── api.py               # API nghiệp vụ
├── db.py                # Kết nối, seed và audit helper
├── schema.sql           # Schema, foreign key, index, CHECK constraint
├── static/
│   ├── app.css
│   └── app.js
└── templates/           # Jinja templates
tests/                   # Kiểm thử Flask API/DB
run.py                   # Điểm chạy ứng dụng
```

## Luồng nghiệp vụ điều chỉnh tồn

1. Quản lý hoặc quản trị viên chọn hàng hóa và nhập số lượng thực tế.
2. API kiểm tra quyền, CSRF, số lượng và lý do.
3. Trong cùng một transaction, hệ thống:
   - tạo bản ghi `inventory_adjustments`;
   - cập nhật `inventory.quantity`;
   - ghi `audit_logs`.
4. Nếu một bước lỗi, toàn bộ transaction được rollback.

Trạng thái tồn kho được tính tự động:

- `Hết hàng`: số lượng bằng 0.
- `Sắp thiếu`: số lượng lớn hơn 0 và không vượt ngưỡng tối thiểu.
- `Đủ hàng`: số lượng lớn hơn ngưỡng tối thiểu.

## API chính

| Method | Endpoint | Mô tả |
|---|---|---|
| `POST` | `/api/auth/login` | Đăng nhập |
| `POST` | `/api/auth/logout` | Đăng xuất |
| `GET` | `/api/dashboard` | Số liệu tổng quan |
| `GET` | `/api/inventory` | Danh sách/lọc/phân trang tồn kho |
| `GET` | `/api/inventory/:id` | Chi tiết và lịch sử |
| `POST` | `/api/inventory/:id/adjustments` | Điều chỉnh tồn |
| `GET/POST` | `/api/categories` | Danh sách/tạo danh mục |
| `PUT/DELETE` | `/api/categories/:id` | Sửa/xóa danh mục |
| `GET/POST` | `/api/users` | Danh sách/tạo người dùng |
| `PUT/DELETE` | `/api/users/:id` | Sửa/xóa người dùng |
| `PUT` | `/api/profile` | Cập nhật hồ sơ |
| `PUT` | `/api/profile/password` | Đổi mật khẩu |
| `GET` | `/api/audit-logs` | Nhật ký quản trị |

Các API ghi dữ liệu yêu cầu header `X-CSRF-Token`; giao diện tự lấy token từ `/api/auth/me`.

## Ghi chú bảo mật

- Khi chạy ngoài môi trường học tập, luôn đặt biến môi trường `SECRET_KEY`.
- Không commit file trong `instance/`, `.env` hoặc mật khẩu thực.
- Ứng dụng này được thiết kế chạy local theo yêu cầu bài nộp, chưa cấu hình reverse proxy/HTTPS để deploy công khai.
