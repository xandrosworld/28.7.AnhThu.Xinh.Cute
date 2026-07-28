# DNP Logistics WMS — Hàng hóa & Xuất kho

Phân hệ do **Lê Thảo** phụ trách trong đồ án quản lý kho. Bản này thay thế bộ 10
màn hình mô phỏng ban đầu bằng ứng dụng full-stack chạy thật với Flask và SQLite.

## Chức năng

- Quản lý hàng hóa: thêm, xem, sửa, xóa; phân trang; tìm theo SKU, barcode hoặc tên;
  lọc danh mục và trạng thái tồn.
- Quản lý danh mục: thêm, sửa và chỉ cho xóa danh mục chưa được sử dụng.
- Thống kê hàng còn tồn, sắp hết, hết hàng và tổng giá trị tồn.
- Quản lý phiếu xuất: tạo, xem, sửa, xóa, tìm kiếm và lọc trạng thái.
- Phiếu có nhiều dòng hàng, tự tính số lượng và tổng giá trị.
- Luồng trạng thái có kiểm soát:
  `Chờ duyệt → Đang xử lý → Hoàn thành` hoặc hủy trước khi hoàn thành.
- Biên bản kiểm tra lưu số lượng thực tế, tình trạng hàng, ghi chú, người và thời điểm
  kiểm tra cho từng dòng.
- Chỉ hoàn thành khi đủ tồn và biên bản kiểm tra đạt toàn bộ.
- Trừ tồn bằng transaction `BEGIN IMMEDIATE`; nếu một dòng không đủ thì toàn bộ giao
  dịch bị hủy, không thể xuất âm kho.
- Mỗi lần trừ tồn sinh một bản ghi `stock_movements` gồm tồn trước, biến động và tồn
  sau. Ràng buộc duy nhất ngăn một phiếu trừ tồn hai lần.
- Nhật ký trạng thái phục vụ truy vết.
- Giao diện responsive, hỗ trợ bàn phím, nhãn form, vùng thông báo và hộp xác nhận.

## Cài đặt và chạy

Yêu cầu Python 3.10 trở lên.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

Mở <http://127.0.0.1:5000>. Database được tạo tự động tại
`instance/dnp_wms.sqlite3` và có sẵn dữ liệu minh họa nhất quán.

Để tạo lại dữ liệu từ đầu, dừng ứng dụng, xóa file database trong thư mục
`instance`, rồi chạy lại. Đây là thao tác dành cho môi trường học tập cục bộ.

## 10 màn hình nghiệp vụ

| Màn hình | Đường dẫn |
|---|---|
| Danh sách hàng hóa | `/hang-hoa` |
| Thêm hàng hóa | `/hang-hoa/them` |
| Cập nhật hàng hóa | `/hang-hoa/<id>/sua` |
| Chi tiết hàng hóa | `/hang-hoa/<id>` |
| Danh sách phiếu xuất | `/xuat-kho` |
| Tạo phiếu xuất | `/xuat-kho/tao` |
| Chi tiết phiếu xuất | `/xuat-kho/<id>` |
| Chỉnh sửa phiếu xuất | `/xuat-kho/<id>/sua` |
| Kiểm tra xuất kho | `/xuat-kho/<id>/kiem-tra` |
| Lịch sử xuất kho | `/lich-su-xuat-kho` |

## Cấu trúc

```text
app.py                  Flask app factory, API, schema và seed
templates/              Giao diện Jinja cho 10 màn hình
static/app.css          Design system và responsive
static/app.js           Kết nối API, form, validation, tương tác
tests/                  Kiểm thử API, DB và nghiệp vụ xuất kho
requirements.txt        Phiên bản thư viện
```

Các bảng chính: `categories`, `products`, `outbound_orders`, `outbound_items`,
`outbound_inspections`, `order_history`, `stock_movements`.

## API chính

- `GET/POST /api/products`, `GET/PUT/DELETE /api/products/<id>`
- `GET/POST /api/categories`, `PATCH/DELETE /api/categories/<id>`
- `GET/POST /api/outbound-orders`, `GET/PUT/DELETE /api/outbound-orders/<id>`
- `PUT /api/outbound-orders/<id>/inspection`
- `POST /api/outbound-orders/<id>/status`
- `POST /api/outbound-orders/<id>/validate-stock`
- `GET /api/outbound-history`
- `GET /health`

API trả lỗi theo cấu trúc JSON thống nhất:

```json
{"error": "Nội dung lỗi", "details": null}
```

## Kiểm thử

```powershell
python -m pytest -q
```

Bộ test bao phủ render 10 màn hình, CRUD, tìm kiếm/lọc, ràng buộc danh mục, luồng
phiếu xuất, bắt buộc kiểm tra, giao dịch trừ tồn, audit và trường hợp không đủ tồn.

## Dữ liệu minh họa

Hệ thống tự tạo 5 danh mục, 10 hàng hóa và 3 phiếu xuất ở các trạng thái khác nhau.
Người thao tác minh họa: **Lê Thảo — Nhân viên kho**. Phân hệ này không triển khai
đăng nhập vì xác thực và người dùng thuộc phần việc của thành viên khác.
