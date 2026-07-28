# DNP WMS – Phân hệ nhập kho

Phần bài làm của **Thanh Trúc** đã được hoàn thiện thành ứng dụng full-stack dùng Flask và SQLite. Hệ thống quản lý xuyên suốt quy trình: tạo phiếu nhập → kiểm tra chất lượng → hoàn tất nhập kho → cập nhật tồn → lịch sử và báo cáo.

## Chức năng nổi bật

- Dashboard lấy KPI, biểu đồ nhập kho, cơ cấu hàng và cảnh báo tồn từ database.
- CRUD phiếu nhập và nhiều dòng hàng hóa, kiểm tra dữ liệu ở cả trình duyệt và server.
- Checklist kiểm soát 7 tiêu chí, ghi nhận số lượng thực nhập và biên bản.
- Hoàn tất nhập kho bằng transaction; ràng buộc database và API idempotent ngăn cộng tồn hai lần.
- Nhật ký audit cho các thao tác tạo, sửa, xóa, kiểm tra và hoàn tất.
- Báo cáo theo thời gian, top hàng nhập, tổng giá trị và xuất CSV UTF-8.
- Giao diện responsive, trạng thái tải, toast lỗi/thành công và hỗ trợ điều hướng bàn phím.
- Bộ dữ liệu demo nhất quán và kiểm thử tự động cho API/nghiệp vụ trọng yếu.

## Cài đặt và chạy

Yêu cầu Python 3.10 trở lên.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python database.py
python app.py
```

Mở `http://127.0.0.1:5000`. Database được tạo tại `instance/wms.sqlite3`. Nếu muốn tạo lại dữ liệu mẫu, hãy xóa riêng file này rồi chạy lại `python database.py`.

> Không mở trực tiếp file HTML. Các trang là Flask template và cần chạy qua `app.py`.

## Chạy kiểm thử

```powershell
python -m unittest discover -s tests -v
```

Bộ test dùng database tạm riêng, không làm thay đổi dữ liệu demo.

## Cấu trúc

```text
app.py                 Flask app, trang và REST API
database.py            Khởi tạo/seed SQLite
schema.sql             Lược đồ, khóa ngoại, CHECK, UNIQUE, index
templates/             Các trang Jinja2
static/app.css         Design system và responsive
static/app.js          Kết nối API, validation, tương tác
tests/test_app.py      Test API, DB, transaction/idempotency
```

## Luồng nghiệp vụ

1. Nhân viên tạo phiếu nhập ở trạng thái `pending`.
2. Tại màn hình kiểm tra, nhân viên đánh giá đủ 7 tiêu chí và nhập số lượng thực tế.
3. Kết quả `fail` chuyển phiếu sang `rejected`; kết quả `pass` chuyển sang `inspecting`.
4. Chỉ phiếu đã kiểm tra đạt mới có thể hoàn tất. Server mở transaction, cộng tồn từng mặt hàng, ghi biến động kho và khóa phiếu ở `completed`.
5. Lệnh hoàn tất lặp lại trả kết quả an toàn, không tạo biến động hoặc cộng tồn lần hai.

## API chính

- `GET /api/dashboard`
- `GET|POST /api/receipts`
- `GET|PUT|DELETE /api/receipts/<id>`
- `POST /api/receipts/<id>/inspection`
- `POST /api/receipts/<id>/complete`
- `GET /api/history`
- `GET /api/reports`
- `GET /reports/export.csv`

Đây là bản chạy cục bộ phục vụ chấm bài; dự án không yêu cầu deploy.
