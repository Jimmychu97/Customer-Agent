"""
消息管理界面
从日志流中过滤 AI 回复、核销、工具调用等业务消息，以可读形式展示
"""

from datetime import datetime
from collections import deque
from PyQt6.QtCore import Qt, QAbstractTableModel, QModelIndex, pyqtSignal, QTimer
from PyQt6.QtWidgets import (QFrame, QVBoxLayout, QHBoxLayout, QWidget,
                             QTableView, QHeaderView, QMessageBox)
from PyQt6.QtGui import QFont, QColor, QPainter
from PyQt6.QtWidgets import QStyledItemDelegate, QStyleOptionViewItem
from qfluentwidgets import (CardWidget, SubtitleLabel, CaptionLabel, BodyLabel,
                            StrongBodyLabel, ComboBox, LineEdit, ScrollArea,
                            FluentIcon as FIF, InfoBar, InfoBarPosition,
                            PushButton, CheckBox, SwitchButton)
from utils.logger_loguru import get_logger, UILogHandler


# ============================================================
# 消息类型定义
# ============================================================

# 需要拦截的模块关键字
_WATCH_MODULES = (
    "Message.handlers",
    "Agent.CustomerAgent",
)

# 消息类型分类规则（按关键字匹配消息内容）
def _classify(message: str) -> str:
    if "AI回复发送成功" in message:
        return "AI回复"
    if "AI回复发送失败" in message or "AI回复生成失败" in message:
        return "AI失败"
    if "[核销]" in message:
        return "核销"
    if "GSX" in message:
        return "GSX"
    if "LLM 决定调用工具" in message or "获取商品列表" in message:
        return "工具调用"
    if "CustomerAgent 初始化" in message:
        return "系统"
    return "其他"


_TYPE_COLORS = {
    "AI回复":  QColor(0, 128, 0),
    "AI失败":  QColor(220, 20, 60),
    "核销":    QColor(30, 100, 220),
    "GSX":     QColor(150, 50, 200),
    "工具调用": QColor(200, 120, 0),
    "系统":    QColor(100, 100, 100),
    "其他":    QColor(80, 80, 80),
}


# ============================================================
# 数据模型
# ============================================================

class MessageItem:
    """单条消息记录"""
    def __init__(self, level: str, message: str, record):
        self.level = level
        self.message = message
        self.msg_type = _classify(message)
        self.timestamp = ""
        self._parse_time(record)

    def _parse_time(self, record):
        try:
            if isinstance(record, dict):
                t = record.get("time", datetime.now())
                if hasattr(t, "strftime"):
                    self.timestamp = t.strftime("%Y-%m-%d %H:%M:%S")
                    return
        except Exception:
            pass
        self.timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class MessageModel(QAbstractTableModel):
    """消息表格数据模型"""
    _HEADERS = ["时间", "类型", "消息"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: deque = deque(maxlen=5000)
        self._filtered: list = []
        self._filter_func = None

    def rowCount(self, parent=QModelIndex()):
        return len(self._filtered)

    def columnCount(self, parent=QModelIndex()):
        return 3

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self._HEADERS[section]
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self._filtered):
            return None
        item = self._filtered[index.row()]
        col = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0: return item.timestamp
            if col == 1: return item.msg_type
            if col == 2: return item.message
        elif role == Qt.ItemDataRole.ForegroundRole:
            return _TYPE_COLORS.get(item.msg_type, QColor(51, 51, 51))
        elif role == Qt.ItemDataRole.ToolTipRole:
            return item.message
        return None

    def add_item(self, item: MessageItem):
        self._items.append(item)
        if self._filter_func is None or self._filter_func(item):
            self._filtered.append(item)
            if len(self._filtered) > 2000:
                self._filtered = self._filtered[-2000:]
        self.layoutChanged.emit()

    def apply_filter(self, filter_func=None):
        self._filter_func = filter_func
        if filter_func is None:
            self._filtered = list(self._items)
        else:
            self._filtered = [i for i in self._items if filter_func(i)]
        self.layoutChanged.emit()

    def clear(self):
        self._items.clear()
        self._filtered.clear()
        self.layoutChanged.emit()


# ============================================================
# UI 组件
# ============================================================

