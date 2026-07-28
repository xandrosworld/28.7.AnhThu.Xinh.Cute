# DNP WMS — Nhánh Thanh Trúc

Phân hệ nhập kho, kiểm tra chất lượng, tồn theo pallet và báo cáo của đề tài
quản lý kho DNP Logistics. Đây là nhánh cá nhân của **Nguyễn Hoàng Thanh Trúc
(BA)**, được hoàn thiện thành một ứng dụng Flask + SQLite có thể chạy và kiểm
thử độc lập.

## Chức năng

- Đăng nhập bằng session, mật khẩu băm, khóa tài khoản, CSRF và phân quyền phía server.
- Ba vai trò: `ADMIN`, `CS`, `WAREHOUSE`.
- Lập/sửa/xóa phiếu nhập; mỗi dòng giữ nguyên SKU, đơn vị, barcode, pallet ID và hạn dùng.
- Kiểm tra 7 tiêu chí, ghi số lượng chấp nhận/từ chối và lý do sai lệch.
- Hoàn tất trong transaction; cộng đúng số lượng chấp nhận và chống cộng tồn hai lần.
- Theo dõi tồn theo lô/pallet, stock movement và audit log có người thao tác.
- Dashboard, lịch sử, báo cáo theo khoảng ngày và CSV UTF-8 BOM.
- Sao lưu/phục hồi SQLite bằng online backup API.

## Cài đặt và chạy

Yêu cầu Python 3.10 trở lên.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:SECRET_KEY = "mot-chuoi-ngau-nhien-dai"
python app.py
```

Mở `http://127.0.0.1:5000`. Cơ sở dữ liệu demo được tạo tại
`instance/wms.sqlite3` ở lần chạy đầu tiên.

| Vai trò | Tài khoản | Mật khẩu | Nghiệp vụ |
|---|---|---|---|
| Quản trị | `admin` | `Admin@123` | Toàn quyền |
| Chăm sóc khách hàng | `cs` | `CS@12345` | Lập và chỉnh sửa phiếu nhập |
| Nhân viên kho | `warehouse` | `Kho@12345` | Kiểm tra và xác nhận nhập |

`locked / Locked@123` là tài khoản bị khóa dùng cho kiểm thử.

> Tài khoản và mật khẩu trên chỉ là dữ liệu demo. Khi sử dụng dữ liệu thật phải
> đổi toàn bộ mật khẩu và đặt `SECRET_KEY` qua biến môi trường.

## Kiểm thử

```powershell
python -m pytest -q
python -m pytest --cov=app --cov=database --cov-report=term-missing
node --check static/app.js
python -m compileall -q app.py database.py
```

Bộ test acceptance bao phủ đăng nhập, tài khoản khóa, RBAC, CSRF, master data,
validation, barcode/pallet/đơn vị, kiểm tra thực nhận, phần hàng từ chối,
transaction hoàn tất, idempotency, tồn theo lô, báo cáo, CSV, audit và
backup/restore. Kết quả xác nhận gần nhất: **14/14 test đạt, coverage 88%**.

## Sao lưu và phục hồi

```powershell
flask --app app backup-db --destination backups/wms-demo.sqlite3
flask --app app restore-db --source backups/wms-demo.sqlite3
```

Nên dừng thao tác ghi trong lúc phục hồi. Lệnh restore kiểm tra
`PRAGMA integrity_check` trước khi thay dữ liệu đích.

## Cấu trúc

```text
app.py                 Flask routes, auth/RBAC/CSRF, nghiệp vụ và CLI
database.py            Khởi tạo và seed dữ liệu demo
schema.sql             Schema, khóa ngoại, UNIQUE, CHECK và index
templates/, static/    Giao diện responsive tiếng Việt
tests/                 Acceptance/integration tests
docs/                  Truy vết yêu cầu, kịch bản nghiệm thu và demo
```

## Tài liệu

- [Ma trận BR/NFR](docs/REQUIREMENTS_TRACEABILITY.md)
- [Kịch bản acceptance](docs/ACCEPTANCE_TESTS.md)
- [Kịch bản demo 8–10 phút](docs/DEMO_GUIDE.md)
- [Phân công và đóng góp](docs/CONTRIBUTION.md)
- `docs/49K21.1_NguyenHoangThanhTruc_Chuong3_KPI4_HoanThien.docx`: bản đối chiếu
  hoàn thiện, được tạo từ bản gốc và không thay đổi file nguồn.

## Giới hạn được công bố

Nhánh Thanh Trúc phụ trách BA, nhập kho và báo cáo. Luồng xuất kho thuộc nhánh
Lê Thảo; giao diện nền tảng và quản trị đầy đủ thuộc nhánh Anh Thư. SQLite được
dùng để giảng viên chạy nhanh; đây không phải cấu hình triển khai production.
