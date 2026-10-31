"""
商品管理界面

左侧：从 product_knowledge 表读取商品列表
右侧：显示选中商品关联的 goods_commit SKU 规格
同步：针对选中商品调用 /glide/v2/mms/query/commit/detail 写入 goods_commit 表
"""
from typing import Optional, List, Dict, Any
from functools import partial
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

# 服务模式四态：索引即库里的取值（3 = 未配置，与 core.service_mode.SERVICE_MODE_UNSET 一致）
_SERVICE_MODE_ITEMS = ["卡券核销", "仅发货", "二选一兼容", "未配置"]


def _service_mode_index(value) -> int:
    """库里的服务模式 → 下拉框索引；缺失或非法一律落到「未配置」"""
    return value if value in (0, 1, 2, 3) else 3


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
                    # 按 sku_id 索引已有记录，保留用户填写的字段
                    existing = {
                        gc.sku_id: gc
                        for gc in session.query(GoodsCommit).filter_by(
                            shop_id=str(self.db_shop_id), goods_id=str(goods_id)
                        ).all()
                    }

                    incoming_sku_ids = set()
                    for sku in skus:
                        specs = sku.get("spec", [])
                        spec = specs[0] if specs else {}
                        sku_id = str(sku.get("sku_id", ""))
                        incoming_sku_ids.add(sku_id)
                        # 多维度规格名拼接，如「黑色 128G」
                        spec_name = " ".join(s.get("spec_name", "") for s in specs if s.get("spec_name"))
                        parent_name = " / ".join(s.get("parent_name", "") for s in specs if s.get("parent_name"))

                        if sku_id in existing:
                            # 已存在：只更新结构字段，保留 server_id / zhenxin_server_id / device_type
                            gc = existing[sku_id]
                            gc.parent_id = spec.get("parent_id")
                            gc.parent_name = parent_name
                            gc.spec_id = str(spec.get("spec_id", "")) if spec.get("spec_id") else None
                            gc.spec_name = spec_name
                            session.flush()  # 强制写入变更
                        else:
                            # 新增 SKU
                            session.add(GoodsCommit(
                                shop_id=str(self.db_shop_id),
                                goods_id=str(goods_id),
                                sku_id=sku_id,
                                parent_id=spec.get("parent_id"),
                                parent_name=parent_name,
                                spec_id=str(spec.get("spec_id", "")) if spec.get("spec_id") else None,
                                spec_name=spec_name,
                            ))

                    # 删除拼多多已下架的 SKU（本地有但远端没有的）
                    for sku_id, gc in existing.items():
                        if sku_id not in incoming_sku_ids:
                            session.delete(gc)

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
        self.goods_table.setColumnCount(5)
        self.goods_table.setHorizontalHeaderLabels(
            ["商品ID", "商品名称", "价格(元)", "已同步SKU", "服务模式"])
        self.goods_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.goods_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.goods_table.itemSelectionChanged.connect(self._on_goods_selected)

        header = self.goods_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.goods_table)
        return container

    def _build_sku_table(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(BodyLabel("SKU 规格（来自同步数据）"))

        self.sku_table = TableWidget()
        self.sku_table.setColumnCount(7)
        self.sku_table.setHorizontalHeaderLabels(["规格名称", "规格类别", "SKU ID", "规格ID", "ServerId", "真心ServerId", "设备类型"])
        self.sku_table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
        self.sku_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.sku_table.itemChanged.connect(self._on_sku_item_changed)

        header = self.sku_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setMinimumSectionSize(80)
        for col in [1, 2, 3, 4, 5, 6]:
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.sku_table.horizontalHeader().setStretchLastSection(False)
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
                        "service_mode": _service_mode_index(getattr(p, "service_mode", None)),
                    }
                    for p in products
                ]

            self._populate_goods_table()
            self.status_label.setText(f"共 {len(self._goods_data)} 个商品")
            self.sync_btn.setEnabled(True)  # 加载完成后启用按鈕
        except Exception as e:
            logger.error(f"加载商品列表失败: {e}")

    def _populate_goods_table(self):
        """填充左表；第 4 列（服务模式）为下拉控件，不参与 QTableWidgetItem 循环"""
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
            self.goods_table.setCellWidget(
                row, 4, self._build_service_mode_combo(g["goods_id"], g.get("service_mode", 0)))

    def _build_service_mode_combo(self, goods_id: str, service_mode: int) -> ComboBox:
        """构造服务模式下拉框

        Args:
            goods_id: 商品ID
            service_mode: 当前服务模式（0 卡券核销 / 1 仅发货 / 2 二选一 / 3 未配置）

        Returns:
            已设置当前值并绑定信号的 ComboBox
        """
        combo = ComboBox()
        combo.addItems(_SERVICE_MODE_ITEMS)
        # 先屏蔽信号设置当前值，再 connect，避免填充时触发写库
        combo.blockSignals(True)
        combo.setCurrentIndex(service_mode if 0 <= service_mode <= 3 else 3)
        combo.blockSignals(False)
        combo.currentIndexChanged.connect(
            partial(self._on_service_mode_changed, goods_id))
        return combo

    def _on_service_mode_changed(self, goods_id: str, index: int):
        """服务模式下拉框变更后写库

        Args:
            goods_id: 商品ID
            index: 下拉框索引，与 0/1/2/3 四态一一对应
        """
        label = _SERVICE_MODE_ITEMS[index] if 0 <= index <= 3 else "未知"
        try:
            from database.db_manager import db_manager
            from database.models import ProductKnowledge
            with db_manager.get_session() as session:
                product = session.query(ProductKnowledge).filter(
                    ProductKnowledge.shop_id == self._selected_db_shop_id,
                    ProductKnowledge.goods_id == int(goods_id),
                ).first()
                if not product:
                    logger.error(f"服务模式写库失败：商品不存在 goods_id={goods_id}")
                    self._show_error("设置失败", f"知识库中未找到商品 {goods_id}")
                    return
                product.service_mode = index
                session.commit()
            for item in self._goods_data:
                if item["goods_id"] == goods_id:
                    item["service_mode"] = index
                    break
            logger.info(f"服务模式已更新: goods_id={goods_id} service_mode={index}")
            self._show_success("设置成功", f"商品 {goods_id} 服务模式已设为：{label}")
        except Exception as e:
            logger.error(f"保存服务模式失败 goods_id={goods_id}: {e}")
            self._show_error("设置失败", str(e))

    # ──────── SKU 展示（来自 goods_commit）────────

    def _on_goods_selected(self):
        rows = self.goods_table.selectionModel().selectedRows()
        if not rows:
            return
        row = rows[0].row()
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
                self.sku_table.blockSignals(True)  # 填充时屏蔽信号，防止 itemChanged 干扰
                for i, row in enumerate(rows):
                    values = [
                        row.spec_name or "",
                        row.parent_name or "",
                        row.sku_id or "",
                        str(row.spec_id or ""),
                        str(row.server_id) if row.server_id else "",
                        str(row.zhenxin_server_id) if row.zhenxin_server_id else "",
                        row.device_type or "",
                    ]
                    self._sku_rows.append({"id": row.id, "sku_id": row.sku_id})
                    for col, val in enumerate(values):
                        item = QTableWidgetItem(str(val))
                        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                        # 前 4 列只读，后 3 列可编辑
                        if col < 4:
                            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                        self.sku_table.setItem(i, col, item)
                self.sku_table.blockSignals(False)  # 填充完成后恢复信号
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
        """ServerId / 真心ServerId / 设备类型 列编辑后保存到数据库"""
        col = item.column()
        if col not in (4, 5, 6):
            return
        row = item.row()
        if not hasattr(self, '_sku_rows') or row >= len(self._sku_rows):
            return
        row_data = self._sku_rows[row]
        value = item.text().strip()
        try:
            from database.db_manager import db_manager
            from database.models import GoodsCommit
            with db_manager.get_session() as session:
                gc = session.query(GoodsCommit).filter_by(id=row_data["id"]).first()
                if gc:
                    if col == 4:
                        gc.server_id = int(value) if value.isdigit() else None
                        logger.info(f"ServerId 已保存: sku_id={row_data['sku_id']} server_id={value!r}")
                    elif col == 5:
                        gc.zhenxin_server_id = int(value) if value.isdigit() else None
                        logger.info(f"真心ServerId 已保存: sku_id={row_data['sku_id']} zhenxin_server_id={value!r}")
                    elif col == 6:
                        gc.device_type = value if value in ("apple", "android") else None
                        logger.info(f"设备类型 已保存: sku_id={row_data['sku_id']} device_type={value!r}")
                    session.commit()
        except Exception as e:
            logger.error(f"保存 SKU 字段失败: {e}")

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
