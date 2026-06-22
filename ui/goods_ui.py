"""
商品管理界面

左侧：从 product_knowledge 表读取商品列表
右侧：显示选中商品关联的 goods_commit SKU 规格
同步：针对选中商品调用 /glide/v2/mms/query/commit/detail 写入 goods_commit 表
"""
from typing import Optional, List, Dict, Any
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
                             QHeaderView, QAbstractItemView, QTableWidgetItem,
                             QSplitter)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor
from qfluentwidgets import (PrimaryPushButton, PushButton, TableWidget,
                            InfoBar, InfoBarPosition, TitleLabel, BodyLabel,
                            ComboBox)
from utils.logger_loguru import get_logger

logger = get_logger("GoodsUI")


# ──────────────────────────────────────────────
# 后台线程
# ──────────────────────────────────────────────

class SyncAllGoodsThread(QThread):
    """同步所有商品到 goods_commit 表"""
    progress = pyqtSignal(int, int, str)   # current, total, goods_name
    finished = pyqtSignal(bool, str)       # success, message

    def __init__(self, shop_id: str, user_id: str, db_shop_id: int, goods_ids: list):
        super().__init__()
        self.shop_id = shop_id
        self.user_id = user_id
        self.db_shop_id = db_shop_id
        self.goods_ids = goods_ids  # list of (goods_id_str, goods_name)

    def run(self):
        try:
            from Channel.pinduoduo.utils.API.goods_commit_api import GoodsCommitAPI
            from database.db_manager import db_manager
            from database.models import GoodsCommit

            api = GoodsCommitAPI(shop_id=self.shop_id, user_id=self.user_id)
            total = len(self.goods_ids)
            synced = 0

            for i, (goods_id, goods_name) in enumerate(self.goods_ids):
                self.progress.emit(i + 1, total, goods_name)
                detail = api.get_goods_detail(goods_id)
                if not detail["success"]:
                    continue

                r = detail["result"]
                skus = r.get("skus", [])

                with db_manager.get_session() as session:
                    # 删除该商品旧SKU数据
                    session.query(GoodsCommit).filter_by(
                        shop_id=str(self.db_shop_id), goods_id=str(goods_id)
                    ).delete()

                    for sku in skus:
                        specs = sku.get("spec", [])
                        spec = specs[0] if specs else {}
                        session.add(GoodsCommit(
                            shop_id=str(self.db_shop_id),
                            goods_id=str(goods_id),
                            sku_id=str(sku.get("sku_id", "")),
                            parent_id=spec.get("parent_id"),
                            parent_name=spec.get("parent_name", ""),
                            spec_id=str(spec.get("spec_id", "")) if spec.get("spec_id") else None,
                            spec_name=spec.get("spec_name", ""),
                        ))
                    session.commit()
                synced += 1

            self.finished.emit(True, f"同步完成，共 {synced}/{total} 个商品")
        except Exception as e:
            logger.error(f"同步失败: {e}")
            self.finished.emit(False, str(e))


# ──────────────────────────────────────────────
# 主界面
# ──────────────────────────────────────────────

