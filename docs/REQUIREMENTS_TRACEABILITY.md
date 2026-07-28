# Ma trận truy vết yêu cầu

Nguồn: `49K21.1_NguyenHoangThanhTruc_Chuong3_KPI4.docx`. “Đạt” là đã có
code/test trong nhánh; “Đạt một phần” là phần nhập kho đã có; “Ngoài nhánh”
được truy vết sang thành viên phụ trách.

## Yêu cầu nghiệp vụ

| ID | Yêu cầu | Hiện thực / bằng chứng | Trạng thái |
|---|---|---|---|
| BR-001 | Đăng nhập | `/login`, `/api/auth/*`, test auth | Đạt |
| BR-002 | Quản lý tài khoản | Seed/khóa; CRUD đầy đủ thuộc Anh Thư | Đạt một phần |
| BR-003 | Phân quyền | `ADMIN`, `CS`, `WAREHOUSE`, test RBAC | Đạt |
| BR-004 | Quản lý danh mục | Thuộc nhánh Anh Thư | Ngoài nhánh |
| BR-005 | Quản lý hàng hóa | Master SKU/barcode/đơn vị, test master | Đạt một phần |
| BR-006 | Tạo phiếu nhập | Nhiều dòng, seal, pallet, barcode | Đạt |
| BR-007 | Tạo phiếu xuất | Thuộc nhánh Lê Thảo | Ngoài nhánh |
| BR-008 | Kiểm tra tồn trước xuất | Thuộc nhánh Lê Thảo | Ngoài nhánh |
| BR-009 | Tự cập nhật tồn | Transaction, movement, lot, idempotency | Đạt phần nhập |
| BR-010 | Kho xác nhận nhập | Inspection/complete chỉ kho/admin | Đạt |
| BR-011 | Kho xác nhận xuất | Thuộc nhánh Lê Thảo | Ngoài nhánh |
| BR-012 | Xem tồn | `/api/inventory`, tìm theo lot/pallet | Đạt |
| BR-013 | Kiểm kê/đối chiếu | Accepted/rejected; kiểm kê tổng thể thuộc Anh Thư | Đạt một phần |
| BR-014 | Tìm hàng | SKU/tên/barcode/pallet và lọc phiếu | Đạt |
| BR-015 | Lịch sử/báo cáo | History, report, CSV UTF-8 | Đạt |

## Yêu cầu phi chức năng

| ID | Yêu cầu | Cách đáp ứng / bằng chứng |
|---|---|---|
| NFR-001 | Dễ sử dụng | Responsive, loading/toast/empty state, tiếng Việt |
| NFR-002 | Đăng nhập an toàn | Scrypt, HttpOnly/SameSite, CSRF, CSP; test security |
| NFR-003 | Phân quyền | Kiểm tra vai trò ở server; test RBAC |
| NFR-004 | Lưu bằng DB | SQLite FK/CHECK/UNIQUE/index/transaction |
| NFR-005 | Phản hồi ≤5 giây | Query có index; cần benchmark lại trên máy chấm |
| NFR-006 | Tìm nhanh | Index ngày/trạng thái/barcode/pallet |
| NFR-007 | Chrome/Edge/Firefox | HTML/CSS/JS chuẩn, không CDN; checklist thủ công |
| NFR-008 | Backup định kỳ | CLI backup/restore + integrity check; test round-trip |
| NFR-009 | Toàn vẹn tồn | `BEGIN IMMEDIATE`, idempotency, unique movement/lot |
| NFR-010 | Lưu lịch sử | Audit create/update/inspect/complete/delete |

## Quy tắc chính

- Pallet ID duy nhất; barcode phải khớp master; đơn vị được chụp lại tại lúc lập phiếu.
- Chỉ số lượng chấp nhận được cộng tồn; phần từ chối bắt buộc có lý do.
- Một phiếu chỉ hoàn tất/cộng tồn một lần; phiếu hoàn tất không sửa hoặc xóa.
