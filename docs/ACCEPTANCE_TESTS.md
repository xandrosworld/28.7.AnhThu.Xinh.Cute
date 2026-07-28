# Kịch bản kiểm thử chấp nhận

Kết quả cục bộ cuối trên Python 3.12/SQLite: **78 test đạt, 1 test cạnh
tranh dành riêng cho SQL Server được skip; coverage package `app` đạt
86,58%**.
CI bản nộp tại
[main run 30349831432](https://github.com/xandrosworld/28.7.AnhThu.Xinh.Cute/actions/runs/30349831432),
SHA `ce80fafd52fbd5df4558aca18803276fdc9ccaed`, đã **thành công 6/6 job**.
SQL Server 2022 + ODBC 18 đạt **77 passed, 2 skipped**; hai skip chỉ là
backup/restore SQLite, còn test cạnh tranh đã chạy và đạt. SQLite/Python 3.10
và 3.12 đều đạt **78 passed, 1 skipped**; coverage đạt **86,58%**.

Playwright đạt đủ **18/18 project-cases**: **9/9 Chromium** và **9/9 Firefox**
tại 1366/1024/390 px. Run tích hợp
[30349830487](https://github.com/xandrosworld/28.7.AnhThu.Xinh.Cute/actions/runs/30349830487)
trên cùng SHA cũng thành công 6/6.

| ID | Vai trò / thao tác | Kết quả mong đợi | Trạng thái / bằng chứng |
|---|---|---|---|
| AT-01 | Khách mở trang/API bảo vệ | Trang chuyển login; API trả 401 có mã lỗi | Đạt — `tests/test_auth.py` |
| AT-02 | Đăng nhập sai hoặc tài khoản khóa | 401/403; không tạo phiên hợp lệ | Đạt — `tests/test_auth.py` |
| AT-03 | Gửi lệnh ghi thiếu/sai CSRF | 403; database không đổi | Đạt — auth/extended contract tests |
| AT-04 | CS tạo phiếu nhập nhiều dòng | Lưu draft cùng snapshot SKU/đơn vị | Đạt — receipt contract tests |
| AT-05 | Kho kiểm nhận thiếu/hỏng | accepted/rejected hợp lệ; rejected có lý do | Đạt — quality regression tests |
| AT-06 | Xác nhận nhập trước/sau inspection và xác nhận lặp | Trước inspection bị chặn; sau inspection chỉ cộng tồn/movement một lần | Đạt — `test_inbound_cannot_bypass_inspection` và idempotency |
| AT-07 | CS tạo phiếu xuất bằng email lạ | 422; chỉ rõ trường `request_email` | Đạt |
| AT-08 | Tạo/xác nhận xuất thiếu tồn | 409/422; không có dòng tồn âm | Đạt |
| AT-09 | Bắt đầu picking, FEFO/FIFO hoặc từ chối | Chỉ kho/admin chuyển picking/rejected; bỏ lô hết hạn | Đạt — `tests/test_outbound_states_dashboard.py` và service tests |
| AT-10 | Lỗi ở dòng xuất sau cùng | Rollback toàn phiếu và movement | Đạt |
| AT-11 | Hai yêu cầu xuất cạnh tranh | Tối đa một yêu cầu thành công khi tổng vượt tồn | Đạt trên SQL Server thật tại CI bản nộp |
| AT-12 | Kiểm kê thập phân từ snapshot đã cũ | Giữ decimal; snapshot cũ trả 409, không ghi đè | Đạt — `test_decimal_adjustment_and_stocktake_are_end_to_end` |
| AT-13 | Xác nhận/hủy lặp | Không tạo side effect lần hai | Đạt |
| AT-14 | Cấu hình/master đã phát sinh | Vai trò/đơn vị có FK; chuyển inactive hoặc 409, không mất lịch sử | Đạt — high-gap + settings UI tests |
| AT-15 | JSON, số, ngày sai kiểu | 400/422 có `error.code/message/fields` | Đạt |
| AT-16 | Backup → đổi dữ liệu → restore | Dữ liệu và integrity check trở lại hợp lệ | Đạt SQLite |
| AT-17 | Migrate/seed chạy trên SQLite | Hoàn tất từ database trống | Đạt local |
| AT-18 | Migrate/test trên SQL Server 2022 | ODBC 18: 77 passed, 2 SQLite-only skipped; concurrency đạt | **Đạt** — [main run 30349831432](https://github.com/xandrosworld/28.7.AnhThu.Xinh.Cute/actions/runs/30349831432), SHA `ce80fafd52fbd5df4558aca18803276fdc9ccaed` |
| AT-19 | Chromium/Firefox, 390/1024/1366 px | Không tràn; auth/RBAC/UI mutation, bàn phím/focus rõ | **Đạt** — Chromium 9/9, Firefox 9/9 tại CI bản nộp |

## Điều kiện đạt trước khi nộp

- Toàn bộ test tự động đạt trên clone sạch và không có warning bị bỏ qua vì
  sai cấu hình.
- Coverage service/API nghiệp vụ trọng yếu đạt tối thiểu 85%; báo cáo coverage
  lưu làm artifact CI.
- Không có secret, database thật, `.env`, cache hoặc file backup trong Git.
- Kết quả Playwright phải kèm browser/project, viewport, ngày chạy và URL CI
  hoặc log local; camera/USB/in vật lý vẫn cần biên bản thủ công.
