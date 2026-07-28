# Kịch bản demo 8–10 phút

## Chuẩn bị

1. Cài đặt, migrate và seed theo `README.md`.
2. Mở sẵn terminal tại thư mục dự án và trình duyệt tại trang đăng nhập.
3. Dùng database demo mới để kịch bản có kết quả lặp lại.

## Trình bày

1. **0:00–1:00 — Bài toán và kiến trúc:** giới thiệu một WMS thống nhất, ba
   vai trò, Flask modular monolith và database có migration.
2. **1:00–2:00 — Bảo mật:** đăng nhập `cs`, chỉ ra session, CSRF và menu theo
   quyền; thử mở trang quản trị để minh họa 403.
3. **2:00–4:00 — Nhập kho:** lập phiếu nhiều dòng có nhà cung cấp, kho,
   container/seal, pallet, barcode và hạn dùng; chuyển sang tài khoản kho để
   kiểm nhận accepted/rejected rồi xác nhận.
4. **4:00–6:30 — Xuất kho:** tạo phiếu bằng email hợp đồng, xem kiểm tra tồn và
   picking list FEFO/FIFO; quét barcode hoặc nhập bằng máy quét USB/thủ công;
   xác nhận xuất và chỉ ra tồn giảm.
5. **6:30–7:30 — Kiểm kê:** lập phiếu từ snapshot, nhập số thực đếm và lý do,
   xác nhận rồi xem stock movement/audit.
6. **7:30–8:30 — Báo cáo:** lọc theo thời gian/kho, xuất CSV UTF-8 và mở bản in
   phiếu/picking list.
7. **8:30–9:30 — Chất lượng:** chạy `python -m pytest`; mở CI và ma trận
   BR/NFR để truy vết yêu cầu tới test.
8. **9:30–10:00 — Đóng góp:** hiển thị lịch sử ba nhánh và phân công trong
   `CONTRIBUTION.md`.

## Phương án dự phòng

- Nếu trình duyệt không hỗ trợ `BarcodeDetector`, máy quét USB hoạt động như
  bàn phím; biểu mẫu luôn cho nhập barcode thủ công.
- Nếu máy chấm không có SQL Server/ODBC, dùng SQLite để demo. Hỗ trợ SQL Server
  được chứng minh bằng job CI riêng.
- Ứng dụng không cần Internet/CDN để chạy.
