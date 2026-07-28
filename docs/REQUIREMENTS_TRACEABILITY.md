# Ma trận truy vết BR/NFR

Nguồn yêu cầu:
`49K21.1_NguyenHoangThanhTruc_Chuong3_KPI4_Goc.docx`. Ma trận này áp dụng cho bản
tích hợp; bằng chứng tự động phải được chạy lại trên clone sạch trước khi ghi
số lượng test vào báo cáo.

## Yêu cầu nghiệp vụ

| ID | Yêu cầu | Hiện thực / API, màn hình | Test tự động / nghiệm thu | Trạng thái |
|---|---|---|---|---|
| BR-001 | Đăng nhập | `/`, `/index.html`; `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/me` | `tests/test_auth.py::test_login_success_and_me`, `::test_login_validation_and_wrong_password`, `::test_locked_user_cannot_login`, `::test_logout_clears_session`; AT-01, AT-02 | Đạt |
| BR-002 | Quản lý tài khoản | `/users`, `/api/users`; khóa/soft-delete | `tests/test_management.py::test_user_crud_password_is_hashed`, `::test_admin_cannot_lock_or_delete_self`; AT-14 | Đạt |
| BR-003 | Phân quyền | `ADMIN`, `CS`, `WAREHOUSE`; kiểm tra quyền phía server | `tests/test_auth.py::test_protected_api_and_csrf`, `::test_page_redirect_and_admin_page_authorization`; `tests/test_extended_contracts.py::test_new_mutation_routes_enforce_csrf_and_role`; AT-01, AT-03 | Đạt |
| BR-004 | Quản lý danh mục | `/categories`, `/api/categories`; không xóa lịch sử tham chiếu | `tests/test_management.py::test_category_crud_and_duplicate_guard`, `::test_cannot_delete_category_in_use`; AT-14 | Đạt |
| BR-005 | Quản lý hàng hóa/cấu hình | `/products`, `/settings`; `/api/products`, `GET/POST/PUT /api/roles`, `GET/POST/PUT /api/units` | `tests/test_wms_operations.py::test_master_data_endpoints_create_validate_and_filter`; `tests/test_high_gap_regressions.py::test_role_and_unit_configuration_lifecycle`; `tests/test_settings_ui.py`; AT-14, AT-15 | Đạt |
| BR-006 | Tạo và xác nhận phiếu nhập | `/inbound-receipts`; draft nhiều dòng, container/seal/pallet; inspection bắt buộc | `tests/test_extended_contracts.py::test_draft_receipt_edit_detail_and_lock_after_submission`; `tests/test_high_gap_regressions.py::test_inbound_cannot_bypass_inspection`; `tests/test_wms_operations.py::test_inbound_confirmation_is_atomic_and_idempotent`; AT-04–AT-06 | Đạt |
| BR-007 | Tạo phiếu xuất | `/outbound-receipts`, `/api/outbound-receipts`; email hợp đồng, nhiều dòng | `tests/test_wms_operations.py::test_outbound_requires_contract_email_and_available_stock`; `tests/test_extended_contracts.py::test_partner_update_normalizes_contract_emails_and_controls_outbound`; AT-07 | Đạt |
| BR-008 | Kiểm tra tồn trước xuất | `GET /api/outbound-receipts/:id/check-stock`; kiểm tra lại trong transaction | `tests/test_wms_operations.py::test_inspection_stock_check_picking_and_cancel_contracts`, `::test_outbound_confirmation_rolls_back_all_lines_on_late_shortage`; `tests/test_quality_regressions.py::test_concurrent_outbound_confirmations_cannot_oversell`; AT-08, AT-11 | Đạt |
| BR-009 | Tự cập nhật tồn | confirm nhập/xuất, lot và stock movement | `tests/test_wms_operations.py::test_inbound_confirmation_is_atomic_and_idempotent`, `::test_outbound_confirmation_decrements_stock_and_feeds_reports`, `::test_outbound_confirmation_rolls_back_all_lines_on_late_shortage`; AT-06, AT-08, AT-10 | Đạt |
| BR-010 | Kho xác nhận nhập | `inspect` rồi `confirm`; accepted/rejected | `tests/test_quality_regressions.py::test_inbound_rejection_requires_reason_and_only_accepted_quantity_enters_stock`; `tests/test_wms_operations.py::test_warehouse_role_can_confirm_but_cs_cannot`; AT-05, AT-06 | Đạt |
| BR-011 | Kho xác nhận xuất | `picking-list`, `start-picking`, `reject` rồi `confirm`; chỉ kho/admin | `tests/test_outbound_states_dashboard.py::test_outbound_picking_and_rejection_state_machine`, `::test_cs_cannot_start_or_reject_picking`; `tests/test_services.py::test_picking_is_fefo_then_fifo_and_excludes_expired_lots`; concurrency test; AT-09, AT-11 | Đạt |
| BR-012 | Xem tồn | `/inventory`, `/api/inventory`; tồn hiện tại/khả dụng và lịch sử | `tests/test_inventory.py::test_inventory_filters_and_detail`; `tests/test_stock_movements_api.py::test_inventory_detail_has_movements_and_true_available_quantity` | Đạt |
| BR-013 | Kiểm kê/đối chiếu | `/stocktakes`, `/api/stocktakes`; decimal, snapshot, số đếm và lý do | `tests/test_high_gap_regressions.py::test_decimal_adjustment_and_stocktake_are_end_to_end`; `tests/test_wms_operations.py::test_stocktake_confirmation_is_idempotent`, `::test_inspection_duplicate_ids_and_stale_stocktake_are_rejected`; AT-12, AT-13 | Đạt |
| BR-014 | Tìm hàng | `/api/inventory`, `/api/stock-movements`; mã/tên/barcode/pallet, phân trang và bộ lọc | `tests/test_inventory.py::test_inventory_filters_and_detail`; `tests/test_stock_movements_api.py::test_stock_movements_filters_and_paginates` | Đạt |
| BR-015 | Dashboard/lịch sử/báo cáo | 6 KPI nhập/xuất/tồn, `/audit-logs`, `/reports`, stock movement, CSV và bản in | `tests/test_outbound_states_dashboard.py::test_dashboard_exposes_inventory_and_receipt_kpis`; `tests/test_quality_regressions.py::test_report_ui_and_csv_share_all_movement_filters`; audit/movement smoke tests | Đạt |

