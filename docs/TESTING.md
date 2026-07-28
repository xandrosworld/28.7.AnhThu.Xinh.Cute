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

Bằng chứng CI chính thức của bản nộp là
[main run 30349831432](https://github.com/xandrosworld/28.7.AnhThu.Xinh.Cute/actions/runs/30349831432),
SHA `ce80fafd52fbd5df4558aca18803276fdc9ccaed`, **thành công 6/6 job**:

- SQLite/Python 3.10: **78 passed, 1 skipped**.
- SQLite/Python 3.12: **78 passed, 1 skipped**, coverage **86,58%**.
- SQL Server 2022 + ODBC 18: **77 passed, 2 skipped**; chỉ hai test
  backup/restore SQLite bị skip, test concurrency hai transaction đã đạt.
- Playwright: **9/9 Chromium** và **9/9 Firefox** tại 1366/1024/390 px.
- Benchmark CI: `report_csv` có P95/lớn nhất **63,46 ms**, verdict PASS,
  thấp hơn ngưỡng 5 giây.

Run tích hợp [30349830487](https://github.com/xandrosworld/28.7.AnhThu.Xinh.Cute/actions/runs/30349830487)
trên cùng SHA cũng thành công 6/6. Camera thật, USB scanner và bản in vẫn cần
biên bản thủ công riêng.
