# DNP Logistics WMS

Hệ thống quản lý kho thống nhất của nhóm, xây dựng bằng Flask, SQLAlchemy và
Alembic. Ứng dụng chạy ngay với SQLite để chấm/demo và hỗ trợ SQL Server 2022
qua `DATABASE_URL`. Không cần Internet, CDN hoặc deployment để sử dụng.

Repository bàn giao chính thức:
<https://github.com/xandrosworld/28.7.AnhThu.Xinh.Cute>

## Chức năng

- Đăng nhập, session, mật khẩu băm, CSRF, audit và phân quyền phía server cho
  `ADMIN`, `CS`, `WAREHOUSE`.
- Quản lý tài khoản, vai trò, đơn vị, danh mục, hàng hóa, khách hàng, email hợp
  đồng, nhà cung cấp và kho; master đã phát sinh được ngừng hoạt động thay vì
  xóa mất lịch sử.
- Phiếu nhập nhiều dòng, container/seal, pallet/barcode/hạn dùng, kiểm nhận
  accepted/rejected và xác nhận nguyên tử.
- Phiếu xuất kiểm email hợp đồng, kiểm tồn lại khi xác nhận, picking FEFO/FIFO,
  bỏ lô hết hạn và không cho tồn âm.
- Tồn theo sản phẩm và lot/pallet, stock movement, kiểm kê snapshot, chống xác
  nhận lặp và rollback toàn bộ khi một dòng lỗi.
- Dashboard, bộ lọc, báo cáo, CSV UTF-8, trang in phiếu/picking list và
  backup/restore SQLite.
- Giao diện tiếng Việt responsive, hỗ trợ bàn phím, loading/empty/error state,
  máy quét USB và `BarcodeDetector` khi trình duyệt có hỗ trợ.

## Kiến trúc

```text
app/
  __init__.py       app factory, cấu hình, security/API envelope
  models.py         mô hình SQLAlchemy và constraint
  services.py       transaction, lot allocation, invariants
  api.py            API và validation
  auth.py           session, RBAC, CSRF
  templates/        giao diện Jinja
  static/           CSS/JavaScript nội bộ
migrations/         Alembic revisions
tests/              unit, API integration, database và HTTP/DOM smoke
docs/               BR/NFR, acceptance, demo và báo cáo hoàn thiện
```

API thành công luôn có `data` và `meta`; các alias lịch sử như `items`, `item`,
`user` vẫn được giữ để tương thích giao diện. Lỗi có dạng:

```json
{
  "ok": false,
  "message": "Thông báo cho người dùng",
  "error": {
    "code": "validation_error",
    "message": "Thông báo cho người dùng",
    "fields": {"field": "Chi tiết lỗi"}
  }
}
```

## Chạy nhanh bằng SQLite

Yêu cầu Python 3.10 trở lên.

### PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Mở `.env`, thay `SECRET_KEY` bằng chuỗi ngẫu nhiên dài và để
`DATABASE_URL` trống. Dùng migration làm nguồn chuẩn:

```powershell
flask --app run.py db upgrade
flask --app run.py seed-db
python run.py
```

Mở <http://127.0.0.1:5000>.

