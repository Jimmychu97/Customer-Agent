import sys
from PyQt6.QtCore import Qt, QTimer, QSettings
from PyQt6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel
from PyQt6.QtGui import QFont, QIcon, QPixmap
from qfluentwidgets import FluentWindow,qrouter, NavigationItemPosition
from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import SubtitleLabel, TeachingTip, TeachingTipTailPosition
from qfluentwidgets import Action
from utils.logger_loguru import get_logger
import time

class Widget(QFrame):

    def __init__(self, text: str, parent=None):
        super().__init__(parent=parent)
        # 创建标题标签
        self.label = SubtitleLabel(text, self)
        # 创建水平布局
        self.hBoxLayout = QHBoxLayout(self)
        # 设置标签文本居中对齐
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # 将标签添加到布局中,设置居中对齐和拉伸因子1
        self.hBoxLayout.addWidget(self.label, 1, Qt.AlignmentFlag.AlignCenter)
        # 必须给子界面设置全局唯一的对象名
        self.setObjectName(text.replace(' ', '-'))

class MainWindow(FluentWindow):
    def __init__(self):
        super().__init__()
        t = time.perf_counter()
        self.setWindowTitle('拼多多AI客服助手')
        self.setWindowIcon(QIcon("icon/icon.ico"))
        self.logger = get_logger("MainWindow")
        self.logger.info(f"  基础属性初始化: {time.perf_counter()-t:.2f}s")

        # 延迟加载的视图
        self.goods_view = None
        self.knowledge_view = None
        self.monitor_view = None
        self.keyword_manager_view = None
        self.user_manager_view = None
        self.log_view = None
        self.message_view = None
        self.voucher_view = None
        self.settingInterface = None

        t = time.perf_counter()
        # 立即初始化导航和窗口
        self.initWindow()
        self.logger.info(f"  initWindow: {time.perf_counter()-t:.2f}s")

        # 延迟加载各个视图，让窗口先显示
        QTimer.singleShot(200, self.lazy_load_views)

    def lazy_load_views(self):
        """延迟加载各个视图，提高启动速度"""
        t0 = time.perf_counter()
        # 局部按需导入，减少启动时的重依赖加载
        t = time.perf_counter()
        from ui.auto_reply_ui import AutoReplyUI
        self.logger.info(f"  import AutoReplyUI: {time.perf_counter()-t:.2f}s")
        t = time.perf_counter()
        from ui.keyword_ui import KeywordManagerWidget
        self.logger.info(f"  import KeywordManagerWidget: {time.perf_counter()-t:.2f}s")
        t = time.perf_counter()
        from ui.user_ui import UserManagerWidget
        self.logger.info(f"  import UserManagerWidget: {time.perf_counter()-t:.2f}s")
        t = time.perf_counter()
        from ui.log_ui import LogUI
        self.logger.info(f"  import LogUI: {time.perf_counter()-t:.2f}s")
        t = time.perf_counter()
        from ui.message_ui import MessageUI
        self.logger.info(f"  import MessageUI: {time.perf_counter()-t:.2f}s")
        t = time.perf_counter()
        from ui.setting_ui import SettingUI
        self.logger.info(f"  import SettingUI: {time.perf_counter()-t:.2f}s")
        t = time.perf_counter()
        from ui.Knowledge_ui import KnowledgeUI
        self.logger.info(f"  import KnowledgeUI: {time.perf_counter()-t:.2f}s")
        t = time.perf_counter()
        from ui.goods_ui import GoodsManagementWidget
        self.logger.info(f"  import GoodsManagementWidget: {time.perf_counter()-t:.2f}s")
        t = time.perf_counter()
        from ui.voucher_ui import VoucherManagementWidget
        self.logger.info(f"  import VoucherManagementWidget: {time.perf_counter()-t:.2f}s")
        t = time.perf_counter()
        self.monitor_view = AutoReplyUI(self)
        self.logger.info(f"  AutoReplyUI: {time.perf_counter()-t:.2f}s")
        t = time.perf_counter()
        self.keyword_manager_view = KeywordManagerWidget(self)
        self.logger.info(f"  KeywordManagerWidget: {time.perf_counter()-t:.2f}s")
        t = time.perf_counter()
        self.user_manager_view = UserManagerWidget(self)
        self.logger.info(f"  UserManagerWidget: {time.perf_counter()-t:.2f}s")
        t = time.perf_counter()
        self.log_view = LogUI(self)
        self.logger.info(f"  LogUI: {time.perf_counter()-t:.2f}s")
        t = time.perf_counter()
        self.message_view = MessageUI(self)
        self.logger.info(f"  MessageUI: {time.perf_counter()-t:.2f}s")
        t = time.perf_counter()
        self.knowledge_view = KnowledgeUI(self)
        self.logger.info(f"  KnowledgeUI: {time.perf_counter()-t:.2f}s")
        t = time.perf_counter()
        self.goods_view = GoodsManagementWidget(self)
        self.logger.info(f"  GoodsManagementWidget: {time.perf_counter()-t:.2f}s")
        t = time.perf_counter()
        self.voucher_view = VoucherManagementWidget(self)
        self.logger.info(f"  VoucherManagementWidget: {time.perf_counter()-t:.2f}s")
        t = time.perf_counter()
        self.settingInterface = SettingUI(self)
        self.logger.info(f"  SettingUI: {time.perf_counter()-t:.2f}s")

        # 初始化导航
        self.initNavigation()
        self.logger.info(f"延迟视图初始化耗时: {time.perf_counter() - t0:.2f}s")

    # 初始化导航栏
    def initNavigation(self):
        self.navigationInterface.setExpandWidth(200)
        self.navigationInterface.setCollapsible(True)
        self.navigationInterface.toggle()
        self.addSubInterface(self.monitor_view, FIF.CHAT, '自动回复')
        self.addSubInterface(self.keyword_manager_view, FIF.EDIT, '关键词管理')
        self.addSubInterface(self.user_manager_view, FIF.PEOPLE, '账号管理')
        self.addSubInterface(self.knowledge_view, FIF.DOCUMENT, '知识库')
        self.addSubInterface(self.goods_view, FIF.SHOPPING_CART, '商品管理')
        self.addSubInterface(self.voucher_view, FIF.CAFE, '核销管理')
        # 添加二维码按钮
        self.navigationInterface.addItem(
            routeKey='contact_us',
            icon=FIF.QRCODE,
            text='联系我们',
            onClick=self.showQRCode,
            selectable=False,
            position=NavigationItemPosition.BOTTOM
        )
        self.addSubInterface(self.log_view, FIF.HISTORY, '日志管理', NavigationItemPosition.BOTTOM)
        self.addSubInterface(self.message_view, FIF.CHAT, '消息管理', NavigationItemPosition.BOTTOM)
        self.addSubInterface(self.settingInterface, FIF.SETTING, '设置', NavigationItemPosition.BOTTOM)


    # 初始化窗口
    def initWindow(self):
        self.setMinimumWidth(1280)
        self.setMinimumHeight(720)
        self.resize(1400, 800)
        # 窗口显示后再恢复上次尺寸，避免 QPainter 错误
        QTimer.singleShot(0, self._restore_window_state)
        



    def showQRCode(self):
        """显示二维码TeachingTip"""
        try:
            tip = TeachingTip.create(
                target=self.navigationInterface,
                image="icon/Customer-Agent-qr.png",
                icon=FIF.PEOPLE,
                title="联系我们",
                content="扫码关注获取更多信息和支持",
                isClosable=True,
                duration=-1,
                tailPosition=TeachingTipTailPosition.LEFT,
                parent=self
            )
            
            # 显示TeachingTip
            tip.show()
            
        except Exception as e:
            self.logger.error(f"显示二维码失败: {e}")

    def _restore_window_state(self):
        """窗口显示后恢复上次的尺寸和位置（不恢复最大化状态）"""
        settings = QSettings("AgentCustomer", "MainWindow")
        w = settings.value("width", type=int)
        h = settings.value("height", type=int)
        x = settings.value("x", type=int)
        y = settings.value("y", type=int)
        if w and h:
            self.resize(w, h)
        if x is not None and y is not None:
            self.move(x, y)

    def closeEvent(self, a0):
        """ 重写窗口关闭事件，确保后台线程安全退出 """
        try:
            from ui.auto_reply_ui import auto_reply_manager
            auto_reply_manager.stop_all()
        except Exception:
            pass

        # 只在非最大化时保存尺寸，避免下次以最大化尺寸打开
        if not self.isMaximized():
            settings = QSettings("AgentCustomer", "MainWindow")
            settings.setValue("width", self.width())
            settings.setValue("height", self.height())
            settings.setValue("x", self.x())
            settings.setValue("y", self.y())
        super().closeEvent(a0)