## Yêu cầu phi chức năng

| ID | Yêu cầu | Cách đáp ứng | Test / bằng chứng | Trạng thái |
|---|---|---|---|---|
| NFR-001 | Dễ sử dụng | Design system tiếng Việt, responsive, loading/empty/error/success | `tests/test_http_smoke.py::test_authenticated_route_and_local_asset_matrix`; `e2e/wms.spec.js`; AT-19 | Đạt: Chromium 9/9, Firefox 9/9 tại CI |
| NFR-002 | Đăng nhập an toàn | Hash mật khẩu, HttpOnly/SameSite, CSRF, security headers | `tests/test_auth.py::test_protected_api_and_csrf`; `tests/test_http_smoke.py::test_api_security_headers_and_success_envelope`; AT-02, AT-03 | Đạt |
| NFR-003 | Phân quyền | Decorator/policy ở server cho cả trang và API | `tests/test_auth.py::test_page_redirect_and_admin_page_authorization`; `tests/test_extended_contracts.py::test_new_mutation_routes_enforce_csrf_and_role` | Đạt |
| NFR-004 | Lưu bằng DB | SQLAlchemy/Alembic; FK role/unit, SQLite và SQL Server | `tests/test_high_gap_regressions.py::test_role_and_unit_foreign_keys_are_populated`; migration `b72f4d8a10c3`; AT-17, AT-18; [main run 30349831432](https://github.com/xandrosworld/28.7.AnhThu.Xinh.Cute/actions/runs/30349831432) | Đạt SQLite và SQL Server |
| NFR-005 | Phản hồi ≤5 giây | Index, phân trang; benchmark 1.012 sản phẩm, 5.010 lot và 5.000 movement | `scripts/benchmark.py`; CI `report_csv` P95/max 63,46 ms tại main run; `docs/performance_results.json`, `docs/PERFORMANCE.md` | Đạt CI |
| NFR-006 | Tìm nhanh | Index mã, barcode, trạng thái, ngày, khóa ngoại | `tests/test_inventory.py::test_inventory_filters_and_detail`; `tests/test_stock_movements_api.py::test_stock_movements_filters_and_paginates`; migration `9f4581f6e261` | Đạt |
| NFR-007 | Chromium/Firefox | Playwright 3 kịch bản × 2 browser × 3 viewport; không phụ thuộc CDN; fallback barcode | `playwright.config.js`, `e2e/wms.spec.js`; AT-19; main run 30349831432 | Đạt: Chromium 9/9 và Firefox 9/9 |
| NFR-008 | Backup định kỳ | CLI backup/restore SQLite; câu lệnh và checklist phục hồi SQL Server | `tests/test_cli.py::test_sqlite_backup_restore_round_trip`, `::test_backup_rejects_overwrite_and_restore_rejects_corruption`; `docs/SQL_SERVER.md` | Đạt trong phạm vi bàn giao |
| NFR-009 | Toàn vẹn tồn | Decimal end-to-end, inspection gate, constraint, transaction, lock/idempotency, không âm | `tests/test_high_gap_regressions.py::test_decimal_adjustment_and_stocktake_are_end_to_end`, `::test_inbound_cannot_bypass_inspection`; service/WMS/concurrency tests | Đạt, gồm concurrency trên SQL Server CI |
| NFR-010 | Lưu lịch sử | movement/audit ghi người, thời gian, tham chiếu và lý do | `tests/test_stock_movements_api.py`; `tests/test_management.py::test_audit_log_is_admin_only`; `tests/test_quality_regressions.py::test_report_ui_and_csv_share_all_movement_filters` | Đạt |

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

- **AT-18 đạt:** SQL Server **77 passed, 2 SQLite-only skipped** trong
  [main run 30349831432](https://github.com/xandrosworld/28.7.AnhThu.Xinh.Cute/actions/runs/30349831432)
  tại SHA `ce80fafd52fbd5df4558aca18803276fdc9ccaed`.
- **NFR-005 đạt:** benchmark CI có P95/lớn nhất `report_csv` **63,46 ms**,
  verdict PASS dưới 5 giây. JSON local chi tiết được giữ làm bằng chứng bổ sung.
- **AT-19 đạt:** suite có 18/18 project-cases; Chromium 9/9 và Firefox 9/9 tại
  1366/1024/390 px trong cùng main run. Camera/USB/in vật lý vẫn cần biên bản
  thủ công.
