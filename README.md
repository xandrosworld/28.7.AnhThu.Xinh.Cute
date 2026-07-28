# DNP Logistics WMS — Backend & nghiệp vụ kho

Phân hệ do **Lê Phương Thảo** phụ trách trong đồ án quản lý kho. Ứng dụng là
một Flask app chạy độc lập, lưu dữ liệu thật bằng SQLite và có giao diện demo
responsive cho hàng hóa, phiếu xuất và lịch sử.

## Điểm nổi bật

- Đăng nhập bằng session, mật khẩu băm, CSRF token và phân quyền phía server:
  `ADMIN`, `CS`, `WAREHOUSE`.
- Quản lý khách hàng cùng email hợp đồng, nhà cung cấp, danh mục, hàng hóa và
  kho.
- Tồn theo kho, lô, pallet, barcode, vị trí, đơn vị và hạn sử dụng.
- Nhập kho nhiều dòng: kiểm tra số đạt/từ chối, lý do hàng lỗi và xác nhận
  transaction. Gọi xác nhận lại không cộng tồn lần hai.
- Xuất kho chỉ nhận email đã đăng ký trong hợp đồng; kiểm tra tồn lần nữa khi
  xác nhận; picking **FEFO trước, FIFO sau**; không âm kho và không trừ hai lần.
- Kiểm kê lưu tồn hệ thống, tồn thực tế, chênh lệch và từ chối xác nhận nếu tồn
  đã thay đổi sau lúc đếm.
- Sổ biến động tồn, audit log, báo cáo tổng hợp và CSV UTF-8.
- Backup/restore SQLite bằng Flask CLI.

## Cài đặt và chạy

Yêu cầu Python 3.10 trở lên:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:SECRET_KEY = "thay-bang-chuoi-ngau-nhien"
python app.py
```

Mở <http://127.0.0.1:5000>. Database và dữ liệu demo được tạo tự động tại
`instance/dnp_wms.sqlite3`.

Tài khoản demo:

| Vai trò | Tên đăng nhập | Mật khẩu |
|---|---|---|
| Quản trị | `admin` | `Admin@123` |
| Chăm sóc khách hàng | `cs` | `CS@12345` |
| Nhân viên kho | `warehouse` | `Kho@12345` |

Email hợp đồng demo: `muahang@khachhang.vn`.

## API nghiệp vụ

- Auth: `/api/auth/login`, `/logout`, `/me`, `/api/roles`.
- Master data: `/api/categories`, `/products`, `/customers`, `/suppliers`,
  `/warehouses`.
- Nhập kho: `/api/inbound-receipts`, `/<id>/inspect`, `/<id>/confirm`,
  `/<id>/cancel`.
- Xuất kho mới: `/api/outbound-receipts`, `/<id>/check-stock`,
  `/<id>/picking-list`, `/<id>/confirm`, `/<id>/cancel`.
- API giao diện cũ chỉ đọc: `GET /api/outbound-orders`, `/stats`, `/<id>`.
  Mọi mutation trên nhánh này trả `410 USE_COMPLIANT_WORKFLOW`; tạo và xử lý
  phiếu phải dùng `/api/outbound-receipts`.
- Tồn kho: `/api/inventory`, `/api/stock-movements`, `/api/stocktakes`,
  `/api/stocktakes/<id>/confirm`.
- Báo cáo: `/api/reports/summary`, `/api/reports/movements.csv`.

API mới trả thành công theo `{ "data": ..., "meta": ... }`; lỗi theo:

```json
{
  "error": {
    "code": "INSUFFICIENT_STOCK",
    "message": "Không đủ tồn kho.",
    "fields": {}
  }
}
```

Mọi request thay đổi dữ liệu sau đăng nhập phải gửi header `X-CSRF-Token`.

## Database và vận hành

Schema dùng khóa ngoại, unique constraint, check constraint và index cho tra cứu
lô/tồn. `BEGIN IMMEDIATE` khóa transaction ghi trên SQLite để hai yêu cầu xác
nhận đồng thời không thể cùng tiêu thụ một lượng tồn. Các kiểu dữ liệu và quan
hệ được tổ chức để có thể chuyển sang SQL Server bằng migration; khi dùng SQL
Server thật cần thay adapter SQLite bằng SQLAlchemy/pyodbc trong cấu hình triển
khai. `DATABASE_URL` hiện chỉ giúp CLI nhận biết để từ chối thao tác sao chép
file; ứng dụng **chưa kết nối hay được kiểm thử tích hợp với SQL Server**.

```powershell
# Bổ sung seed nếu database đã có từ phiên bản cũ
flask --app app seed-demo

# Backup nhất quán bằng SQLite backup API
flask --app app backup-db .\backups\dnp-wms.sqlite3

# Restore yêu cầu xác nhận rõ ràng
# Dừng các tiến trình web đang dùng database trước khi chạy lệnh này.
flask --app app restore-db .\backups\dnp-wms.sqlite3 --yes
```

Với SQL Server, dùng `BACKUP DATABASE`/`RESTORE DATABASE` theo quyền của DBA;
CLI sẽ không giả vờ sao chép file database server.

## Kiểm thử

```powershell
python -m pytest -q
```

24 test hiện tại bao phủ 10 màn hình demo, CRUD, tìm kiếm/lọc, auth/RBAC/CSRF,
email hợp đồng, nhập kho, xuất kho FEFO/FIFO, idempotency, rollback khi thiếu
tồn, kiểm kê, báo cáo CSV và chặn mutation của quy trình phiếu xuất cũ.

Nếu máy có thư mục tạm bị hạn chế quyền, có thể chạy không ghi cache:

```powershell
python -m pytest -q -p no:cacheprovider
```

## Cấu trúc

```text
app.py              App factory, API tương thích và schema ban đầu
wms.py              Auth, RBAC, schema WMS, transaction, API mới và CLI
templates/          Giao diện Jinja và trang đăng nhập
static/              Design system, responsive và kết nối API/CSRF
tests/               Test hồi quy và test nghiệp vụ trọng yếu
requirements.txt    Dependency được khóa phiên bản
```
