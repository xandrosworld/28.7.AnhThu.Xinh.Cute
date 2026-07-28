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
- **Playwright E2E:** auth, RBAC/navigation và tạo master qua UI trên Chromium
  và Firefox tại 1366/1024/390 px; giữ screenshot/trace khi lỗi.
- **E2E thủ công bổ sung:** camera thật, máy quét USB và bản in vật lý.

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
- Playwright Chromium và Firefox tại ba viewport 1366/1024/390 px.
- Benchmark NFR-005, static/link/secret scan.
- Coverage toàn package ứng dụng, ngưỡng 85%, và lưu XML artifact.
- Compile/static checks cùng kiểm tra route/asset.

Kết quả xác minh cuối trên Python 3.12/SQLite: **78 test đạt, 1 test concurrency
SQL Server được skip; coverage toàn package `app` đạt 86,58%**, vượt ngưỡng
CI 85%.

Kết quả chính thức trên SQL Server 2022 + ODBC 18: **67 passed, 2 skipped** tại
[GitHub Actions run 30346223947](https://github.com/xandrosworld/28.7.AnhThu.Xinh.Cute/actions/runs/30346223947),
SHA `c4a2ad80fd2a5b894f6969d2604359786add8f87`. Đây là baseline trước các
migration/ràng buộc mới. Chỉ hai test backup/restore
SQLite trong `tests/test_cli.py` bị skip; test concurrency dùng hai
transaction/kết nối độc lập đã chạy và đạt. Cùng workflow, các job
SQLite/Python 3.10 và 3.12 cũng thành công. Revision hiện tại chờ CI mới nên
chưa kế thừa kết luận SQL Server này.

Playwright hiện có 3 kịch bản, 2 browser và 3 viewport, tổng cộng **18
project-cases**. Xác minh local bằng system Chrome đạt **9/9 Chromium cases**
tại 1366/1024/390 px. Firefox local và cả hai job Playwright trên CI đang chờ
chạy; AT-19 vì vậy mới đạt một phần. Lệnh cài/chạy chính xác nằm trong README.
Camera thật, USB scanner và bản in vẫn cần biên bản thủ công riêng.
