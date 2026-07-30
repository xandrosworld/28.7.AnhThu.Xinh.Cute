# DNP Logistics WMS

Hệ thống quản lý kho thống nhất của nhóm, xây dựng bằng Flask, SQLAlchemy và
Alembic. Ứng dụng chạy ngay với SQLite để chấm/demo và hỗ trợ SQL Server 2022
qua `DATABASE_URL`. Không cần Internet, CDN hoặc deployment để sử dụng.

Repository bàn giao chính thức:
<https://github.com/xandrosworld/28.7.AnhThu.Xinh.Cute>

## Chức năng

- Đăng nhập, session, mật khẩu băm, CSRF, audit và phân quyền phía server cho
  `ADMIN`, `CS`, `WAREHOUSE`.
- Vận hành một kho duy nhất tại Đà Nẵng, không chia vị trí kho; kho được gán
  tự động khi lập hàng hóa, phiếu nhập/xuất và phiếu kiểm kê.
- Quản lý tài khoản, danh mục và hàng hóa trên giao diện; dữ liệu khách hàng,
  email hợp đồng và nhà cung cấp vẫn được bảo toàn trong nghiệp vụ nhập/xuất.
- Phiếu nhập nhiều dòng, container/seal, pallet/barcode/hạn dùng, kiểm nhận
  accepted/rejected bắt buộc trước xác nhận và cập nhật tồn nguyên tử.
- Phiếu xuất kiểm email hợp đồng, kiểm tồn lại khi xác nhận, picking FEFO/FIFO,
  trạng thái bắt đầu lấy hàng/từ chối/hủy, bỏ lô hết hạn và không cho tồn âm.
- Tồn theo sản phẩm và lot/pallet, stock movement, kiểm kê snapshot, chống xác
  nhận lặp, hỗ trợ số lượng thập phân và rollback toàn bộ khi một dòng lỗi.
- Dashboard có KPI nhập/xuất/tồn, cảnh báo và hoạt động gần đây; báo cáo có bộ
  lọc, CSV UTF-8, trang in phiếu/picking list và backup/restore SQLite.
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

