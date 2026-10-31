import sys
import os
import io
import re
import csv

from PyQt5 import uic, QtWidgets
from PyQt5.QtCore import QDate, Qt
from PyQt5.QtGui import QFont, QColor, QBrush
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QTableWidgetItem, QHeaderView, QVBoxLayout,
    QMessageBox, QFileDialog, QPushButton, QLabel, QFrame
)
import pymysql

# Biểu đồ: nếu máy chưa cài matplotlib thì vẫn chạy, chỉ không có biểu đồ
try:
    try:
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
    except Exception:
        from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
    from matplotlib.figure import Figure
    from matplotlib.ticker import FuncFormatter
    CO_MATPLOTLIB = True
except Exception:
    CO_MATPLOTLIB = False

# ===================== KẾT NỐI MYSQL =====================
DB_CONFIG = {
    "host": "localhost",
    "port": 3306,
    "user": "root",
    "password": "123456789",
    "database": "Cafe",
    "charset": "utf8mb4",
    "cursorclass": pymysql.cursors.DictCursor,
}

UI_FILE = "doanh_thu.ui"

# ===================== CỠ CHỮ (pt) =====================
CO_CHU_DAU_COT = 12
CO_CHU_O = 10
CAO_DONG_BANG = 40

# Đơn có trạng thái này thì không tính vào doanh thu
TRANG_THAI_HUY = ("Huy", "Hủy", "Da huy", "Đã hủy")

NHAN_COT_DAU = {"Ngày": "Ngày", "Tuần": "Ngày", "Tháng": "Ngày", "Năm": "Tháng"}
# Tiêu đề cột 2 của bảng: kỳ "Ngày" hiện cả ngày lẫn giờ
TIEU_DE_COT_DAU = {"Ngày": "Ngày / Giờ", "Tuần": "Ngày", "Tháng": "Ngày", "Năm": "Tháng"}
# Khung giờ luôn hiện trên biểu đồ khi xem theo ngày
GIO_MO_CUA, GIO_DONG_CUA = 8, 23


def doc_ui_tuong_thich(duong_dan):
    """Đọc file .ui và đổi kiểu enum của Qt6 về kiểu PyQt5 hiểu được,
    bỏ cỡ chữ -1 (gây cảnh báo 'Point size <= 0')."""
    with open(duong_dan, encoding="utf-8") as f:
        noi_dung = f.read()

    def sua(m):
        return m.group(1) + re.sub(r"(\w+)::\w+::(\w+)", r"\1::\2", m.group(2)) + m.group(3)

    noi_dung = re.sub(r"(<(?:enum|set)>)(.*?)(</(?:enum|set)>)", sua, noi_dung, flags=re.S)
    noi_dung = re.sub(r"\s*<pointsize>-1</pointsize>", "", noi_dung)
    return io.BytesIO(noi_dung.encode("utf-8"))


def dinh_dang_so(so):
    return f"{int(round(float(so or 0))):,}".replace(",", ".")


def dinh_dang_tien(so_tien):
    return dinh_dang_so(so_tien) + " VNĐ"


def ghep_chi_tiet(kieu, moc, du_lieu):
    """Ghép các mốc thời gian với số liệu lấy từ DB.
    moc: [(nhãn, khóa)]   du_lieu: {khóa: (số đơn, doanh thu)}
    """
    ket_qua = []
    for nhan, khoa in moc:
        so_don, doanh_thu = du_lieu.get(khoa, (0, 0))
        ket_qua.append((nhan, so_don, doanh_thu))
    return ket_qua


class DoanhThuWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        base_dir = os.path.dirname(os.path.abspath(__file__))
        os.chdir(base_dir)
        uic.loadUi(doc_ui_tuong_thich(os.path.join(base_dir, UI_FILE)), self)
        self.setWindowTitle("Báo cáo doanh thu")

        # Ngày đại diện cho kỳ thống kê đang chọn
        self.ngay_chon = QDate.currentDate()
        self.chi_tiet = []          # [(nhãn, số đơn, doanh thu)] đang hiển thị
        self.tong_don = 0
        self.tong_doanh_thu = 0
        self.da_bao_loi_db = False
        self.chi_tiet_gio = []      # [(giờ, số đơn, doanh thu)] của ngày đang xem (chỉ kỳ 'Ngày')
        self.dong_bang = []         # các dòng đang hiển thị trong bảng (dùng cho Excel)

        self.cau_hinh_menu()
        self.cau_hinh_bieu_do()
        self.cau_hinh_bang()
        self.cau_hinh_combo()
        self.noi_su_kien()

        # Hiển thị báo cáo ban đầu
        self.cap_nhat_thoi_gian()

    # ------------------------------------------------------------------
    # CẤU HÌNH GIAO DIỆN
    def cau_hinh_menu(self):
        """Sidebar giống các trang còn lại."""
        frame = self.findChild(QFrame, "frame")
        if frame is not None:
            frame.setMinimumWidth(230)
            frame.setMaximumWidth(280)
        b = self.findChild(QPushButton, "btnTimKiemDonHang")
        if b is not None:
            b.setText("Tìm kiếm ")

    def cau_hinh_bieu_do(self):
        layout = self.widgetBieuDo.layout()
        if layout is None:
            layout = QVBoxLayout(self.widgetBieuDo)
        layout.setContentsMargins(5, 5, 5, 5)
        self.widgetBieuDo.setMinimumHeight(260)

        self.figure = None
        self.canvas = None
        if CO_MATPLOTLIB:
            self.figure = Figure(figsize=(6, 4))
            self.canvas = FigureCanvasQTAgg(self.figure)
            layout.addWidget(self.canvas)
        else:
            lb = QLabel("Chưa cài matplotlib nên không vẽ được biểu đồ.\n"
                        "Mở CMD gõ:  python -m pip install matplotlib")
            lb.setAlignment(Qt.AlignCenter)
            layout.addWidget(lb)

    def cau_hinh_bang(self):
        self.table = self.tableDoanhThu
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["STT", "Ngày/Tháng", "Số đơn hàng", "Doanh thu"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)

        f_o = QFont(self.table.font())
        f_o.setPointSize(CO_CHU_O)
        self.table.setFont(f_o)
        f_dau = QFont(f_o)
        f_dau.setPointSize(CO_CHU_DAU_COT)
        f_dau.setBold(True)
        self.table.horizontalHeader().setFont(f_dau)

    def cau_hinh_combo(self):
        self.cboDate.blockSignals(True)
        self.cboDate.clear()
        self.cboDate.addItems(["Ngày", "Tuần", "Tháng", "Năm"])
        self.cboDate.setCurrentText("Ngày")
        self.cboDate.blockSignals(False)

    def noi_su_kien(self):
        self.cboDate.currentTextChanged.connect(self.cap_nhat_thoi_gian)
        self.btnTruoc.clicked.connect(self.ky_truoc)
        self.btnSau.clicked.connect(self.ky_sau)
        self.btnXemBaoCao.clicked.connect(self.xem_bao_cao)
        self.btnXuatExcel.clicked.connect(self.xuat_excel)

        # Menu bên trái
        def noi(ten, ham):
            b = self.findChild(QPushButton, ten)
            if b is not None:
                b.clicked.connect(ham)
        noi("btnDonHang", self.mo_lap_don_hang)
        noi("btnNguyenLieu", self.mo_nhap_nguyen_lieu)
        noi("btnTimKiemDonHang", self.mo_tim_kiem)
        noi("btnBaoCao", self.mo_bao_cao)

    # ------------------------------------------------------------------
    # THỜI GIAN
    def lay_khoang_thoi_gian(self):
        kieu = self.cboDate.currentText()
        ngay = self.ngay_chon

        if kieu == "Ngày":
            return ngay, ngay
        if kieu == "Tuần":
            dau = ngay.addDays(1 - ngay.dayOfWeek())
            return dau, dau.addDays(6)
        if kieu == "Tháng":
            dau = QDate(ngay.year(), ngay.month(), 1)
            cuoi = QDate(ngay.year(), ngay.month(), ngay.daysInMonth())
            return dau, cuoi
        return QDate(ngay.year(), 1, 1), QDate(ngay.year(), 12, 31)

    def lay_dau_ky(self, ngay):
        kieu = self.cboDate.currentText()
        if kieu == "Ngày":
            return ngay
        if kieu == "Tuần":
            return ngay.addDays(1 - ngay.dayOfWeek())
        if kieu == "Tháng":
            return QDate(ngay.year(), ngay.month(), 1)
        return QDate(ngay.year(), 1, 1)

    def cap_nhat_thoi_gian(self, *_):
        dau, cuoi = self.lay_khoang_thoi_gian()
        if dau == cuoi:
            text = dau.toString("dd/MM/yyyy")
        else:
            text = f"{dau.toString('dd/MM/yyyy')} - {cuoi.toString('dd/MM/yyyy')}"
        self.lbDate.setText(f"Thời gian: {text}")

        # Không cho chuyển sang kỳ tương lai
        dau_hien_tai = self.lay_dau_ky(QDate.currentDate())
        self.btnSau.setEnabled(dau < dau_hien_tai)

        self.xem_bao_cao()

    def ky_truoc(self):
        kieu = self.cboDate.currentText()
        if kieu == "Ngày":
            self.ngay_chon = self.ngay_chon.addDays(-1)
        elif kieu == "Tuần":
            self.ngay_chon = self.ngay_chon.addDays(-7)
        elif kieu == "Tháng":
            self.ngay_chon = self.ngay_chon.addMonths(-1)
        else:
            self.ngay_chon = self.ngay_chon.addYears(-1)
        self.cap_nhat_thoi_gian()

    def ky_sau(self):
        kieu = self.cboDate.currentText()
        if kieu == "Ngày":
            ngay_moi = self.ngay_chon.addDays(1)
        elif kieu == "Tuần":
            ngay_moi = self.ngay_chon.addDays(7)
        elif kieu == "Tháng":
            ngay_moi = self.ngay_chon.addMonths(1)
        else:
            ngay_moi = self.ngay_chon.addYears(1)

        if self.lay_dau_ky(ngay_moi) > self.lay_dau_ky(QDate.currentDate()):
            return
        self.ngay_chon = ngay_moi
        self.cap_nhat_thoi_gian()

    def tao_moc_thoi_gian(self, dau, cuoi):
        """Trả về [(nhãn hiển thị, khóa để ghép với dữ liệu DB)]."""
        kieu = self.cboDate.currentText()
        moc = []
        if kieu == "Ngày":
            moc.append((dau.toString("dd/MM/yyyy"), dau.toString("yyyy-MM-dd")))
        elif kieu == "Tuần":
            for i in range(7):
                ngay = dau.addDays(i)
                moc.append((ngay.toString("dd/MM"), ngay.toString("yyyy-MM-dd")))
        elif kieu == "Tháng":
            for i in range(dau.daysInMonth()):
                ngay = dau.addDays(i)
                moc.append((ngay.toString("dd"), ngay.toString("yyyy-MM-dd")))
        else:
            for thang in range(1, 13):
                moc.append((f"Tháng {thang}", thang))
        return moc

    # ------------------------------------------------------------------
    # DATABASE
    def lay_du_lieu_bao_cao(self, dau, cuoi, moc):
        """Đọc bảng don_hang, gom theo ngày / tháng.
        Trả về: tổng đơn, tổng doanh thu, [(nhãn, số đơn, doanh thu)]"""
        kieu = self.cboDate.currentText()
        bieu_thuc = {
            "Ngày": "DATE(NgayDat)",
            "Tuần": "DATE(NgayDat)",
            "Tháng": "DATE(NgayDat)",
            "Năm": "MONTH(NgayDat)",
        }[kieu]
        tu = dau.toString("yyyy-MM-dd") + " 00:00:00"
        den = cuoi.addDays(1).toString("yyyy-MM-dd") + " 00:00:00"
        cho = ",".join(["%s"] * len(TRANG_THAI_HUY))
        sql = f"""
            SELECT {bieu_thuc} AS khoa,
                   COUNT(*) AS so_don,
                   COALESCE(SUM(ThanhTien), 0) AS doanh_thu
            FROM don_hang
            WHERE NgayDat >= %s AND NgayDat < %s
              AND TrangThai NOT IN ({cho})
            GROUP BY khoa
            ORDER BY khoa
        """

        du_lieu = {}
        try:
            conn = pymysql.connect(**DB_CONFIG)
            try:
                with conn.cursor() as cur:
                    cur.execute(sql, [tu, den, *TRANG_THAI_HUY])
                    for r in cur.fetchall():
                        khoa = r["khoa"]
                        if hasattr(khoa, "isoformat"):      # kiểu DATE
                            khoa = khoa.isoformat()
                        du_lieu[khoa] = (int(r["so_don"]), float(r["doanh_thu"]))
            finally:
                conn.close()
        except Exception as e:
            if not self.da_bao_loi_db:
                self.da_bao_loi_db = True
                QMessageBox.warning(self, "Lỗi Database", f"Không đọc được doanh thu:\n{e}")

        chi_tiet = ghep_chi_tiet(kieu, moc, du_lieu)
        tong_don = sum(x[1] for x in chi_tiet)
        tong_doanh_thu = sum(x[2] for x in chi_tiet)
        return tong_don, tong_doanh_thu, chi_tiet

    def lay_theo_gio(self, ngay):
        """Doanh thu của 1 ngày chia theo giờ: [(giờ, số đơn, doanh thu)], chỉ các giờ có đơn."""
        cho = ",".join(["%s"] * len(TRANG_THAI_HUY))
        sql = f"""
            SELECT HOUR(NgayDat) AS gio,
                   COUNT(*) AS so_don,
                   COALESCE(SUM(ThanhTien), 0) AS doanh_thu
            FROM don_hang
            WHERE NgayDat >= %s AND NgayDat < %s
              AND TrangThai NOT IN ({cho})
            GROUP BY gio
            ORDER BY gio
        """
        tu = ngay.toString("yyyy-MM-dd") + " 00:00:00"
        den = ngay.addDays(1).toString("yyyy-MM-dd") + " 00:00:00"
        ket_qua = []
        try:
            conn = pymysql.connect(**DB_CONFIG)
            try:
                with conn.cursor() as cur:
                    cur.execute(sql, [tu, den, *TRANG_THAI_HUY])
                    for r in cur.fetchall():
                        ket_qua.append((int(r["gio"]), int(r["so_don"]), float(r["doanh_thu"])))
            finally:
                conn.close()
        except Exception as e:
            if not self.da_bao_loi_db:
                self.da_bao_loi_db = True
                QMessageBox.warning(self, "Lỗi Database", f"Không đọc được doanh thu theo giờ:\n{e}")
        return ket_qua

    # ------------------------------------------------------------------
    # HIỂN THỊ
    def xem_bao_cao(self):
        dau, cuoi = self.lay_khoang_thoi_gian()
        moc = self.tao_moc_thoi_gian(dau, cuoi)
        kieu = self.cboDate.currentText()

        self.tong_don, self.tong_doanh_thu, self.chi_tiet = \
            self.lay_du_lieu_bao_cao(dau, cuoi, moc)

        self.chi_tiet_gio = self.lay_theo_gio(dau) if kieu == "Ngày" else []

        self.lb_tong_don.setText(dinh_dang_so(self.tong_don))
        self.lb_tong_doanh_thu.setText(dinh_dang_so(self.tong_doanh_thu))

        # Bảng: kỳ "Ngày" = 1 dòng tổng của ngày + các dòng theo giờ có đơn
        self.dong_bang = self.tao_dong_bang(kieu)
        self.table.setHorizontalHeaderLabels(
            ["STT", TIEU_DE_COT_DAU.get(kieu, "Ngày/Tháng"), "Số đơn hàng", "Doanh thu"]
        )
        self.table.setRowCount(len(self.dong_bang))
        for row, (stt, nhan, so_don, doanh_thu, la_tong) in enumerate(self.dong_bang):
            cac_o = [str(stt), nhan, dinh_dang_so(so_don), dinh_dang_tien(doanh_thu)]
            self.table.setRowHeight(row, CAO_DONG_BANG)
            for col, gia_tri in enumerate(cac_o):
                item = QTableWidgetItem(gia_tri)
                item.setTextAlignment(
                    (Qt.AlignRight if col >= 2 else Qt.AlignCenter) | Qt.AlignVCenter
                )
                if la_tong:
                    f = QFont(item.font())
                    f.setBold(True)
                    item.setFont(f)
                    item.setBackground(QBrush(QColor("#EDE1D4")))
                self.table.setItem(row, col, item)

        self.ve_bieu_do(kieu)

    def tao_dong_bang(self, kieu):
        """[(STT, nhãn, số đơn, doanh thu, là dòng tổng?)] dùng cho bảng và Excel."""
        if kieu != "Ngày":
            return [(i + 1, n, s, d, False) for i, (n, s, d) in enumerate(self.chi_tiet)]
        dong = []
        for nhan, so_don, doanh_thu in self.chi_tiet:          # đúng 1 dòng: cả ngày
            dong.append(("", nhan, so_don, doanh_thu, True))
        for stt, (gio, so_don, doanh_thu) in enumerate(self.chi_tiet_gio, start=1):
            dong.append((stt, f"{gio:02d}:00 - {gio:02d}:59", so_don, doanh_thu, False))
        return dong

    def du_lieu_bieu_do(self, kieu):
        """Nhãn + doanh thu để vẽ. Kỳ 'Ngày' vẽ theo giờ (kể cả giờ chưa có đơn)."""
        if kieu != "Ngày":
            return [x[0] for x in self.chi_tiet], [x[2] for x in self.chi_tiet]
        theo_gio = {g: d for g, _, d in self.chi_tiet_gio}
        gio_hien = sorted(set(range(GIO_MO_CUA, GIO_DONG_CUA + 1)) | set(theo_gio))
        return [f"{g:02d}h" for g in gio_hien], [theo_gio.get(g, 0) for g in gio_hien]

    def ve_bieu_do(self, kieu):
        if self.figure is None:
            return
        self.figure.clear()
        ax = self.figure.add_subplot(111)

        nhan, doanh_thu = self.du_lieu_bieu_do(kieu)

        ax.bar(nhan, doanh_thu, color="#8B5A2B")
        ax.set_title("Doanh thu theo giờ" if kieu == "Ngày"
                     else f"Doanh thu theo {NHAN_COT_DAU.get(kieu, 'thời gian').lower()}")
        ax.set_ylabel("Doanh thu (VNĐ)")
        ax.grid(axis="y", linestyle="--", alpha=0.3)
        ax.yaxis.set_major_formatter(
            FuncFormatter(lambda v, _: f"{v:,.0f}".replace(",", "."))
        )
        if max(doanh_thu, default=0) == 0:
            ax.set_ylim(0, 1)

        # Hạn chế nhãn chồng lên nhau khi có nhiều mốc
        if len(nhan) > 10:
            ax.tick_params(axis="x", labelrotation=45)

        self.figure.tight_layout()
        self.canvas.draw()

    # ------------------------------------------------------------------
    # XUẤT EXCEL
    def xuat_excel(self):
        """Xuất báo cáo đang hiển thị ra file Excel."""
        duong_dan, _ = QFileDialog.getSaveFileName(
            self, "Lưu báo cáo Excel", "BaoCaoDoanhThu.xlsx", "Excel Files (*.xlsx)"
        )
        if not duong_dan:
            return
        if not duong_dan.lower().endswith(".xlsx"):
            duong_dan += ".xlsx"

        kieu = self.cboDate.currentText()
        thoi_gian = self.lbDate.text().replace("Thời gian: ", "")
        tieu_de = ["STT", TIEU_DE_COT_DAU.get(kieu, "Ngày/Tháng"), "Số đơn hàng", "Doanh thu (VNĐ)"]
        dong = [[stt, nhan, so_don, doanh_thu]
                for stt, nhan, so_don, doanh_thu, _ in self.dong_bang]

        try:
            try:
                from openpyxl import Workbook
                from openpyxl.styles import Font, Alignment
                from openpyxl.utils import get_column_letter
            except ImportError:
                duong_csv = duong_dan[:-5] + ".csv"
                with open(duong_csv, "w", newline="", encoding="utf-8-sig") as f:
                    w = csv.writer(f)
                    w.writerow(["BÁO CÁO DOANH THU"])
                    w.writerow(["Thời gian", thoi_gian])
                    w.writerow(["Tổng số đơn hàng", self.tong_don])
                    w.writerow(["Tổng doanh thu (VNĐ)", self.tong_doanh_thu])
                    w.writerow([])
                    w.writerow(tieu_de)
                    w.writerows(dong)
                QMessageBox.information(
                    self, "Đã xuất file CSV",
                    f"Máy chưa cài openpyxl nên đã xuất file CSV (mở bằng Excel được):\n{duong_csv}\n\n"
                    "Muốn xuất .xlsx: mở CMD gõ  python -m pip install openpyxl"
                )
                return

            wb = Workbook()
            ws = wb.active
            ws.title = "Báo cáo doanh thu"

            # Căn giữa tiêu đề qua A1:D1 bằng "center across selection" (không gộp ô -> không có MergedCell)
            ws["A1"] = "BÁO CÁO DOANH THU"
            ws["A1"].font = Font(size=16, bold=True)
            for ten_o in ("A1", "B1", "C1", "D1"):
                ws[ten_o].alignment = Alignment(horizontal="centerContinuous")

            ws["A3"] = "Thời gian"
            ws["B3"] = thoi_gian
            ws["A4"] = "Tổng số đơn hàng"
            ws["B4"] = self.tong_don
            ws["A5"] = "Tổng doanh thu (VNĐ)"
            ws["B5"] = self.tong_doanh_thu
            ws["B5"].number_format = "#,##0"
            for o in ("A3", "A4", "A5"):
                ws[o].font = Font(bold=True)

            dong_tieu_de = 7
            for cot, ten in enumerate(tieu_de, start=1):
                o = ws.cell(row=dong_tieu_de, column=cot, value=ten)
                o.font = Font(bold=True)
                o.alignment = Alignment(horizontal="center")
            for i, d in enumerate(dong, start=1):
                for cot, gia_tri in enumerate(d, start=1):
                    o = ws.cell(row=dong_tieu_de + i, column=cot, value=gia_tri)
                    if cot == 4:
                        o.number_format = "#,##0"

            # Tự điều chỉnh độ rộng cột
            # (không dùng cot[0].column_letter vì ô A1:D1 đã gộp -> B1..D1 là MergedCell, không có thuộc tính đó)
            try:
                for so_cot in range(1, ws.max_column + 1):
                    chu = [len(str(ws.cell(row=r, column=so_cot).value))
                           for r in range(2, ws.max_row + 1)
                           if ws.cell(row=r, column=so_cot).value is not None]
                    ws.column_dimensions[get_column_letter(so_cot)].width = max(chu, default=8) + 4
            except Exception as e_rong:
                print("Bỏ qua chỉnh độ rộng cột:", e_rong)

            wb.save(duong_dan)
            QMessageBox.information(self, "Thành công", f"Đã xuất báo cáo Excel:\n{duong_dan}")
        except PermissionError:
            QMessageBox.critical(self, "Không lưu được",
                                 "File đang được mở bằng Excel. Hãy đóng file đó rồi thử lại.")
        except Exception as e:
            import traceback
            traceback.print_exc()
            vi_tri = traceback.format_exc().strip().splitlines()[-3:-1]
            QMessageBox.critical(
                self, "Lỗi xuất Excel",
                f"Không thể xuất file:\n{e}\n\nFile đang chạy: {os.path.abspath(__file__)}\n"
                + "\n".join(x.strip() for x in vi_tri)
            )

    # ------------------------------------------------------------------
    # CHUYỂN MÀN HÌNH (menu bên trái)
    def _chuyen(self, lop):
        self.cua_so = lop()
        self.cua_so.setGeometry(self.geometry())
        self.cua_so.show()
        self.close()

    def mo_lap_don_hang(self):
        from lap_don_hang import LapDonHangWindow
        self._chuyen(LapDonHangWindow)

    def mo_nhap_nguyen_lieu(self):
        from main import NhapNguyenLieuWindow
        self._chuyen(NhapNguyenLieuWindow)

    def mo_tim_kiem(self):
        from tim_kiem_don_hang import TimKiemDonHangWindow
        self._chuyen(TimKiemDonHangWindow)

    def mo_bao_cao(self):
        from bao_cao_ton_hang import BaoCaoTonHangWindow
        self._chuyen(BaoCaoTonHangWindow)


# Giữ tên cũ để code cũ gọi DoanhThu() vẫn chạy
DoanhThu = DoanhThuWindow


if __name__ == "__main__":
    app = QApplication.instance() or QApplication(sys.argv)
    window = DoanhThuWindow()
    window.show()
    sys.exit(app.exec_())