"""Append a verified implementation appendix to the supplied Chapter 3 DOCX.

The source document is opened read-only and the result is always saved to a
different path. Requires: python -m pip install python-docx
"""

from pathlib import Path

from docx import Document
from docx.enum.text import WD_BREAK
from docx.shared import Pt


ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(r"C:\Users\DELL\Downloads\49K21.1_NguyenHoangThanhTruc_Chuong3_KPI4.docx")
OUTPUT = ROOT / "docs" / "49K21.1_NguyenHoangThanhTruc_Chuong3_KPI4_HoanThien.docx"


def add_table(document, headers, rows):
    table = document.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    for index, header in enumerate(headers):
        table.rows[0].cells[index].text = header
    for row in rows:
        cells = table.add_row().cells
        for index, value in enumerate(row):
            cells[index].text = str(value)
    return table


def main():
    if not SOURCE.exists():
        raise FileNotFoundError(f"Không tìm thấy tài liệu nguồn: {SOURCE}")
    document = Document(SOURCE)
    document.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
    document.add_heading("PHỤ LỤC ĐỐI CHIẾU SẢN PHẨM HOÀN THIỆN", level=1)
    document.add_paragraph(
        "Phụ lục này được bổ sung từ kết quả hiện thực và kiểm thử trên nhánh "
        "Thanh_Truc. Nội dung gốc của báo cáo được giữ nguyên; file nguồn không "
        "bị ghi đè."
    )
    document.add_heading("1. Phạm vi đóng góp", level=2)
    document.add_paragraph(
        "Nguyễn Hoàng Thanh Trúc giữ vai trò Business Analyst, phụ trách phân "
        "tích yêu cầu, luồng nhập kho, báo cáo và kiểm thử chấp nhận. Nhánh cá "
        "nhân hiện thực độc lập luồng lập phiếu → kiểm tra → xác nhận → cập nhật tồn."
    )
    document.add_heading("2. Điểm hoàn thiện chính", level=2)
    for item in (
        "Session authentication, mật khẩu băm, tài khoản khóa, CSRF và RBAC ADMIN/CS/WAREHOUSE.",
        "Pallet ID duy nhất; barcode khớp master; bảo toàn đơn vị và hạn dùng theo dòng nhập.",
        "Ghi riêng số lượng chấp nhận/từ chối; phần từ chối bắt buộc có lý do.",
        "Transaction hoàn tất, stock movement và inventory lot; idempotency chống cộng tồn hai lần.",
        "Dashboard, audit history, báo cáo theo ngày, CSV UTF-8 và backup/restore có integrity check.",
    ):
        document.add_paragraph(item, style="List Bullet")

    document.add_heading("3. Kết quả kiểm thử đã xác nhận", level=2)
    document.add_paragraph(
        "Lệnh: python -m pytest -q — Kết quả tại thời điểm lập phụ lục: "
        "14/14 kiểm thử đạt; coverage app/database đạt 88%."
    )
    add_table(
        document,
        ("Nhóm", "Bằng chứng"),
        (
            ("Bảo mật", "Đăng nhập đúng/sai, tài khoản khóa, CSRF, security headers"),
            ("Phân quyền", "CS lập phiếu; Warehouse kiểm tra/xác nhận; truy cập sai vai trò trả 403"),
            ("Nhập kho", "Validation, pallet/barcode/unit, accepted/rejected và immutable completed receipt"),
            ("Toàn vẹn tồn", "Transaction, stock movement/lot, hoàn tất lặp không cộng tồn"),
            ("Báo cáo/vận hành", "Filter, CSV UTF-8 BOM, audit history, backup/restore round-trip"),
        ),
    )
    document.add_heading("4. Truy vết BR/NFR", level=2)
    document.add_paragraph(
        "Ma trận đầy đủ nằm tại docs/REQUIREMENTS_TRACEABILITY.md. Các yêu cầu "
        "xuất kho BR-007, BR-008, BR-011 thuộc nhánh Lê Thảo; CRUD quản trị đầy "
        "đủ thuộc nhánh Anh Thư. Phụ lục không tuyên bố các mục ngoài nhánh là đã hoàn tất."
    )
    add_table(
        document,
        ("Nhóm yêu cầu", "Kết quả nhánh Thanh_Truc"),
        (
            ("BR-001, BR-003, BR-006, BR-010, BR-012, BR-014, BR-015", "Đạt"),
            ("BR-002, BR-005, BR-009, BR-013", "Đạt phần liên quan nhập kho"),
            ("BR-004, BR-007, BR-008, BR-011", "Ngoài phạm vi nhánh"),
            ("NFR-001 đến NFR-010", "Có hiện thực/bằng chứng; browser/performance cần xác nhận trên máy chấm"),
        ),
    )
    document.add_heading("5. Hướng dẫn nghiệm thu nhanh", level=2)
    for index, step in enumerate((
        "Cài dependency: python -m pip install -r requirements.txt.",
        "Chạy: đặt SECRET_KEY rồi thực thi python app.py.",
        "Demo CS bằng cs / CS@12345; demo kho bằng warehouse / Kho@12345.",
        "Chạy kiểm thử: python -m pytest -q.",
        "Tham khảo docs/DEMO_GUIDE.md cho kịch bản thuyết trình 8–10 phút.",
    ), 1):
        document.add_paragraph(f"{index}. {step}")

    styles = document.styles
    styles["Normal"].font.name = "Times New Roman"
    styles["Normal"].font.size = Pt(13)
    document.core_properties.title = (
        "Chương 3 KPI4 — Bản hoàn thiện và phụ lục đối chiếu nhánh Thanh Trúc"
    )
    document.save(OUTPUT)
    print(OUTPUT)


if __name__ == "__main__":
    main()