Bằng chứng CI chính thức của bản nộp:
[main run 30349831432](https://github.com/xandrosworld/28.7.AnhThu.Xinh.Cute/actions/runs/30349831432)
trên commit `ce80fafd52fbd5df4558aca18803276fdc9ccaed` đã **thành công 6/6 job**.
SQLite/Python 3.10 và 3.12 đều đạt **78 passed, 1 skipped**; SQL Server 2022 +
ODBC 18 đạt **77 passed, 2 skipped**. Hai skip trên SQL Server chỉ là
backup/restore dành riêng cho SQLite; test cạnh tranh xác nhận xuất đã thực sự
chạy và đạt trên SQL Server.

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

Với SQL Server, dùng `BACKUP DATABASE`/`RESTORE DATABASE`, `CHECKSUM`,
`RESTORE VERIFYONLY` và phục hồi thử sang database riêng. Câu lệnh mẫu cùng
quy trình kiểm tra `DBCC CHECKDB` nằm tại
[`docs/SQL_SERVER.md`](docs/SQL_SERVER.md#backup-và-kiểm-tra-phục-hồi). CLI
SQLite cố ý từ chối database khác.

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
**78 test đạt, 1 test cạnh tranh dành riêng cho SQL Server được skip; coverage
toàn package `app` đạt 86,58%**. Ngưỡng coverage là 85%; Playwright được tính
riêng, không cộng vào số test pytest.

Kết quả SQL Server CI chính thức tại
[main run 30349831432](https://github.com/xandrosworld/28.7.AnhThu.Xinh.Cute/actions/runs/30349831432),
SHA `ce80fafd52fbd5df4558aca18803276fdc9ccaed`: **77 passed, 2 skipped**;
hai skip chỉ dành cho CLI backup/restore SQLite, còn test concurrency đã chạy
và đạt.

Bộ test bao phủ:

- auth, tài khoản khóa, RBAC, CSRF và API error envelope;
- CRUD/update/deactivate master, cấu hình vai trò/đơn vị, duplicate và dữ liệu
  đã tham chiếu;
- draft/edit/inspection bắt buộc/confirm/cancel phiếu nhập;
- email hợp đồng, FEFO/FIFO, start-picking/reject/cancel, lô hết hạn, rollback
  và invariant tồn;
- stale/idempotent stocktake, số lượng thập phân, dữ liệu số/ngày/JSON sai kiểu;
- migration/seed, backup/restore và HTTP/DOM/static asset;
- cạnh tranh xác nhận xuất trên job SQL Server thật.

Workflow `.github/workflows/ci.yml` cấu hình static/secret scan, benchmark,
SQLite trên Python 3.10/3.12, Playwright Chromium/Firefox tại 1366/1024/390 px
và SQL Server 2022 + ODBC 18. Cả 6/6 job đã đạt tại run và SHA nêu trên; dự án
không có job deploy.

### Playwright E2E

Node.js chỉ cần cho kiểm thử trình duyệt, không cần để chạy Flask:

```powershell
npm ci
npx playwright install chromium firefox
npm run test:e2e:list
npm run test:e2e
```

Chạy riêng từng browser hoặc viewport:

```powershell
npm run test:e2e -- --project=chromium-1366 --project=chromium-1024 --project=chromium-390
npm run test:e2e -- --project=firefox-1366 --project=firefox-1024 --project=firefox-390
```

Nếu đã có Google Chrome trên Windows, có thể dùng binary hệ thống mà không tải
Chromium của Playwright:

```powershell
$env:PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH='C:\Program Files\Google\Chrome\Application\chrome.exe'
npm run test:e2e -- --project=chromium-1366 --project=chromium-1024 --project=chromium-390
```

Suite hiện khai báo **18 project-cases**: 3 kịch bản × 2 browser × 3 viewport.
CI đạt **9/9 Chromium** và **9/9 Firefox** tại 1366/1024/390 px trong
[main run 30349831432](https://github.com/xandrosworld/28.7.AnhThu.Xinh.Cute/actions/runs/30349831432),
SHA `ce80fafd52fbd5df4558aca18803276fdc9ccaed`. Xác minh bổ sung cục bộ bằng
system Chrome cũng đạt 9/9 Chromium.

## API chính

| Nhóm | Endpoint |
|---|---|
| Auth | `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/me` |
| Cấu hình | `GET/POST /api/roles`, `PUT /api/roles/:id`; `GET/POST /api/units`, `PUT /api/units/:id` |
| Master | `/api/users`, `/categories`, `/products`, `/customers`, `/suppliers`, `/warehouses` |
| Nhập | `/api/inbound-receipts`, `/:id/inspect`, `/:id/confirm`, `/:id/cancel` |
| Xuất | `/api/outbound-receipts`, `/:id/check-stock`, `/:id/picking-list`, `/:id/start-picking`, `/:id/reject`, `/:id/confirm`, `/:id/cancel` |
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

Playwright được quản lý riêng trong `package.json`; Flask vẫn chạy offline mà
không cần Node/browser. Kết quả browser, screenshot/trace khi lỗi và URL CI
phải được lưu làm bằng chứng nghiệm thu, tách khỏi thống kê pytest.

## Lịch sử ba thành viên

- `Anh_Thu`: frontend, responsive và trải nghiệm người dùng.
- `Le_Thao`: backend, database, transaction và toàn vẹn tồn.
- `Thanh_Truc`: phân tích yêu cầu, acceptance, báo cáo và tài liệu.

Ba nhánh và tác giả commit được giữ nguyên. `main` là sản phẩm tích hợp, không
thay thế hoặc làm mất bằng chứng đóng góp của từng bạn.
