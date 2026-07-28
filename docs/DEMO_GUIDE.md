# Kịch bản demo 8–10 phút

1. **0:00–1:00:** Bài toán số hóa nhập kho; vai trò BA của Thanh Trúc.
2. **1:00–2:00:** Đăng nhập `cs / CS@12345`; giải thích session, hash, CSRF, RBAC.
3. **2:00–4:00:** Lập phiếu có NCC, kho, container, seal, pallet, barcode, hạn dùng.
4. **4:00–6:30:** Đăng nhập `warehouse / Kho@12345`; kiểm tra 7 tiêu chí, ghi
   accepted/rejected và lý do; hoàn tất hai lần để chứng minh idempotency.
5. **6:30–8:00:** Xem lot/pallet, lịch sử, dashboard, báo cáo và CSV UTF-8.
6. **8:00–9:00:** Chạy `python -m pytest -q`; trình bày ma trận BR/NFR.
7. **9:00–10:00:** Nêu backup/restore và phạm vi: xuất kho ở Lê Thảo, nền tảng
   quản trị/FE ở Anh Thư.

Nếu camera barcode không hỗ trợ, máy quét USB nhập như bàn phím hoặc chọn mã
từ master. Ứng dụng không phụ thuộc Internet/CDN.