### Linux/macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp .env.example .env
flask --app run.py db upgrade
flask --app run.py seed-db
python run.py
```

`seed-db` an toàn khi chạy lặp và không chèn trùng dữ liệu. `init-db` là lệnh
reset dữ liệu demo phục vụ phát triển; lệnh này xóa dữ liệu đang cấu hình nên
không dùng với dữ liệu cần giữ.

## Tài khoản demo

| Vai trò | Tên đăng nhập | Mật khẩu |
|---|---|---|
| Quản trị viên | `admin` | `Admin@123` |
| Chăm sóc khách hàng | `cs` | `Cs@123456` |
| Nhân viên kho | `warehouse` | `Kho@12345` |
| Tương thích CS | `quanlykho` | `Kho@12345` |
| Tương thích kho | `nhanvien` | `NV@123456` |

`khoatam / Locked@123` được seed ở trạng thái khóa để kiểm thử.

Các mật khẩu trên chỉ dành cho dữ liệu demo. Khi dùng ngoài máy cá nhân phải
đổi mật khẩu, đặt `SECRET_KEY` thật qua biến môi trường và dùng HTTPS.

## SQL Server 2022

1. Cài Microsoft ODBC Driver 18 và tạo database `dnp_wms`.
2. Đặt `DATABASE_URL`, ví dụ:

```text
mssql+pyodbc://wms_user:<PASSWORD>@localhost:1433/dnp_wms?driver=ODBC+Driver+18+for+SQL+Server&Encrypt=yes&TrustServerCertificate=yes
```

3. Chạy:

```powershell
flask --app run.py db upgrade
flask --app run.py seed-db
python run.py
```

Không commit mật khẩu hoặc chuỗi kết nối thật. Xem thêm
[`docs/SQL_SERVER.md`](docs/SQL_SERVER.md). GitHub Actions có job SQL Server
2022 riêng: job này tạo database thật, chạy migration/seed rồi chạy test qua
ODBC 18; không giả lập bằng SQLite.

## Migration

Sau khi thay đổi model:

```powershell
flask --app run.py db migrate -m "mo ta thay doi"
flask --app run.py db upgrade
```

Luôn review revision sinh ra trên cả SQLite và SQL Server. Không dùng
`db.create_all()` thay migration cho database bàn giao.

## Backup và phục hồi

SQLite:

```powershell
flask --app run.py backup-db --output backups\dnp-wms-20260728.sqlite
flask --app run.py restore-db backups\dnp-wms-20260728.sqlite --yes
```

- Backup từ chối ghi đè file đã tồn tại.
- Restore kiểm tra `PRAGMA integrity_check` trước và yêu cầu `--yes`.
- Không commit thư mục backup vào Git.

Với SQL Server, dùng `BACKUP DATABASE`/`RESTORE DATABASE` và chính sách
transaction log của SQL Server; CLI SQLite cố ý từ chối database khác.

## Kiểm thử

Chạy toàn bộ:

```powershell
python -m pytest -q
```

Đo coverage đầy đủ để phân tích:

```powershell
python -m pytest --cov=app --cov-report=term-missing -q
```

Kết quả xác minh cục bộ cuối trên Python 3.12/SQLite:
**65 test đạt, 1 test cạnh tranh dành riêng cho SQL Server được skip; coverage
toàn package `app` đạt 87,48%**. CI đặt ngưỡng 85% cho toàn package. Không tính
checklist trình duyệt thủ công vào số test tự động.

Bộ test bao phủ:

- auth, tài khoản khóa, RBAC, CSRF và API error envelope;
- CRUD/update/deactivate master, duplicate và dữ liệu đã tham chiếu;
- draft/edit/inspect/confirm/cancel phiếu nhập;
- email hợp đồng, FEFO/FIFO, lô hết hạn, rollback và invariant tồn;
- stale/idempotent stocktake, dữ liệu số/ngày/JSON sai kiểu;
- migration/seed, backup/restore và HTTP/DOM/static asset;
- cạnh tranh xác nhận xuất trên job SQL Server thật.

Workflow `.github/workflows/ci.yml` chạy SQLite trên Python 3.10/3.12 và một
job SQL Server 2022 + ODBC 18. Test đồng thời chỉ được chuyển từ skip sang chạy
trong job SQL Server thật. Dự án không có job deploy.

## API chính

| Nhóm | Endpoint |
|---|---|
| Auth | `POST /api/auth/login`, `POST /logout`, `GET /me` |
| Lookup | `GET /api/roles`, `/units`, `/operations/lookups` |
| Master | `/api/users`, `/categories`, `/products`, `/customers`, `/suppliers`, `/warehouses` |
| Nhập | `/api/inbound-receipts`, `/:id/inspect`, `/:id/confirm`, `/:id/cancel` |
| Xuất | `/api/outbound-receipts`, `/:id/check-stock`, `/:id/picking-list`, `/:id/confirm`, `/:id/cancel` |
| Tồn | `/api/inventory`, `/api/stock-movements`, `/api/stocktakes`, `/api/stocktakes/:id` |
| Báo cáo | `/api/reports/summary`, `/api/reports/export.csv` |

Các lệnh ghi cần session đúng vai trò và header `X-CSRF-Token`.

## Tài liệu và nghiệm thu

- [Ma trận BR/NFR](docs/REQUIREMENTS_TRACEABILITY.md)
- [Acceptance tests](docs/ACCEPTANCE_TESTS.md)
- [Kịch bản demo 8–10 phút](docs/DEMO_GUIDE.md)
- [Phân công và đóng góp](docs/CONTRIBUTION.md)
- [Chiến lược kiểm thử](docs/TESTING.md)
- [Bằng chứng hiệu năng NFR-005](docs/PERFORMANCE.md)
- Bản báo cáo hoàn thiện:
  `docs/49K21.1_NguyenHoangThanhTruc_Chuong3_KPI4_HoanThien.docx`

Playwright không được đưa vào dependency bắt buộc để tránh tải browser và gây
flaky khi chấm offline. HTTP/DOM/static contract được test tự động; Chrome,
Edge, Firefox, camera barcode và các kích thước 390/1024/1366 px nằm trong
checklist nghiệm thu thủ công và phải có biên bản trước khi nộp.

## Lịch sử ba thành viên

- `Anh_Thu`: frontend, responsive và trải nghiệm người dùng.
- `Le_Thao`: backend, database, transaction và toàn vẹn tồn.
- `Thanh_Truc`: phân tích yêu cầu, acceptance, báo cáo và tài liệu.

Ba nhánh và tác giả commit được giữ nguyên. `main` là sản phẩm tích hợp, không
thay thế hoặc làm mất bằng chứng đóng góp của từng bạn.
