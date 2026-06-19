"""
核销管理界面

顶部：商户选择器（多租户）
上方：手动核销单（验券 + 执行核销）
下方：核销记录列表（手动点查询才加载）
"""
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
                             QGroupBox, QHeaderView, QAbstractItemView, QTableWidgetItem)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QDateTime
from PyQt6.QtGui import QColor
from qfluentwidgets import (PrimaryPushButton, PushButton, LineEdit, TableWidget,
                            InfoBar, InfoBarPosition, TitleLabel, BodyLabel,
                            ComboBox, DateTimeEdit)
from utils.logger_loguru import get_logger
from typing import Optional

logger = get_logger("VoucherUI")


# ──────────────────────────────────────────────
# 后台线程
# ──────────────────────────────────────────────
class PreviewThread(QThread):
    finished = pyqtSignal(dict)

    def __init__(self, shop_id, user_id, card_no):
        super().__init__()
        self.shop_id, self.user_id, self.card_no = shop_id, user_id, card_no

    def run(self):
        try:
            from Channel.pinduoduo.utils.API.mms_voucher_api import MmsVoucherAPI
            self.finished.emit(MmsVoucherAPI(self.shop_id, self.user_id).preview(self.card_no))
        except Exception as e:
            self.finished.emit({"success": False, "error_msg": str(e)})


class VerificationThread(QThread):
    finished = pyqtSignal(dict)

    def __init__(self, shop_id, user_id, card_no, order_sn, store_id):
        super().__init__()
        self.shop_id, self.user_id = shop_id, user_id
        self.card_no, self.order_sn, self.store_id = card_no, order_sn, store_id

    def run(self):
        try:
            from Channel.pinduoduo.utils.API.mms_voucher_api import MmsVoucherAPI
            self.finished.emit(MmsVoucherAPI(self.shop_id, self.user_id).verification(
                self.card_no, order_sn=self.order_sn, store_id=self.store_id
            ))
        except Exception as e:
            self.finished.emit({"success": False, "error_msg": str(e)})


class QueryThread(QThread):
    finished = pyqtSignal(dict)

    def __init__(self, shop_id, user_id, page_num, page_size, begin_ts, end_ts):
        super().__init__()
        self.shop_id, self.user_id = shop_id, user_id
        self.page_num, self.page_size = page_num, page_size
        self.begin_ts, self.end_ts = begin_ts, end_ts

    def run(self):
        try:
            from Channel.pinduoduo.utils.API.mms_voucher_api import MmsVoucherAPI
            self.finished.emit(MmsVoucherAPI(self.shop_id, self.user_id).query_records(
                self.page_num, self.page_size, self.begin_ts, self.end_ts
            ))
        except Exception as e:
            self.finished.emit({"success": False, "error_msg": str(e), "total": 0, "records": []})


