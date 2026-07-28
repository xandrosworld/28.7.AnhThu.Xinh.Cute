# Kịch bản kiểm thử chấp nhận

Chạy `python -m pytest -q`. Kết quả xác nhận gần nhất:
**14/14 test đạt, coverage 88%**.

| ID | Vai trò / thao tác | Kết quả mong đợi |
|---|---|---|
| AT-01 | Khách mở dashboard/API | Trang chuyển login; API 401 |
| AT-02 | Đăng nhập sai/tài khoản khóa | 401/403, không tạo session |
| AT-03 | CS tạo phiếu có pallet/barcode | Phiếu `pending`, snapshot đúng |
| AT-04 | Kho thử tạo phiếu | 403 |
| AT-05 | CS thử kiểm tra/xác nhận | 403 |
| AT-06 | Kho ghi accepted/rejected | Lưu sai lệch; rejected có lý do |
| AT-07 | Hoàn tất khi chưa kiểm tra đạt | 409, tồn không đổi |
| AT-08 | Hoàn tất phiếu đạt hai lần | Tồn chỉ tăng một lần |
| AT-09 | Sửa/xóa phiếu hoàn tất | 409 |
| AT-10 | Ghi dữ liệu thiếu CSRF | 403 |
| AT-11 | Tìm SKU/barcode/pallet | Đúng lot, đơn vị và số lượng |
| AT-12 | Xuất CSV theo ngày | UTF-8 BOM, đủ cột truy vết |
| AT-13 | Backup → đổi dữ liệu → restore | Dữ liệu trở về bản sao |

Trước khi nộp, kiểm tra thủ công Chrome/Edge/Firefox ở 1366, 1024 và 390 px;
điều hướng bàn phím; loading, empty, validation và error state; clone sạch rồi
cài/chạy/test lại.
