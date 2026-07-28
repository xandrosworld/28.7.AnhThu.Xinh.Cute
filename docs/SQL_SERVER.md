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

## Bằng chứng CI baseline

[GitHub Actions run 30346223947](https://github.com/xandrosworld/28.7.AnhThu.Xinh.Cute/actions/runs/30346223947)
tại SHA `c4a2ad80fd2a5b894f6969d2604359786add8f87` là bằng chứng của
**baseline trước migration hiện tại** và đã thành công:

- SQLite/Python 3.10: đạt.
- SQLite/Python 3.12: đạt.
- SQL Server 2022 + ODBC Driver 18: **67 passed, 2 skipped**.
- Hai skip chỉ là test CLI backup/restore dành riêng cho SQLite trong
  `tests/test_cli.py`.
- Test cạnh tranh xác nhận xuất dùng hai transaction/kết nối độc lập đã chạy
  và đạt trên SQL Server thật.

Revision hiện tại bổ sung migration ràng buộc vai trò/đơn vị tính và inspection
bắt buộc, vì vậy phải chạy lại job SQL Server sau khi push. Không kế thừa trạng thái
đạt của baseline cho revision mới; chỉ cập nhật tài liệu nghiệm thu khi có URL run
và SHA tương ứng.

## Backup và kiểm tra phục hồi

Development SQLite dùng CLI backup/restore của ứng dụng. Với SQL Server, tài
khoản thực hiện cần quyền backup phù hợp và thư mục đích phải được SQL Server
service account cho phép ghi. Thay đường dẫn, tên database và ngày giờ theo môi
trường thật.

Tạo full backup có checksum:

```sql
BACKUP DATABASE [dnp_wms]
TO DISK = N'D:\SQLBackups\dnp_wms_full_20260728.bak'
WITH COPY_ONLY, COMPRESSION, CHECKSUM, INIT, STATS = 10;
GO

RESTORE VERIFYONLY
FROM DISK = N'D:\SQLBackups\dnp_wms_full_20260728.bak'
WITH CHECKSUM;
GO
```

Nếu dùng recovery model `FULL`, bổ sung backup transaction log theo lịch:

```sql
BACKUP LOG [dnp_wms]
TO DISK = N'D:\SQLBackups\dnp_wms_log_20260728_1200.trn'
WITH COMPRESSION, CHECKSUM, INIT, STATS = 10;
GO
```

Không kiểm tra bằng cách ghi đè database đang vận hành. Trước hết lấy logical
file name trong bản backup:

```sql
RESTORE FILELISTONLY
FROM DISK = N'D:\SQLBackups\dnp_wms_full_20260728.bak';
GO
```

Sau đó thay `<logical_data_name>` và `<logical_log_name>` bằng hai giá trị trả
về, rồi phục hồi sang database kiểm tra riêng:

```sql
RESTORE DATABASE [dnp_wms_restore_check]
FROM DISK = N'D:\SQLBackups\dnp_wms_full_20260728.bak'
WITH
  MOVE N'<logical_data_name>' TO N'D:\SQLData\dnp_wms_restore_check.mdf',
  MOVE N'<logical_log_name>' TO N'D:\SQLData\dnp_wms_restore_check_log.ldf',
  RECOVERY, CHECKSUM, REPLACE, STATS = 10;
GO

DBCC CHECKDB ([dnp_wms_restore_check]) WITH NO_INFOMSGS;
GO
```

Biên bản backup cần ghi thời điểm, người thực hiện, kích thước file, kết quả
`RESTORE VERIFYONLY`, kết quả `DBCC CHECKDB` và thời gian phục hồi. Chỉ coi quy
trình đạt khi phục hồi thử trên database riêng thành công; không commit file
`.bak`, `.trn`, mật khẩu hay chuỗi kết nối thật vào Git.
