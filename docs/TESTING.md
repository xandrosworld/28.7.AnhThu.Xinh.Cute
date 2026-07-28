# Chiến lược kiểm thử

## Các lớp kiểm thử

- **Unit/service:** validate đầu vào, phân bổ FEFO/FIFO, tổng hợp số lượng và
  quy tắc trạng thái.
- **API integration:** auth/RBAC/CSRF, CRUD, transaction, idempotency, rollback
  và cấu trúc lỗi.
- **Database:** migration từ database trống, unique/FK/check, decimal, không âm,
  lot aggregate và hành vi trên SQLite/SQL Server.
- **HTTP/DOM smoke:** mọi route màn hình trả HTML đúng, static asset tồn tại,
  không có link placeholder hoặc lỗi JavaScript cú pháp.
- **E2E thủ công:** ba vai trò, camera/USB barcode, responsive và in ấn trên
  Chrome/Edge/Firefox.

## Quy tắc chống test giả

- Mỗi test dùng database cô lập và rollback/xóa sau test.
- Không phụ thuộc thứ tự, seed của test khác hoặc Internet.
- Khi kiểm tra idempotency phải so sánh cả số lượng tồn và số movement trước/sau.
- Khi kiểm tra rollback phải gây lỗi sau khi ít nhất một dòng đã được xử lý.
- Khi kiểm tra xuất đồng thời phải dùng hai transaction/kết nối độc lập.
- Test SQL Server chỉ được tính đạt khi kết nối tới SQL Server thật.

## Lệnh kiểm thử

Lệnh cài đặt và chạy chính xác nằm trong `README.md`. CI chạy:

- Python 3.10 và 3.12 với SQLite.
- SQL Server 2022 qua ODBC Driver 18.
- Coverage toàn package ứng dụng, ngưỡng 85%, và lưu XML artifact.
- Compile/static checks cùng kiểm tra route/asset.

Kết quả xác minh cuối trên Python 3.12/SQLite: **65 test đạt, 1 test concurrency
SQL Server được skip; coverage toàn package `app` đạt 87,48%**, vượt ngưỡng
CI 85%.

Playwright chỉ nên bật trong CI sau khi smoke test ổn định và browser binaries
được cache/cài rõ ràng. Cho đến lúc đó, checklist trình duyệt trong
`ACCEPTANCE_TESTS.md` vẫn là kiểm tra thủ công và được ghi đúng như vậy.
