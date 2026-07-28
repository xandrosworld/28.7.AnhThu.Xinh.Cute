# Kịch bản demo 8–10 phút

## Chuẩn bị

1. Cài đặt, migrate và seed theo `README.md`.
2. Mở sẵn terminal tại thư mục dự án và trình duyệt tại trang đăng nhập.
3. Dùng database demo mới để kịch bản có kết quả lặp lại.

## Trình bày

1. **0:00–1:00 — Bài toán và kiến trúc:** giới thiệu một WMS thống nhất, ba
   vai trò, Flask modular monolith và database có migration.
2. **1:00–2:00 — Bảo mật và cấu hình:** đăng nhập `admin`, mở `/settings` để
   minh họa cấu hình vai trò/đơn vị tính và cơ chế inactive; đăng nhập `cs`,
   chỉ ra menu theo quyền và thử mở trang quản trị để minh họa 403.
3. **2:00–4:00 — Nhập kho:** lập phiếu nhiều dòng có nhà cung cấp, kho,
   container/seal, pallet, barcode và hạn dùng; chuyển sang tài khoản kho để
   thử xác nhận trước inspection để thấy bị chặn; sau đó kiểm nhận
   accepted/rejected có lý do rồi mới xác nhận.
4. **4:00–6:30 — Xuất kho:** tạo phiếu bằng email hợp đồng, xem kiểm tra tồn và
   chuyển sang trạng thái bắt đầu lấy hàng, xem picking list FEFO/FIFO; quét
   barcode hoặc nhập bằng máy quét USB/thủ công; minh họa từ chối có lý do hoặc
   xác nhận xuất và chỉ ra tồn giảm.
5. **6:30–7:30 — Kiểm kê:** lập phiếu từ snapshot, nhập số thực đếm thập phân
   và lý do, xác nhận rồi xem stock movement/audit.
6. **7:30–8:30 — Báo cáo:** lọc theo thời gian/kho, xuất CSV UTF-8 và mở bản in
   phiếu/picking list.
7. **8:30–9:30 — Chất lượng:** chỉ ra 6 KPI dashboard; chạy
   `python -m pytest` và `npm run test:e2e:list`; mở benchmark, CI và ma trận
   BR/NFR để truy vết yêu cầu tới test. Mở
   [main run 30349831432](https://github.com/xandrosworld/28.7.AnhThu.Xinh.Cute/actions/runs/30349831432)
   để chỉ ra 6/6 job xanh tại SHA `ce80fafd52fbd5df4558aca18803276fdc9ccaed`.
8. **9:30–10:00 — Đóng góp:** hiển thị lịch sử ba nhánh và phân công trong
   `CONTRIBUTION.md`.

## Phương án dự phòng

- Nếu trình duyệt không hỗ trợ `BarcodeDetector`, máy quét USB hoạt động như
  bàn phím; biểu mẫu luôn cho nhập barcode thủ công.
- Nếu máy chấm không có SQL Server/ODBC, dùng SQLite để demo. Hỗ trợ SQL Server
  được chứng minh bằng job CI riêng.
- Ứng dụng không cần Internet/CDN để chạy.
- Nếu máy demo không có browser Playwright, dùng bằng chứng CI đã đạt
  Chromium 9/9 và Firefox 9/9 cùng HTTP smoke.
