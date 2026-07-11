"""
卡券核销测试界面

独立运行，不依赖生产环境 WebSocket/账号。
支持：
- 上传图片 → OCR识别 → 提取卡券/序列号
- 直接输入文字测试提取逻辑
- 调用 preview 验券
- 调用 verification 核销
- 调用 GSX / 真心验机查询

运行方式：
    uv run python scripts/test_voucher_ui.py
"""
import sys
import os
import asyncio
import threading

# 把项目根目录加入 path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTextEdit, QFileDialog,
    QGroupBox, QSplitter, QComboBox, QScrollArea
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QPixmap, QDragEnterEvent, QDropEvent


# ── 异步任务线程 ──────────────────────────────────────────────
class AsyncWorker(QThread):
    """在独立线程里跑 asyncio 协程，完成后发信号"""
    finished = pyqtSignal(str)   # 成功结果文本
    error    = pyqtSignal(str)   # 错误文本

    def __init__(self, coro):
        super().__init__()
        self.coro = coro

    def run(self):
        try:
            result = asyncio.run(self.coro)
            self.finished.emit(str(result))
        except Exception as e:
            self.error.emit(str(e))


# ── 图片拖放区 ────────────────────────────────────────────────
class ImageDropLabel(QLabel):
    image_dropped = pyqtSignal(str)   # 本地文件路径

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(300, 200)
        self.setStyleSheet(
            "border: 2px dashed #aaa; border-radius: 8px; color: #888; font-size: 13px;"
        )
        self.setText("拖放图片到此处\n或点击「选择图片」按钮")

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e: QDropEvent):
        for url in e.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.webp')):
                self._show(path)
                self.image_dropped.emit(path)
                break

    def _show(self, path: str):
        pix = QPixmap(path).scaled(
            self.width(), self.height(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )
        self.setPixmap(pix)


# ── 主窗口 ────────────────────────────────────────────────────
class TestWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("卡券核销测试工具")
        self.resize(1000, 750)
        self._workers = []          # 防止 QThread 被 GC
        self._image_path = ""
        self._setup_ui()
        self._load_accounts()

    # ── UI 构建 ───────────────────────────────────────────────
    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter)

        # 左侧：输入区
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setSpacing(8)

        # 账号选择
        acc_box = QGroupBox("账号（用于 preview / verification）")
        av = QVBoxLayout(acc_box)
        self.combo_account = QComboBox()
        av.addWidget(self.combo_account)
        lv.addWidget(acc_box)

        # 图片区
        img_box = QGroupBox("图片 OCR 识别")
        iv = QVBoxLayout(img_box)
        self.img_label = ImageDropLabel()
        self.img_label.image_dropped.connect(self._on_image_dropped)
        iv.addWidget(self.img_label)
        btn_row = QHBoxLayout()
        btn_select = QPushButton("选择图片")
        btn_select.clicked.connect(self._pick_image)
        btn_ocr = QPushButton("▶ OCR 识别")
        btn_ocr.clicked.connect(self._run_ocr)
        btn_row.addWidget(btn_select)
        btn_row.addWidget(btn_ocr)
        iv.addLayout(btn_row)
        lv.addWidget(img_box)

        # 文字输入区
        txt_box = QGroupBox("文字提取测试（直接输入卡券号 / 序列号）")
        tv = QVBoxLayout(txt_box)
        self.txt_input = QTextEdit()
        self.txt_input.setPlaceholderText("粘贴用户发来的文字，或 OCR 识别结果会自动填入...")
        self.txt_input.setMaximumHeight(100)
        tv.addWidget(self.txt_input)
        btn_extract = QPushButton("▶ 提取卡券/序列号")
        btn_extract.clicked.connect(self._run_extract)
        tv.addWidget(btn_extract)
        lv.addWidget(txt_box)

        # 手动输入核销参数
        param_box = QGroupBox("核销参数（可手动填写覆盖提取结果）")
        pv = QVBoxLayout(param_box)
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("卡券号:"))
        self.inp_coupon = QLineEdit()
        self.inp_coupon.setPlaceholderText("10-14位纯数字")
        row1.addWidget(self.inp_coupon)
        pv.addLayout(row1)
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("序列号/IMEI:"))
        self.inp_sn = QLineEdit()
        self.inp_sn.setPlaceholderText("苹果序列号 或 15位IMEI")
        row2.addWidget(self.inp_sn)
        pv.addLayout(row2)
        lv.addWidget(param_box)

        # 操作按钮
        op_box = QGroupBox("操作")
        ov = QVBoxLayout(op_box)
        btn_preview = QPushButton("① Preview 验券")
        btn_preview.clicked.connect(self._run_preview)
        btn_verify  = QPushButton("② Verification 核销")
        btn_verify.clicked.connect(self._run_verification)
        btn_gsx     = QPushButton("③ GSX 验机查询")
        btn_gsx.clicked.connect(self._run_gsx)
        btn_zhenxin = QPushButton("③ 真心验机查询（苹果）")
        btn_zhenxin.clicked.connect(self._run_zhenxin)
        btn_full    = QPushButton("🚀 一键完整流程（OCR→提取→Preview→核销→验机）")
        btn_full.setStyleSheet("font-weight: bold; background: #1a73e8; color: white; padding: 6px;")
        btn_full.clicked.connect(self._run_full)
        for b in [btn_preview, btn_verify, btn_gsx, btn_zhenxin, btn_full]:
            ov.addWidget(b)
        lv.addWidget(op_box)
        lv.addStretch()

        # 右侧：日志区
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        log_label = QLabel("日志输出")
        log_label.setStyleSheet("font-weight: bold;")
        rv.addWidget(log_label)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setStyleSheet("font-family: Consolas, monospace; font-size: 12px;")
        rv.addWidget(self.log)
        btn_clear = QPushButton("清空日志")
        btn_clear.clicked.connect(self.log.clear)
        rv.addWidget(btn_clear)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([420, 580])

    # ── 账号加载 ──────────────────────────────────────────────
    def _load_accounts(self):
        try:
            from database import db_manager
            accounts = db_manager.get_all_accounts()
            for acc in accounts:
                label = f"{acc.get('shop_name','?')} / {acc.get('username','?')} (shop={acc.get('shop_id')} user={acc.get('user_id')})"
                self.combo_account.addItem(label, userData=acc)
            if not accounts:
                self.combo_account.addItem("（无账号，请先在主程序登录）")
                self._log("⚠️ 未找到账号，preview/verification 功能不可用")
        except Exception as e:
            self.combo_account.addItem(f"加载失败: {e}")
            self._log(f"❌ 加载账号失败: {e}")

    def _current_account(self):
        return self.combo_account.currentData()

    # ── 日志 ──────────────────────────────────────────────────
    def _log(self, text: str):
        self.log.append(text)
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    # ── 图片选择 ──────────────────────────────────────────────
    def _pick_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择图片", "", "图片文件 (*.png *.jpg *.jpeg *.bmp *.webp)"
        )
        if path:
            self.img_label._show(path)
            self._on_image_dropped(path)

    def _on_image_dropped(self, path: str):
        self._image_path = path
        self._log(f"📷 已选择图片: {path}")

    # ── OCR 识别 ──────────────────────────────────────────────
    def _run_ocr(self):
        if not self._image_path:
            self._log("⚠️ 请先选择图片")
            return
        self._log("🔍 正在 OCR 识别...")
        self._run_async(self._ocr_coro(), self._on_ocr_done)

    async def _ocr_coro(self):
        # 把本地文件转为 base64 data URL
        import base64, mimetypes
        mime = mimetypes.guess_type(self._image_path)[0] or "image/jpeg"
        with open(self._image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        data_url = f"data:{mime};base64,{b64}"

        from Message.handlers.image_handler import _get_vl_client
        client = await _get_vl_client()
        if not client:
            return "❌ VL 模型未配置（请在设置页填写 vl_llm 配置）"

        prompt = "OCR模式：原样输出图中所有文字，禁止分析、禁止加工、禁止总结。"
        response = await client.chat(
            messages=[{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": data_url}},
                {"type": "text", "text": prompt},
            ]}],
            tool_choice="none",
            max_tokens=500,
        )
        return response.content or ""

    def _on_ocr_done(self, text: str):
        self._log(f"📄 OCR 结果:\n{text}\n")
        self.txt_input.setPlainText(text)
        # 自动触发提取
        self._run_extract()

    # ── 文字提取 ──────────────────────────────────────────────
    def _run_extract(self):
        text = self.txt_input.toPlainText().strip()
        if not text:
            self._log("⚠️ 请先输入文字或识别图片")
            return

        from Message.handlers.voucher_handler import (
            _extract_coupon, _extract_imei, _extract_serial
        )
        coupon = _extract_coupon(text)
        imei   = _extract_imei(text)
        sn     = _extract_serial(text)
        device_sn = imei or sn

        self._log("─" * 40)
        self._log(f"🔎 提取结果:")
        self._log(f"   卡券号  : {coupon or '未识别'}")
        self._log(f"   IMEI    : {imei or '未识别'}")
        self._log(f"   序列号  : {sn or '未识别'}")
        self._log("─" * 40)

        if coupon:
            self.inp_coupon.setText(coupon)
        if device_sn:
            self.inp_sn.setText(device_sn)

    # ── Preview 验券 ──────────────────────────────────────────
    def _run_preview(self):
        coupon = self.inp_coupon.text().strip()
        if not coupon:
            self._log("⚠️ 请先填写卡券号")
            return
        acc = self._current_account()
        if not acc:
            self._log("⚠️ 请先选择账号")
            return
        self._log(f"🔍 Preview 验券: {coupon} ...")
        self._run_async(self._preview_coro(coupon, acc), self._on_preview_done)

    async def _preview_coro(self, coupon: str, acc: dict):
        from Channel.pinduoduo.utils.API.mms_voucher_api import MmsVoucherAPI
        api = MmsVoucherAPI(shop_id=acc["shop_id"], user_id=acc["user_id"])
        return api.preview(coupon)

    def _on_preview_done(self, result: str):
        import ast
        try:
            d = ast.literal_eval(result)
        except Exception:
            d = result
        self._log(f"📋 Preview 结果:\n{self._fmt(d)}\n")

    # ── Verification 核销 ─────────────────────────────────────
    def _run_verification(self):
        coupon = self.inp_coupon.text().strip()
        if not coupon:
            self._log("⚠️ 请先填写卡券号")
            return
        acc = self._current_account()
        if not acc:
            self._log("⚠️ 请先选择账号")
            return
        self._log(f"⚡ 执行核销: {coupon} ...")
        self._run_async(self._verification_coro(coupon, acc), lambda r: self._log(f"✅ 核销结果:\n{r}\n"))

    async def _verification_coro(self, coupon: str, acc: dict):
        from Channel.pinduoduo.utils.API.mms_voucher_api import MmsVoucherAPI
        api = MmsVoucherAPI(shop_id=acc["shop_id"], user_id=acc["user_id"])
        # 先 preview 拿 order_sn / store_id
        prev = api.preview(coupon)
        if not prev.get("success"):
            return f"Preview 失败: {prev.get('error_msg')}"
        result = api.verification(
            coupon,
            order_sn=prev.get("order_sn", ""),
            store_id=prev.get("store_id", 0),
        )
        return result

    # ── GSX 验机 ──────────────────────────────────────────────
    def _run_gsx(self):
        sn = self.inp_sn.text().strip()
        coupon = self.inp_coupon.text().strip()
        if not sn:
            self._log("⚠️ 请先填写序列号/IMEI")
            return
        self._log(f"🔬 GSX 验机查询: sn={sn} ...")
        self._run_async(self._gsx_coro(sn, coupon), lambda r: self._log(f"📊 GSX 结果:\n{r}\n"))

    async def _gsx_coro(self, sn: str, coupon: str):
        # 尝试从 GoodsCommit 查 server_id
        server_id = await asyncio.to_thread(self._get_server_id, coupon, "android")
        if not server_id:
            return "❌ 未找到 GSX server_id，请在商品管理页填写 ServerId"
        from Channel.pinduoduo.utils.API.gsx_api import query_gsx
        return query_gsx(sn, server_id)

    # ── 真心验机 ──────────────────────────────────────────────
    def _run_zhenxin(self):
        sn = self.inp_sn.text().strip()
        coupon = self.inp_coupon.text().strip()
        if not sn:
            self._log("⚠️ 请先填写序列号")
            return
        self._log(f"🍎 真心验机查询: sn={sn} ...")
        self._run_async(self._zhenxin_coro(sn, coupon), lambda r: self._log(f"📊 真心结果:\n{r}\n"))

    async def _zhenxin_coro(self, sn: str, coupon: str):
        sid = await asyncio.to_thread(self._get_server_id, coupon, "apple")
        if not sid:
            return "❌ 未找到真心 server_id，请在商品管理页填写「真心ServerId」"
        from Channel.pinduoduo.utils.API.zhenxin_gsx_api import query_zhenxin
        return query_zhenxin(sn, sid)

    # ── 一键完整流程 ──────────────────────────────────────────
    def _run_full(self):
        if self._image_path:
            self._log("🚀 一键流程：OCR → 提取 → Preview → 核销 → 验机")
            self._run_async(self._full_coro_image(), self._on_full_done)
        else:
            text = self.txt_input.toPlainText().strip()
            if not text:
                self._log("⚠️ 请先选择图片或输入文字")
                return
            self._log("🚀 一键流程（文字）：提取 → Preview → 核销 → 验机")
            self._run_async(self._full_coro_text(text), self._on_full_done)

    async def _full_coro_image(self):
        ocr_text = await self._ocr_coro()
        return await self._full_coro_text(ocr_text)

    async def _full_coro_text(self, text: str):
        from Message.handlers.voucher_handler import (
            _extract_coupon, _extract_imei, _extract_serial
        )
        coupon = _extract_coupon(text)
        imei   = _extract_imei(text)
        sn     = _extract_serial(text) or imei

        lines = [f"【提取】卡券={coupon or '无'} SN={sn or '无'}"]

        acc = self._current_account()
        if not acc:
            lines.append("❌ 无账号，跳过 preview/核销")
            return "\n".join(lines)

        if not coupon:
            lines.append("❌ 未提取到卡券号，流程终止")
            return "\n".join(lines)

        # Preview
        from Channel.pinduoduo.utils.API.mms_voucher_api import MmsVoucherAPI
        api = MmsVoucherAPI(shop_id=acc["shop_id"], user_id=acc["user_id"])
        prev = api.preview(coupon)
        lines.append(f"【Preview】{self._fmt(prev)}")

        if not prev.get("success"):
            return "\n".join(lines)

        goods_id = str(prev.get("goods_id", ""))
        spec     = prev.get("spec", "")

        # 验机（核销前）
        if sn:
            cfg = await asyncio.to_thread(self._get_query_config, goods_id, spec)
            device_type = cfg.get("device_type")
            if device_type == "apple" and cfg.get("zhenxin_server_id"):
                from Channel.pinduoduo.utils.API.zhenxin_gsx_api import query_zhenxin
                zx = await asyncio.to_thread(query_zhenxin, sn, cfg["zhenxin_server_id"])
                lines.append(f"【真心验机】{zx}")
            elif cfg.get("server_id"):
                from Channel.pinduoduo.utils.API.gsx_api import query_gsx
                gsx = await asyncio.to_thread(query_gsx, sn, cfg["server_id"])
                lines.append(f"【GSX验机】{gsx}")
            else:
                lines.append("【验机】未配置 server_id，跳过")

        # 核销
        vr = api.verification(coupon, order_sn=prev.get("order_sn",""), store_id=prev.get("store_id",0))
        lines.append(f"【核销】{self._fmt(vr)}")

        return "\n".join(lines)

    def _on_full_done(self, result: str):
        self._log(f"🏁 完整流程结果:\n{result}\n")

    # ── 辅助：查 GoodsCommit ─────────────────────────────────
    def _get_server_id(self, coupon: str, device_type: str) -> str:
        """通过卡券号 preview 拿 goods_id，再查 GoodsCommit"""
        try:
            acc = self._current_account()
            if not acc:
                return ""
            from Channel.pinduoduo.utils.API.mms_voucher_api import MmsVoucherAPI
            api = MmsVoucherAPI(shop_id=acc["shop_id"], user_id=acc["user_id"])
            prev = api.preview(coupon) if coupon else {}
            goods_id = str(prev.get("goods_id", ""))
            spec     = prev.get("spec", "")
            cfg = self._get_query_config(goods_id, spec)
            if device_type == "apple":
                return cfg.get("zhenxin_server_id") or ""
            return cfg.get("server_id") or ""
        except Exception:
            return ""

    def _get_query_config(self, goods_id: str, spec: str) -> dict:
        try:
            from database import db_manager
            from database.models import GoodsCommit
            with db_manager.session_scope() as session:
                gc = session.query(GoodsCommit).filter_by(
                    goods_id=goods_id, spec_name=spec
                ).first()
                if not gc:
                    return {"server_id": None, "zhenxin_server_id": None, "device_type": None}
                return {
                    "server_id":         str(gc.server_id) if gc.server_id else None,
                    "zhenxin_server_id": str(gc.zhenxin_server_id) if gc.zhenxin_server_id else None,
                    "device_type":       gc.device_type,
                }
        except Exception:
            return {"server_id": None, "zhenxin_server_id": None, "device_type": None}

    # ── 辅助：格式化 dict ─────────────────────────────────────
    @staticmethod
    def _fmt(d) -> str:
        if isinstance(d, dict):
            return "\n".join(f"  {k}: {v}" for k, v in d.items())
        return str(d)

    # ── 辅助：启动异步任务 ────────────────────────────────────
    def _run_async(self, coro, on_done):
        worker = AsyncWorker(coro)
        worker.finished.connect(on_done)
        worker.error.connect(lambda e: self._log(f"❌ 错误: {e}"))
        worker.finished.connect(lambda: self._workers.remove(worker))
        worker.error.connect(lambda _: self._workers.remove(worker))
        self._workers.append(worker)
        worker.start()


# ── 入口 ──────────────────────────────────────────────────────
if __name__ == "__main__":
    # 初始化 config + DI 容器（复用 app.py 的初始化顺序）
    try:
        from config import config
        from core.di_container import container, configure_standard_services
        configure_standard_services(config)
    except Exception as e:
        print(f"初始化警告: {e}")

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = TestWindow()
    win.show()
    sys.exit(app.exec())
