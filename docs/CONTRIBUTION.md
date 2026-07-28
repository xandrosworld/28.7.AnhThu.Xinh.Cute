# Phân công và bằng chứng đóng góp

Nhóm giữ nguyên ba nhánh cá nhân và lịch sử commit. Bản tích hợp trên
`integration/wms-final` và `main` chỉ hợp nhất các phần đã được kiểm thử; không
đổi tác giả, không squash và không nhận thay phần việc của thành viên khác.

| Thành viên | Vai trò theo báo cáo | Trách nhiệm chính | Nhánh bằng chứng |
|---|---|---|---|
| Nguyễn Hoàng Thanh Trúc | BA | BR/NFR, luồng nghiệp vụ, tiêu chí chấp nhận, báo cáo, kiểm thử nghiệm thu | `Thanh_Truc` |
| Dương Thị Anh Thư | FE Developer | Design system, giao diện tiếng Việt, responsive, accessibility và trạng thái UX | `Anh_Thu` |
| Lê Phương Thảo | BE Developer | Mô hình dữ liệu, API, transaction và tính toàn vẹn tồn kho | `Le_Thao` |

## Nguyên tắc trình bày khi bảo vệ

- Dùng `git log --graph --all` để chỉ ra lịch sử thật của từng nhánh.
- Trình bày sản phẩm cuối trên `main`, không xem ba nhánh cá nhân là ba sản
  phẩm trùng lặp.
- Chỉ nhận một yêu cầu là “Đạt” khi có code chạy được và bằng chứng kiểm thử
  tương ứng trong [ma trận truy vết](REQUIREMENTS_TRACEABILITY.md).
- Những giới hạn môi trường như camera barcode và SQL Server được trình bày
  cùng cơ chế dự phòng, không tuyên bố đã chạy nếu chưa có bằng chứng CI.