class MessageTableView(QTableView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._model = MessageModel()
        self.setModel(self._model)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.setSortingEnabled(False)
        header = self.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        self.setColumnWidth(0, 160)
        self.setColumnWidth(1, 80)
        self.setStyleSheet("""
            QTableView {
                background-color: #ffffff;
                alternate-background-color: #f8f9fa;
                gridline-color: #e9ecef;
            }
            QTableView::item { padding: 5px; border: none; }
            QHeaderView::section {
                background-color: #e9ecef;
                padding: 8px;
                border: none;
                border-right: 1px solid #dee2e6;
                border-bottom: 1px solid #dee2e6;
                font-weight: bold;
            }
        """)


class MessageFilterCard(CardWidget):
    filter_changed = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)

        layout.addWidget(StrongBodyLabel("消息过滤"))

        # 类型过滤
        type_row = QHBoxLayout()
        type_row.addWidget(CaptionLabel("类型:"))
        self.type_combo = ComboBox()
        self.type_combo.addItems(["全部", "AI回复", "AI失败", "核销", "GSX", "工具调用", "系统", "其他"])
        self.type_combo.setFixedWidth(110)
        type_row.addWidget(self.type_combo)
        type_row.addStretch()
        layout.addLayout(type_row)

        # 搜索
        search_row = QHBoxLayout()
        search_row.addWidget(CaptionLabel("搜索:"))
        self.search_edit = LineEdit()
        self.search_edit.setPlaceholderText("关键词搜索...")
        search_row.addWidget(self.search_edit)
        layout.addLayout(search_row)

        # 自动滚动
        self.auto_scroll = CheckBox("自动滚动")
        self.auto_scroll.setChecked(True)
        layout.addWidget(self.auto_scroll)

        self.type_combo.currentTextChanged.connect(self._emit)
        self.search_edit.textChanged.connect(self._emit)
        self.auto_scroll.stateChanged.connect(self._emit)

    def _emit(self):
        self.filter_changed.emit({
            "type": self.type_combo.currentText(),
            "search": self.search_edit.text().strip(),
            "auto_scroll": self.auto_scroll.isChecked(),
        })


class MessageControlCard(CardWidget):
    clear_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)

        layout.addWidget(StrongBodyLabel("操作"))

        self.clear_btn = PushButton("清空")
        self.clear_btn.setIcon(FIF.DELETE)
        self.clear_btn.clicked.connect(self.clear_clicked.emit)
        layout.addWidget(self.clear_btn)

        # 统计标签
        self.stats_label = CaptionLabel("共 0 条消息")
        self.stats_label.setStyleSheet("color: #666;")
        layout.addWidget(self.stats_label)

    def update_stats(self, count: int):
        self.stats_label.setText(f"共 {count} 条消息")


# ============================================================
# 日志信号发射器（复用 log_ui 的模式）
# ============================================================

class MsgSignalEmitter(QWidget):
    log_received = pyqtSignal(str, str, object)


# ============================================================
# 主界面
# ============================================================

class MessageUI(QFrame):
    """消息管理界面"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("message-ui")
        self.logger = get_logger("MessageUI")
        self._auto_scroll = True
        self._setup_ui()
        self._setup_handler()
        self._connect_signals()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        title = SubtitleLabel("消息管理")
        title.setFont(QFont("Microsoft YaHei", 18, QFont.Weight.Bold))
        layout.addWidget(title)

        content = QWidget()
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(16)

        # 左侧控制面板
        left = QWidget()
        left.setFixedWidth(240)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(12)

        self.filter_card = MessageFilterCard()
        self.control_card = MessageControlCard()
        left_layout.addWidget(self.filter_card)
        left_layout.addWidget(self.control_card)
        left_layout.addStretch()

        # 右侧表格
        self.table_view = MessageTableView()

        content_layout.addWidget(left)
        content_layout.addWidget(self.table_view, 1)
        layout.addWidget(content, 1)

    def _setup_handler(self):
        self._emitter = MsgSignalEmitter(self)
        self._ui_handler = UILogHandler()
        self._ui_handler.log_received.connect(
            self._emitter.log_received
        )
        self._emitter.log_received.connect(
            self._on_log, Qt.ConnectionType.QueuedConnection
        )
        self._ui_handler.install()

    def _connect_signals(self):
        self.filter_card.filter_changed.connect(self._apply_filter)
        self.control_card.clear_clicked.connect(self._clear)

    def _on_log(self, level: str, message: str, record):
        """拦截日志，只保留业务相关消息"""
        # 只看 INFO/WARNING/ERROR
        if level not in ("INFO", "WARNING", "ERROR"):
            return
        # 只看指定模块
        module = ""
        if isinstance(record, dict):
            module = record.get("name", "")
        if not any(module.startswith(m) for m in _WATCH_MODULES):
            return

        item = MessageItem(level, message, record)
        self.table_view._model.add_item(item)
        self.control_card.update_stats(len(self.table_view._model._items))

        if self._auto_scroll:
            model = self.table_view._model
            if model.rowCount() > 0:
                last = model.index(model.rowCount() - 1, 0)
                self.table_view.scrollTo(last, QTableView.ScrollHint.PositionAtBottom)

    def _apply_filter(self, f: dict):
        self._auto_scroll = f.get("auto_scroll", True)
        type_filter = f.get("type", "全部")
        search = f.get("search", "")

        def func(item: MessageItem) -> bool:
            if type_filter != "全部" and item.msg_type != type_filter:
                return False
            if search and search.lower() not in item.message.lower():
                return False
            return True

        self.table_view._model.apply_filter(None if (type_filter == "全部" and not search) else func)

    def _clear(self):
        reply = QMessageBox.question(
            self, "确认清空", "确定要清空所有消息记录吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.table_view._model.clear()
            self.control_card.update_stats(0)

    def closeEvent(self, event):
        self._ui_handler.uninstall()
        super().closeEvent(event)
