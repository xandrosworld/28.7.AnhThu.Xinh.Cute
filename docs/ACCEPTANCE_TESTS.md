# Kịch bản kiểm thử chấp nhận

Kết quả cục bộ cuối trên Python 3.12/SQLite: **64 test đạt, 1 test cạnh
tranh dành riêng cho SQL Server được skip; coverage package `app` đạt
87,48%**.
Kết quả SQL Server chỉ được ghi đạt sau khi job CI thật hoàn tất. Kiểm tra
trình duyệt thủ công không tính gộp vào số test pytest.

| ID | Vai trò / thao tác | Kết quả mong đợi |
|---|---|---|
| AT-01 | Khách mở trang/API bảo vệ | Trang chuyển login; API trả 401 có mã lỗi |
| AT-02 | Đăng nhập sai hoặc tài khoản khóa | 401/403; không tạo phiên hợp lệ |
| AT-03 | Gửi lệnh ghi thiếu/sai CSRF | 403; database không đổi |
| AT-04 | CS tạo phiếu nhập nhiều dòng | Lưu draft cùng snapshot SKU/đơn vị |
| AT-05 | Kho kiểm nhận thiếu/hỏng | accepted/rejected hợp lệ; rejected có lý do |
| AT-06 | Xác nhận nhập hai lần | Chỉ cộng tồn và ghi movement một lần |
| AT-07 | CS tạo phiếu xuất bằng email lạ | 422; chỉ rõ trường `request_email` |
| AT-08 | Tạo/xác nhận xuất thiếu tồn | 409/422; không có dòng tồn âm |
| AT-09 | Picking có lô còn hạn và hết hạn | Bỏ lô hết hạn; FEFO rồi FIFO |
| AT-10 | Lỗi ở dòng xuất sau cùng | Rollback toàn phiếu và movement |
| AT-11 | Hai yêu cầu xuất cạnh tranh | Tối đa một yêu cầu thành công khi tổng vượt tồn |
| AT-12 | Kiểm kê từ snapshot đã cũ | 409; không ghi đè thay đổi mới |
| AT-13 | Xác nhận/hủy lặp | Không tạo side effect lần hai |
| AT-14 | Xóa master đã phát sinh | Chuyển inactive hoặc 409; không mất lịch sử |
| AT-15 | JSON, số, ngày sai kiểu | 400/422 có `error.code/message/fields` |
| AT-16 | Backup → đổi dữ liệu → restore | Dữ liệu và integrity check trở lại hợp lệ |
| AT-17 | Migrate/seed chạy trên SQLite | Hoàn tất từ database trống |
| AT-18 | Migrate/test trên SQL Server 2022 | Job CI dùng ODBC 18 đạt |
| AT-19 | Chrome/Edge/Firefox, 390/1024/1366 px | Không tràn; bàn phím, focus và trạng thái rõ |

## Điều kiện đạt trước khi nộp

- Toàn bộ test tự động đạt trên clone sạch và không có warning bị bỏ qua vì
  sai cấu hình.
- Coverage service/API nghiệp vụ trọng yếu đạt tối thiểu 85%; báo cáo coverage
  lưu làm artifact CI.
- Không có secret, database thật, `.env`, cache hoặc file backup trong Git.
- Các mục kiểm tra trình duyệt thủ công có ngày, người kiểm tra và kết quả trong
  biên bản nghiệm thu; không ghi “Đạt” dựa chỉ trên suy đoán.
