# Bằng chứng hiệu năng NFR-005

## Kết luận

Benchmark độc lập ngày 28/07/2026 **đạt NFR-005** trong môi trường đo: tất cả
request được đo đều dưới 5 giây. Request chậm nhất là xuất CSV 5.000 movement,
với thời gian lớn nhất **55,413 ms**.

Đây là benchmark phía server bằng Flask test client. Phép đo bao gồm routing,
xác thực session, truy vấn SQLite, xử lý nghiệp vụ, serialization và tạo toàn
bộ response; không bao gồm độ trễ mạng hoặc thời gian render của trình duyệt.

## Môi trường và dữ liệu

| Thuộc tính | Giá trị đã đo |
|---|---|
| Thời điểm UTC | `2026-07-28T08:46:45.894631+00:00` |
| Hệ điều hành | Windows 11 `10.0.22621` |
| CPU | Intel64 Family 6 Model 154; 12 logical CPUs |
| Python | CPython 3.12.10 |
| Flask | 3.1.1 |
| SQLAlchemy | 2.0.49 |
| SQLite | 3.49.1 |
| Pytest (môi trường xác minh) | 8.4.1 |
| Sản phẩm | 1.012, trong đó 1.000 bản ghi benchmark |
| Lot/pallet | 5.010 |
| Stock movement | 5.000 |
| Kích thước database | 2.756.608 byte, khoảng 2,63 MiB |
| Thời gian sinh dữ liệu | 0,073549 giây |
| Cách lấy mẫu | 2 warmup + 7 lượt đo cho mỗi endpoint |
| Tiêu chí đạt | Mọi lượt đo `< 5,000 giây` |

Database được tạo trong thư mục tạm và xóa sau khi chạy. Benchmark không đọc,
ghi hoặc reset database demo của người dùng.

## Kết quả

| Tác vụ | Median | P95 | Lớn nhất | Response | Kết quả |
|---|---:|---:|---:|---:|---|
| Dashboard | 1,909 ms | 2,189 ms | 2,189 ms | 1.304 byte | Đạt |
| Trang tồn kho 50 dòng | 4,181 ms | 4,334 ms | 4,334 ms | 51.075 byte | Đạt |
| Tìm tồn theo SKU | 5,337 ms | 5,797 ms | 5,797 ms | 1.221 byte | Đạt |
| Tìm master hàng hóa | 1,730 ms | 1,794 ms | 1,794 ms | 1.093 byte | Đạt |
| Tổng hợp báo cáo | 4,621 ms | 4,960 ms | 4,960 ms | 25.456 byte | Đạt |
| Báo cáo lọc kho/hàng/khách | 3,524 ms | 4,102 ms | 4,102 ms | 364 byte | Đạt |
| Xuất CSV 5.000 movement | 34,248 ms | 55,413 ms | 55,413 ms | 470.073 byte | Đạt |

Kết quả máy đọc đầy đủ, gồm bảy mẫu của từng endpoint, nằm tại
[`performance_results.json`](performance_results.json).

## Chạy lại

Từ thư mục gốc của dự án:

```powershell
pip install -r requirements.txt
python scripts\benchmark.py --json-output docs\performance_results.json
```

Tùy chọn quy mô và ngưỡng:

```powershell
python scripts\benchmark.py `
  --products 1000 `
  --lots 5000 `
  --movements 5000 `
  --warmups 2 `
  --iterations 7 `
  --threshold 5 `
  --json-output docs\performance_results.json
```

Script từ chối dataset dưới 5.000 lot hoặc 5.000 movement và trả exit code khác
0 nếu bất kỳ request đo nào chạm hoặc vượt ngưỡng.

## Giới hạn diễn giải

- Kết quả chứng minh yêu cầu thời gian phản hồi trên SQLite và cấu hình máy đã
  ghi; không phải cam kết throughput cho số người dùng đồng thời không giới hạn.
- Chưa benchmark SQL Server thật trên máy này vì môi trường không có
  Docker/SQL Server. Workflow CI vẫn kiểm tra migration và tính đúng đắn riêng.
- Độ trễ mạng, reverse proxy và trình duyệt cần được đo lại nếu triển khai lên
  hạ tầng khác.