class GoodsManagementWidget(QWidget):
    """商品管理主界面"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('goods-management-interface')
        self._accounts: list = []
        self._selected_shop_id: Optional[str] = None
        self._selected_user_id: Optional[str] = None
        self._selected_db_shop_id: Optional[int] = None
        self._goods_data: List[Dict[str, Any]] = []   # product_knowledge 行
        self._selected_goods_id: Optional[str] = None
        self._init_ui()

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(36, 20, 36, 20)
        root.setSpacing(16)

        root.addWidget(TitleLabel("商品管理"))
        root.addWidget(self._build_account_bar())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_goods_table())
        splitter.addWidget(self._build_sku_table())
        splitter.setSizes([700, 400])
        root.addWidget(splitter, 1)

        root.addWidget(self._build_bottom_bar())

    def _build_account_bar(self) -> QWidget:
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

        refresh_btn = PushButton("刷新账号")
        refresh_btn.clicked.connect(self._load_accounts)
        layout.addWidget(refresh_btn)

        layout.addStretch()
        self.status_label = BodyLabel("")
        layout.addWidget(self.status_label)
        return bar

    def _build_goods_table(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(BodyLabel("商品列表（来自知识库）"))

        self.goods_table = TableWidget()
        self.goods_table.setColumnCount(4)
        self.goods_table.setHorizontalHeaderLabels(["商品ID", "商品名称", "价格(元)", "已同步SKU"])
        self.goods_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.goods_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.goods_table.itemSelectionChanged.connect(self._on_goods_selected)

        header = self.goods_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.goods_table)
        return container

    def _build_sku_table(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(BodyLabel("SKU 规格（来自同步数据）"))

        self.sku_table = TableWidget()
        self.sku_table.setColumnCount(5)
        self.sku_table.setHorizontalHeaderLabels(["规格名称", "规格类别", "SKU ID", "规格ID", "ServerId"])
        self.sku_table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)  # ServerId 列可双击编辑
        self.sku_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.sku_table.itemChanged.connect(self._on_sku_item_changed)

        header = self.sku_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in [1, 2, 3, 4]:
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.sku_table)
        return container

    def _build_bottom_bar(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)

        self.sync_btn = PrimaryPushButton("同步所有商品SKU")
        self.sync_btn.setEnabled(True)
        self.sync_btn.clicked.connect(self._on_sync)
        layout.addWidget(self.sync_btn)
        layout.addStretch()
        return bar

    # ──────── 账号加载 ────────

    def showEvent(self, event):
        super().showEvent(event)
        if not self._accounts:
            self._load_accounts()

    def _load_accounts(self):
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

            if self._accounts:
                self.account_combo.setCurrentIndex(0)
                self._apply_selected_account(0)
                self._load_goods()
        except Exception as e:
            logger.error(f"加载账号失败: {e}")

    def _on_account_changed(self, index: int):
        self._apply_selected_account(index)
        self.goods_table.setRowCount(0)
        self.sku_table.setRowCount(0)
        self.sync_btn.setEnabled(False)
        if self._selected_db_shop_id:
            self._load_goods()

    def _apply_selected_account(self, index: int):
        if 0 <= index < len(self._accounts):
            acc = self._accounts[index]
            self._selected_shop_id = str(acc.get("shop_id", ""))
            self._selected_user_id = str(acc.get("user_id", ""))
            self._selected_db_shop_id = self._resolve_db_shop_id()

    def _resolve_db_shop_id(self) -> Optional[int]:
        try:
            from database.db_manager import db_manager
            from database.models import Shop
            with db_manager.get_session() as session:
                shop = session.query(Shop).filter_by(shop_id=self._selected_shop_id).first()
                return shop.id if shop else None
        except Exception:
            return None

    # ──────── 商品列表（来自 product_knowledge）────────

    def _load_goods(self):
        if not self._selected_db_shop_id:
            return
        try:
            from database.db_manager import db_manager
            from database.models import ProductKnowledge, GoodsCommit

            with db_manager.get_session() as session:
                products = session.query(ProductKnowledge).filter_by(
                    shop_id=self._selected_db_shop_id
                ).order_by(ProductKnowledge.goods_name).all()

                # 已同步的 goods_id 集合
                synced_ids = {
                    gc.goods_id
                    for gc in session.query(GoodsCommit.goods_id).filter_by(
                        shop_id=str(self._selected_db_shop_id)
                    ).all()
                }

                self._goods_data = [
                    {
                        "goods_id": str(p.goods_id),
                        "goods_name": p.goods_name,
                        "price": p.price or "",
                        "synced": str(p.goods_id) in synced_ids,
                    }
                    for p in products
                ]

            self._populate_goods_table()
            self.status_label.setText(f"共 {len(self._goods_data)} 个商品")
        except Exception as e:
            logger.error(f"加载商品列表失败: {e}")

    def _populate_goods_table(self):
        self.goods_table.setRowCount(len(self._goods_data))
        for row, g in enumerate(self._goods_data):
            synced = g["synced"]
            values = [
                g["goods_id"],
                g["goods_name"],
                g["price"],
                "✓" if synced else "—",
            ]
            for col, val in enumerate(values):
                item = QTableWidgetItem(str(val))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if col == 3 and synced:
                    item.setForeground(QColor("#27ae60"))
                self.goods_table.setItem(row, col, item)

    # ──────── SKU 展示（来自 goods_commit）────────

    def _on_goods_selected(self):
        row = self.goods_table.currentRow()
        if row < 0 or row >= len(self._goods_data):
            return
        self._selected_goods_id = self._goods_data[row]["goods_id"]
        self._load_sku(self._selected_goods_id)

    def _load_sku(self, goods_id: str):
        self.sku_table.setRowCount(0)
        try:
            from database.db_manager import db_manager
            from database.models import GoodsCommit

            with db_manager.get_session() as session:
                rows = session.query(GoodsCommit).filter_by(
                    shop_id=str(self._selected_db_shop_id),
                    goods_id=goods_id,
                ).all()

                self.sku_table.setRowCount(len(rows))
                self._sku_rows = []  # 存储行数据用于保存
                for i, row in enumerate(rows):
                    values = [
                        row.spec_name or "",
                        row.parent_name or "",
                        row.sku_id or "",
                        str(row.spec_id or ""),
                        str(row.server_id) if row.server_id else "",
                    ]
                    self._sku_rows.append({"id": row.id, "sku_id": row.sku_id})
                    for col, val in enumerate(values):
                        item = QTableWidgetItem(str(val))
                        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                        # 前 4 列只读，ServerId 列可编辑
                        if col < 4:
                            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                        self.sku_table.setItem(i, col, item)
        except Exception as e:
            logger.error(f"加载SKU失败: {e}")

    # ──────── 同步选中商品 ────────

    def _on_sync(self):
        if not self._selected_db_shop_id or not self._selected_shop_id:
            self._show_error("未选择商户", "请先选择商户账号")
            return

        goods_ids = [(g["goods_id"], g["goods_name"]) for g in self._goods_data]
        if not goods_ids:
            self._show_error("没有商品", "知识库中暂无商品")
            return

        self.sync_btn.setEnabled(False)
        self.status_label.setText("同步中...")

        self._sync_thread = SyncAllGoodsThread(
            self._selected_shop_id,
            self._selected_user_id,
            self._selected_db_shop_id,
            goods_ids,
        )
        self._sync_thread.progress.connect(
            lambda cur, total, name: self.status_label.setText(f"同步中 {cur}/{total}: {name}")
        )
        self._sync_thread.finished.connect(self._on_sync_done)
        self._sync_thread.start()

    def _on_sku_item_changed(self, item: QTableWidgetItem):
        """ServerId 列编辑后保存到数据库"""
        if item.column() != 4:  # 只处理 ServerId 列
            return
        row = item.row()
        if not hasattr(self, '_sku_rows') or row >= len(self._sku_rows):
            return
        row_data = self._sku_rows[row]
        server_id = item.text().strip()
        try:
            from database.db_manager import db_manager
            from database.models import GoodsCommit
            with db_manager.get_session() as session:
                gc = session.query(GoodsCommit).filter_by(id=row_data["id"]).first()
                if gc:
                    gc.server_id = int(server_id) if server_id.isdigit() else None
                    session.commit()
                    logger.info(f"ServerId 已保存: sku_id={row_data['sku_id']} server_id={server_id!r}")
        except Exception as e:
            logger.error(f"保存 ServerId 失败: {e}")

    def _on_sync_done(self, success: bool, message: str):
        self.sync_btn.setEnabled(True)
        if success:
            self._show_success("同步成功", message)
            self._load_goods()
            if self._selected_goods_id:
                self._load_sku(self._selected_goods_id)
        else:
            self._show_error("同步失败", message)
        self.status_label.setText(f"共 {len(self._goods_data)} 个商品")

    # ──────── 工具 ────────

    def _show_success(self, title: str, msg: str):
        InfoBar.success(title=title, content=msg, isClosable=True,
                        position=InfoBarPosition.TOP, duration=3000, parent=self)

    def _show_error(self, title: str, msg: str):
        InfoBar.error(title=title, content=msg, isClosable=True,
                      position=InfoBarPosition.TOP, duration=5000, parent=self)