# ──────────────────────────────────────────────
# 主界面
# ──────────────────────────────────────────────
class VoucherManagementWidget(QWidget):
    """核销管理主界面"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('voucher-management-interface')
        self._accounts: list = []          # [{shop_id, user_id, username, shop_name}]
        self._selected_shop_id: Optional[str] = None
        self._selected_user_id: Optional[str] = None
        self._preview_data: dict = {}
        self._current_page = 1
        self._total_records = 0
        self._page_size = 20
        self._init_ui()

    # ──────── UI 构建 ────────

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(36, 20, 36, 20)
        root.setSpacing(16)

        root.addWidget(TitleLabel("核销管理"))
        root.addWidget(self._build_account_bar())   # 商户选择
        root.addWidget(self._build_verify_group())  # 核销单
        root.addWidget(self._build_record_group(), 1)  # 记录列表

    def _build_account_bar(self) -> QWidget:
        """顶部商户选择栏"""
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        layout.addWidget(BodyLabel("当前商户:"))
        self.account_combo = ComboBox()
        self.account_combo.setMinimumWidth(260)
        self.account_combo.setPlaceholderText("请选择商户账号")
        self.account_combo.currentIndexChanged.connect(self._on_account_changed)
        layout.addWidget(self.account_combo)

        refresh_account_btn = PushButton("刷新账号")
        refresh_account_btn.clicked.connect(self._load_accounts)
        layout.addWidget(refresh_account_btn)

        layout.addStretch()
        return bar

    def _build_verify_group(self) -> QGroupBox:
        """核销操作区"""
        group = QGroupBox("手动核销")
        form = QFormLayout(group)
        form.setSpacing(12)

        card_row = QHBoxLayout()
        self.card_no_input = LineEdit()
        self.card_no_input.setPlaceholderText("输入拼多多卡券号")
        card_row.addWidget(self.card_no_input)
        self.preview_btn = PushButton("验券")
        self.preview_btn.clicked.connect(self._on_preview)
        card_row.addWidget(self.preview_btn)
        form.addRow("卡券号:", card_row)

        self.goods_name_label = BodyLabel("—")
        form.addRow("商品:", self.goods_name_label)

        self.order_sn_label = BodyLabel("—")
        form.addRow("订单号:", self.order_sn_label)

        self.order_status_label = BodyLabel("—")
        form.addRow("订单状态:", self.order_status_label)

        self.amount_label = BodyLabel("—")
        form.addRow("订单金额:", self.amount_label)

        self.verify_btn = PrimaryPushButton("确认核销")
        self.verify_btn.setEnabled(False)
        self.verify_btn.clicked.connect(self._on_verification)
        form.addRow("", self.verify_btn)

        return group

    def _build_record_group(self) -> QGroupBox:
        """核销记录区"""
        group = QGroupBox("核销记录")
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        # 筛选栏
        filter_row = QHBoxLayout()
        filter_row.addWidget(BodyLabel("开始时间:"))
        self.begin_dt = DateTimeEdit(self)
        self.begin_dt.setDateTime(QDateTime.currentDateTime().addDays(-30))
        filter_row.addWidget(self.begin_dt)

        filter_row.addWidget(BodyLabel("结束时间:"))
        self.end_dt = DateTimeEdit(self)
        self.end_dt.setDateTime(QDateTime.currentDateTime())
        filter_row.addWidget(self.end_dt)

        self.refresh_btn = PushButton("查询")
        self.refresh_btn.clicked.connect(lambda: self._on_query(1))
        filter_row.addWidget(self.refresh_btn)

        filter_row.addStretch()
        self.total_label = BodyLabel("共 0 条")
        filter_row.addWidget(self.total_label)
        layout.addLayout(filter_row)

        # 表格
        self.record_table = TableWidget()
        self.record_table.setColumnCount(7)
        self.record_table.setHorizontalHeaderLabels([
            "核销时间", "卡券号", "订单号", "商品名称", "规格", "金额(元)", "状态"
        ])
        self.record_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.record_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        header = self.record_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.record_table)

        # 分页
        page_row = QHBoxLayout()
        self.prev_btn = PushButton("上一页")
        self.prev_btn.setEnabled(False)
        self.prev_btn.clicked.connect(self._prev_page)
        self.next_btn = PushButton("下一页")
        self.next_btn.setEnabled(False)
        self.next_btn.clicked.connect(self._next_page)
        self.page_label = BodyLabel("—")
        page_row.addStretch()
        page_row.addWidget(self.prev_btn)
        page_row.addWidget(self.page_label)
        page_row.addWidget(self.next_btn)
        layout.addLayout(page_row)

        return group

    # ──────── 账号加载 ────────

    def showEvent(self, event):
        """首次显示时加载账号列表，不自动查询"""
        super().showEvent(event)
        if not self._accounts:
            self._load_accounts()

    def _load_accounts(self):
        """从数据库加载所有商户账号"""
        try:
            from database.db_manager import db_manager
            all_accounts = db_manager.get_all_accounts_with_details()
            self._accounts = all_accounts

            self.account_combo.blockSignals(True)
            self.account_combo.clear()
            for acc in all_accounts:
                label = f"{acc.get('shop_name', '')} - {acc.get('username', '')}"
                self.account_combo.addItem(label)
            self.account_combo.blockSignals(False)

            # 默认选第一个运行中的账号
            self._select_running_account()

        except Exception as e:
            logger.error(f"加载账号失败: {e}")

    def _select_running_account(self):
        """优先选中正在运行的账号"""
        try:
            from ui.auto_reply import auto_reply_manager
            running = auto_reply_manager.running_accounts
            if not running:
                if self._accounts:
                    self.account_combo.setCurrentIndex(0)
                    self._apply_selected_account(0)
                return

            first_thread = next(iter(running.values()))
            running_shop_id = str(first_thread.account_data.get("shop_id", ""))
            running_user_id = str(first_thread.account_data.get("user_id", ""))

            for i, acc in enumerate(self._accounts):
                if str(acc.get("shop_id")) == running_shop_id and \
                   str(acc.get("user_id")) == running_user_id:
                    self.account_combo.setCurrentIndex(i)
                    self._apply_selected_account(i)
                    return

            # 没找到匹配的就选第一个
            if self._accounts:
                self.account_combo.setCurrentIndex(0)
                self._apply_selected_account(0)

        except Exception as e:
            logger.warning(f"自动选择运行账号失败: {e}")
            if self._accounts:
                self.account_combo.setCurrentIndex(0)
                self._apply_selected_account(0)

    def _on_account_changed(self, index: int):
        self._apply_selected_account(index)
        # 切换商户时清空验券结果和记录表格
        self._clear_preview()
        self.record_table.setRowCount(0)
        self.total_label.setText("共 0 条")
        self.page_label.setText("—")
        self.prev_btn.setEnabled(False)
        self.next_btn.setEnabled(False)

    def _apply_selected_account(self, index: int):
        if 0 <= index < len(self._accounts):
            acc = self._accounts[index]
            self._selected_shop_id = str(acc.get("shop_id", ""))
            self._selected_user_id = str(acc.get("user_id", ""))

    # ──────── 验券 ────────

    def _on_preview(self):
        if not self._check_account():
            return
        card_no = self.card_no_input.text().strip()
        if not card_no:
            self._show_error("错误", "请输入卡券号")
            return

        self.preview_btn.setEnabled(False)
        self.verify_btn.setEnabled(False)
        self._clear_preview()

        self._preview_thread = PreviewThread(
            self._selected_shop_id, self._selected_user_id, card_no
        )
        self._preview_thread.finished.connect(self._on_preview_done)
        self._preview_thread.start()

    def _on_preview_done(self, result: dict):
        self.preview_btn.setEnabled(True)
        if not result["success"]:
            self._show_error("验券失败", result.get("error_msg", "未知错误"))
            return

        self._preview_data = result
        amount = result.get("order_amount", 0) or 0
        self.goods_name_label.setText(
            f"{result.get('goods_name', '')}  {result.get('spec', '')}"
        )
        self.order_sn_label.setText(result.get("order_sn", "—"))
        self.order_status_label.setText(result.get("order_display_status", "—"))
        self.amount_label.setText(f"{amount / 100:.2f} 元" if amount else "—")
        self.verify_btn.setEnabled(True)

    def _clear_preview(self):
        self._preview_data = {}
        self.goods_name_label.setText("—")
        self.order_sn_label.setText("—")
        self.order_status_label.setText("—")
        self.amount_label.setText("—")
        self.verify_btn.setEnabled(False)

    # ──────── 核销 ────────

    def _on_verification(self):
        if not self._check_account() or not self._preview_data:
            return

        card_no = self.card_no_input.text().strip()
        order_sn = self._preview_data.get("order_sn", "")
        store_id = self._preview_data.get("store_id", 0) or 0

        self.verify_btn.setEnabled(False)
        self._verify_thread = VerificationThread(
            self._selected_shop_id, self._selected_user_id,
            card_no, order_sn, store_id
        )
        self._verify_thread.finished.connect(self._on_verification_done)
        self._verify_thread.start()

    def _on_verification_done(self, result: dict):
        if result["success"]:
            self._show_success("核销成功", f"卡券 {self.card_no_input.text().strip()} 已核销")
            self.card_no_input.clear()
            self._clear_preview()
        else:
            self.verify_btn.setEnabled(True)
            self._show_error("核销失败", result.get("error_msg", "未知错误"))

    # ──────── 查询记录 ────────

    def _on_query(self, page: int = 1):
        if not self._check_account():
            return

        self._current_page = page
        self.refresh_btn.setEnabled(False)
        self.record_table.setRowCount(0)

        begin_ts = self.begin_dt.dateTime().toSecsSinceEpoch()
        end_ts = self.end_dt.dateTime().toSecsSinceEpoch()

        self._query_thread = QueryThread(
            self._selected_shop_id, self._selected_user_id,
            page, self._page_size, begin_ts, end_ts
        )
        self._query_thread.finished.connect(self._on_query_done)
        self._query_thread.start()

    def _on_query_done(self, result: dict):
        self.refresh_btn.setEnabled(True)
        if not result["success"]:
            self._show_error("查询失败", result.get("error_msg", ""))
            return

        self._total_records = result.get("total", 0)
        self._populate_table(result.get("records", []))

        total_pages = max(1, (self._total_records + self._page_size - 1) // self._page_size)
        self.page_label.setText(f"第 {self._current_page} / {total_pages} 页")
        self.total_label.setText(f"共 {self._total_records} 条")
        self.prev_btn.setEnabled(self._current_page > 1)
        self.next_btn.setEnabled(self._current_page < total_pages)

    def _populate_table(self, records: list):
        self.record_table.setRowCount(len(records))
        for row, r in enumerate(records):
            amount = r.get("order_amount", 0) or 0
            status = r.get("status", 0)
            err = r.get("err_msg") or ""
            status_text = "成功" if status == 0 else f"失败: {err}"

            values = [
                r.get("verification_time", ""),
                r.get("card_no", ""),
                r.get("order_sn", ""),
                r.get("goods_name", ""),
                r.get("spec", ""),
                f"{amount / 100:.2f}",
                status_text,
            ]
            for col, val in enumerate(values):
                item = QTableWidgetItem(str(val))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if status == 1:
                    item.setForeground(QColor("#e74c3c"))
                self.record_table.setItem(row, col, item)

    def _prev_page(self):
        if self._current_page > 1:
            self._on_query(self._current_page - 1)

    def _next_page(self):
        total_pages = max(1, (self._total_records + self._page_size - 1) // self._page_size)
        if self._current_page < total_pages:
            self._on_query(self._current_page + 1)

    # ──────── 工具 ────────

    def set_account_info(self, shop_id: str, user_id: str):
        """外部注入账号（兼容旧调用）"""
        self._selected_shop_id = shop_id
        self._selected_user_id = user_id

    def _check_account(self) -> bool:
        if self._selected_shop_id and self._selected_user_id:
            return True
        self._show_error("未选择商户", "请先在顶部选择商户账号")
        return False

    def _show_success(self, title: str, msg: str):
        InfoBar.success(title=title, content=msg, isClosable=True,
                        position=InfoBarPosition.TOP, duration=3000, parent=self)

    def _show_error(self, title: str, msg: str):
        InfoBar.error(title=title, content=msg, isClosable=True,
                      position=InfoBarPosition.TOP, duration=5000, parent=self)
