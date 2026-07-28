# Bằng chứng hiệu năng NFR-005

## Kết luận

Benchmark CI của bản nộp ngày 28/07/2026 **đạt NFR-005**: tất cả request được
đo đều dưới 5 giây. Tác vụ chậm nhất là xuất CSV 5.000 movement, với P95 và
thời gian lớn nhất **63,46 ms**, verdict **PASS**. Bằng chứng nằm trong
[main run 30349831432](https://github.com/xandrosworld/28.7.AnhThu.Xinh.Cute/actions/runs/30349831432)
tại SHA `ce80fafd52fbd5df4558aca18803276fdc9ccaed`.

Đây là benchmark phía server bằng Flask test client. Phép đo bao gồm routing,
xác thực session, truy vấn SQLite, xử lý nghiệp vụ, serialization và tạo toàn
bộ response; không bao gồm độ trễ mạng hoặc thời gian render của trình duyệt.

## Bằng chứng nghiệm thu CI

| Thuộc tính | Kết quả |
|---|---|
| Workflow | Main run `30349831432`, thành công 6/6 job |
| Commit | `ce80fafd52fbd5df4558aca18803276fdc9ccaed` |
| Dataset | 1.012 sản phẩm, 5.010 lot/pallet, 5.000 stock movement |
| Tác vụ chậm nhất | Xuất CSV 5.000 movement |
| P95 / lớn nhất | 63,46 ms / 63,46 ms |
| Ngưỡng / verdict | `< 5.000 ms` / PASS |

Integration run `30349830487` trên cùng SHA cũng thành công 6/6.

## Môi trường và dữ liệu local bổ sung

| Thuộc tính | Giá trị đã đo |
|---|---|
| Thời điểm UTC | `2026-07-28T09:57:43.350288+00:00` |
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
| Kích thước database | 2.772.992 byte, khoảng 2,64 MiB |
| Thời gian sinh dữ liệu | 0,106386 giây |
| Cách lấy mẫu | 2 warmup + 7 lượt đo cho mỗi endpoint |
| Tiêu chí đạt | Mọi lượt đo `< 5,000 giây` |

Database được tạo trong thư mục tạm và xóa sau khi chạy. Benchmark không đọc,
ghi hoặc reset database demo của người dùng.

## Kết quả local chi tiết

| Tác vụ | Median | P95 | Lớn nhất | Response | Kết quả |
|---|---:|---:|---:|---:|---|
| Dashboard | 3,772 ms | 6,772 ms | 6,772 ms | 1.438 byte | Đạt |
| Trang tồn kho 50 dòng | 6,381 ms | 7,526 ms | 7,526 ms | 51.075 byte | Đạt |
| Tìm tồn theo SKU | 8,769 ms | 9,270 ms | 9,270 ms | 1.221 byte | Đạt |
| Tìm master hàng hóa | 2,803 ms | 3,035 ms | 3,035 ms | 1.087 byte | Đạt |
| Tổng hợp báo cáo | 7,241 ms | 8,245 ms | 8,245 ms | 25.456 byte | Đạt |
| Báo cáo lọc kho/hàng/khách | 5,034 ms | 5,790 ms | 5,790 ms | 364 byte | Đạt |
| Xuất CSV 5.000 movement | 48,695 ms | 71,778 ms | 71,778 ms | 470.073 byte | Đạt |

Kết quả máy đọc local đầy đủ, gồm bảy mẫu của từng endpoint, nằm tại
[`performance_results.json`](performance_results.json). Số **71,778 ms** trong
bảng trên là phép đo local bổ sung; kết luận nghiệm thu sử dụng benchmark CI
**63,46 ms** tại SHA bản nộp.

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

- Kết quả chứng minh yêu cầu thời gian phản hồi trên SQLite trong CI và cấu
  hình local đã ghi; không phải cam kết throughput cho số người dùng đồng thời
  không giới hạn.
- Benchmark hiệu năng chạy trên SQLite; SQL Server CI kiểm tra migration và
  tính đúng đắn riêng, không được diễn giải thành benchmark SQL Server.
- Độ trễ mạng, reverse proxy và trình duyệt cần được đo lại nếu triển khai lên
  hạ tầng khác.
