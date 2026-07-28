# Chạy với SQL Server 2022

Ứng dụng nhận kết nối qua `DATABASE_URL`. SQLite là mặc định để demo nhanh;
SQL Server là cấu hình tương thích với kiến trúc trong báo cáo.

## Chuỗi kết nối

Ví dụ dùng ODBC Driver 18:

```text
mssql+pyodbc://sa:<PASSWORD>@localhost:1433/dnp_wms?driver=ODBC+Driver+18+for+SQL+Server&TrustServerCertificate=yes
```

Không ghi mật khẩu thật vào `.env.example`, workflow, ảnh chụp hoặc Git. Trong
CI, mật khẩu service chỉ là giá trị tạm của job.

## Khởi tạo

1. Cài Microsoft ODBC Driver 18 và bảo đảm SQL Server đã sẵn sàng nhận kết nối.
2. Tạo database `dnp_wms`.
3. Đặt `DATABASE_URL` và `SECRET_KEY` trong biến môi trường.
4. Chạy migration tới revision mới nhất.
5. Chạy seed demo một lần rồi khởi động ứng dụng.

Các lệnh chính xác được duy trì trong `README.md` và workflow CI để tránh tài
liệu lệch với code. Job SQL Server phải thực sự chạy migration và test; không
được thay bằng SQLite dưới cùng tên job.

## Bằng chứng CI đã nghiệm thu

[GitHub Actions run 30346223947](https://github.com/xandrosworld/28.7.AnhThu.Xinh.Cute/actions/runs/30346223947)
tại SHA `c4a2ad80fd2a5b894f6969d2604359786add8f87` đã **thành công**:

- SQLite/Python 3.10: đạt.
- SQLite/Python 3.12: đạt.
- SQL Server 2022 + ODBC Driver 18: **67 passed, 2 skipped**.
- Hai skip chỉ là test CLI backup/restore dành riêng cho SQLite trong
  `tests/test_cli.py`.
- Test cạnh tranh xác nhận xuất dùng hai transaction/kết nối độc lập đã chạy
  và đạt trên SQL Server thật.

## Backup

- Development SQLite: dùng CLI backup/restore của ứng dụng.
- SQL Server: dùng chính sách backup `.bak`/transaction log của SQL Server và
  kiểm tra phục hồi định kỳ trên database khác.
- Không phục hồi đè database đang vận hành nếu chưa dừng ghi dữ liệu và có bản
  backup được xác minh.
