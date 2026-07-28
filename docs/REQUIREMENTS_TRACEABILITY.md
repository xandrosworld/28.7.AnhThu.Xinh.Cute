# Ma trận truy vết BR/NFR

Nguồn yêu cầu:
`49K21.1_NguyenHoangThanhTruc_Chuong3_KPI4.docx`. Ma trận này áp dụng cho bản
tích hợp; bằng chứng tự động phải được chạy lại trên clone sạch trước khi ghi
số lượng test vào báo cáo.

## Yêu cầu nghiệp vụ

| ID | Yêu cầu | Hiện thực / giao diện | Bằng chứng nghiệm thu |
|---|---|---|---|
| BR-001 | Đăng nhập | `/login`, `/api/auth/login`, `/logout`, `/me` | AT-01, AT-02 |
| BR-002 | Quản lý tài khoản | `/users`, `/api/users`; khóa/soft-delete | AT-14; test user CRUD/password hash |
| BR-003 | Phân quyền | `ADMIN`, `CS`, `WAREHOUSE`; kiểm tra quyền phía server | AT-01, AT-03; test RBAC |
| BR-004 | Quản lý danh mục | `/categories`, `/api/categories`; không xóa lịch sử | AT-14; test duplicate/soft-delete |
| BR-005 | Quản lý hàng hóa | `/products`, `/api/products`; SKU/barcode/đơn vị | AT-14, AT-15; test master data |
| BR-006 | Tạo phiếu nhập | `/inbound-receipts`; draft nhiều dòng, container/seal/pallet | AT-04 |
| BR-007 | Tạo phiếu xuất | `/outbound-receipts`; email hợp đồng, nhiều dòng | AT-07 |
| BR-008 | Kiểm tra tồn trước xuất | `check-stock`; kiểm tra lại trong transaction | AT-08, AT-11 |
| BR-009 | Tự cập nhật tồn | confirm nhập/xuất, lot và stock movement | AT-06, AT-08, AT-10 |
| BR-010 | Kho xác nhận nhập | inspect rồi confirm; accepted/rejected | AT-05, AT-06 |
| BR-011 | Kho xác nhận xuất | picking rồi confirm; chỉ kho/admin | AT-09, AT-11 |
| BR-012 | Xem tồn | `/inventory`, `/api/inventory`; tìm SKU/barcode/pallet/kho | test inventory filters/detail |
| BR-013 | Kiểm kê/đối chiếu | `/stocktakes`; snapshot, số đếm và lý do | AT-12, AT-13 |
| BR-014 | Tìm hàng | tìm mã/tên/barcode/pallet; phân trang và bộ lọc | test search/filter |
| BR-015 | Lịch sử/báo cáo | stock movement, audit, `/reports`, CSV và bản in | test report/export/audit |

## Yêu cầu phi chức năng

| ID | Yêu cầu | Cách đáp ứng | Bằng chứng |
|---|---|---|---|
| NFR-001 | Dễ sử dụng | Design system tiếng Việt, responsive, loading/empty/error/success | AT-19; checklist có biên bản |
| NFR-002 | Đăng nhập an toàn | Hash mật khẩu, HttpOnly/SameSite, CSRF, security headers | AT-02, AT-03; test auth/security |
| NFR-003 | Phân quyền | Decorator/policy ở server cho cả trang và API | test ma trận vai trò/endpoint |
| NFR-004 | Lưu bằng DB | SQLAlchemy/Alembic; SQLite và SQL Server | AT-17; **AT-18 đạt** trên [CI run 30346223947](https://github.com/xandrosworld/28.7.AnhThu.Xinh.Cute/actions/runs/30346223947) |
| NFR-005 | Phản hồi ≤5 giây | Index, phân trang; benchmark 1.012 sản phẩm, 5.010 lot và 5.000 movement | Đạt trên môi trường đo; xem `PERFORMANCE.md` |
| NFR-006 | Tìm nhanh | Index mã, barcode, trạng thái, ngày, khóa ngoại | test search + kiểm tra migration |
| NFR-007 | Chrome/Edge/Firefox | HTML/CSS/JS không phụ thuộc CDN; fallback barcode | AT-19 |
| NFR-008 | Backup định kỳ | CLI backup/restore SQLite; hướng dẫn SQL Server | AT-16 |
| NFR-009 | Toàn vẹn tồn | Decimal, constraint, transaction, lock/idempotency, không âm | AT-06, AT-08–AT-13; concurrency SQL Server đã đạt trên CI |
| NFR-010 | Lưu lịch sử | movement/audit ghi người, thời gian, tham chiếu và lý do | test audit/movement/report |

## Quy tắc nghiệp vụ trọng yếu

- Mã chứng từ, SKU, barcode và pallet ID là duy nhất trong phạm vi được mô hình
  quy định; API trả 409 khi xung đột.
- Chỉ số lượng nhập được chấp nhận mới tăng tồn; từ chối phải có lý do.
- Phiếu xuất chỉ nhận email hợp đồng đang hoạt động và không dùng lô hết hạn.
- Picking ưu tiên FEFO khi có hạn dùng, sau đó FIFO theo ngày nhận/lô.
- Hàng xuất giữ đơn vị của lô nhập; không tự quy đổi hoặc xé kiện.
- Confirm/cancel là idempotent theo nghĩa không tạo side effect lần hai.
- Xác nhận phiếu và kiểm kê là transaction nguyên tử; lỗi ở dòng cuối phải
  rollback cả tồn lẫn movement.
- Master đã được tham chiếu chỉ được ngừng hoạt động; lịch sử không bị xóa.

## Trạng thái xác minh trước nộp

- **AT-18 đã đạt:** [GitHub Actions run 30346223947](https://github.com/xandrosworld/28.7.AnhThu.Xinh.Cute/actions/runs/30346223947)
  tại SHA `c4a2ad80fd2a5b894f6969d2604359786add8f87` đã xanh; SQL Server 2022 +
  ODBC 18 đạt 67 passed, 2 SQLite-only skipped và test concurrency đã chạy/đạt.
- NFR-005 đã có log benchmark độc lập và JSON bằng chứng trong
  `PERFORMANCE.md`; phải đo lại nếu đổi database hoặc hạ tầng.
- AT-19 là kiểm tra trình duyệt thủ công cho đến khi có Playwright ổn định;
  không cộng vào số test tự động.
