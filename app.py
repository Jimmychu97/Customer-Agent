"""
应用程序入口点

全局单例初始化顺序（重要）：
1. config           → 必须在最前面，其他模块都依赖配置
2. DI 容器           → 通过 configure_standard_services() 统一注册所有服务
3. db_manager       → 通过 DI 容器获取
4. logger           → 日志系统，依赖 config
5. queue_manager    → 通过 DI 容器获取
6. message_consumer_manager → 通过 DI 容器获取
7. status_manager   → 通过 DI 容器获取（ConnectionStatusManager 单例）
8. cache_manager    → 通过 DI 容器获取

关键原则：
- config 必须最先初始化
- DI 容器通过 configure_standard_services() 统一管理所有服务的生命周期
- UI 模块在 main() 中通过延迟加载初始化
- 业务模块间通过延迟导入（lazy import）避免循环依赖
- PDDChannel 每个 AutoReplyThread 独立实例，共享 ConnectionStatusManager
"""
import sys
import ctypes
import os
from pathlib import Path
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QApplication

# ============================================================================
# 全局单例预初始化（确保正确的初始化顺序）
# ============================================================================
# 1. 配置必须最先加载
from config import config as _app_config

# 2. 数据库管理器（通过 DI 代理，懒加载）
from database import db_manager as _app_db_manager

# 3. 日志系统（依赖配置）
from utils.logger_loguru import get_logger as _get_logger

# 4. 配置标准服务到 DI 容器（必须在其他业务模块导入前执行）
from core.di_container import configure_standard_services
configure_standard_services(_app_config)

# ============================================================================

from ui.main_ui import MainWindow
import time

# 设置 Playwright 浏览器路径（支持打包后的 exe）
def get_project_root():
    """获取项目根目录（支持 PyInstaller 打包后的 exe）"""
    import sys
    if getattr(sys, 'frozen', False):
        # PyInstaller 打包后的 exe
        return Path(sys._MEIPASS).parent
    return Path(__file__).resolve().parent

def setup_playwright_browsers_path():
    """设置 Playwright 浏览器安装路径"""
    project_root = get_project_root()
    browsers_path = project_root / ".browsers"
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers_path)
    return browsers_path

def _auto_start_all_accounts() -> None:
    """启动后自动拉起所有「在线」账号的自动回复

    不依赖自动回复页面实例（那是懒加载的），直接按数据库里的账号状态启动，
    这样每次打开程序都不用手动点「启动自动回复」。
    """
    logger = _get_logger("App")
    try:
        from database.db_manager import db_manager
        from ui.auto_reply.manager import auto_reply_manager

        accounts = db_manager.get_all_accounts_with_details() or []
        eligible = [
            acc for acc in accounts
            if acc.get("status") == 1 and not auto_reply_manager.is_running(acc)
        ]
        if not eligible:
            logger.info("启动自动回复：没有需要启动的账号（未登录或已在运行）")
            return

        started = []
        for acc in eligible:
            if auto_reply_manager.start_auto_reply(acc):
                started.append(acc.get("username"))
        logger.info(f"启动自动回复：已自动开启 {len(started)}/{len(eligible)} 个账号 {started}")
    except Exception as e:
        logger.warning(f"启动自动回复失败: {e}")


def main():
    """应用程序主函数"""
    # 设置 Playwright 浏览器路径
    browsers_path = setup_playwright_browsers_path()

    # 创建应用
    app = QApplication(sys.argv)
    app.setApplicationName("Agent-Customer")

    # 创建主窗口
    logger = _get_logger("App")
    logger.info("应用程序启动...")

    # Qt 槽函数里的异常只打到 stderr，GUI 没有控制台就等于「点了没反应」。
    # 统一兜底：写日志（带堆栈）+ 弹窗，避免任何一次点击静默失败。
    def _excepthook(exc_type, exc_value, exc_tb):
        # Ctrl+C 停止程序不算异常，别在日志里刷一屏 Traceback
        if issubclass(exc_type, KeyboardInterrupt):
            return
        logger.opt(exception=(exc_type, exc_value, exc_tb)).error("未捕获异常（界面操作）")
        try:
            from PyQt6.QtWidgets import QMessageBox

            QMessageBox.critical(
                None, "程序异常",
                f"{exc_type.__name__}: {exc_value}\n\n详情见 logs/app.log")
        except Exception:
            pass

    sys.excepthook = _excepthook

    # 部署自检：避免"新版调用 + 旧版被调"（例如 DatabaseManager 缺少 save_message）
    # 造成消息记录静默丢失（历史上表现为 WARNING: 'DatabaseManager' object has no attribute 'save_message'）
    deploy_problems = []
    try:
        from Message.core.persistence import check_deployment
        deploy_problems = check_deployment()
        if deploy_problems:
            logger.error("部署不完整，消息记录不可用：" + "；".join(deploy_problems))
        else:
            logger.info("部署自检通过：消息记录功能可用")
    except Exception as e:
        logger.warning(f"部署自检执行失败: {e}")

    t0 = time.perf_counter()
    t_import = time.perf_counter()
    from ui.main_ui import MainWindow  # noqa: F401
    logger.info(f"  MainWindow 模块导入耗时: {time.perf_counter() - t_import:.2f}s")
    t_window = time.perf_counter()
    window = MainWindow()
    window.show()
    logger.info(f"  MainWindow 实例化耗时: {time.perf_counter() - t_window:.2f}s")
    logger.info(f"窗口创建与显示总耗时: {time.perf_counter() - t0:.2f}s")

    # 部署不完整时在界面顶部给出明确提示（不阻断启动）
    if deploy_problems:
        try:
            from qfluentwidgets import InfoBar, InfoBarPosition
            InfoBar.error(
                title="部署不完整",
                content="消息记录不可用：" + deploy_problems[0],
                orient=Qt.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=15000,
                parent=window,
            )
        except Exception:
            pass

    # 将窗口设为应用级别的变量，防止被垃圾回收
    app.main_window = window

    # 启动后是否自动拉起「在线」账号的自动回复：由 config 的 auto_reply.autostart 控制，默认关闭。
    # 关闭后照样可以在「自动回复」页手动启动；这样避免一开机就自动连平台，
    # 也避免本机与服务器同时在线把同一个号互相顶掉。
    if _app_config.get("auto_reply.autostart", False):
        logger.info("auto_reply.autostart=true，3 秒后自动拉起在线账号的自动回复")
        QTimer.singleShot(3000, lambda: _auto_start_all_accounts())
    else:
        logger.info("auto_reply.autostart=false（默认关闭），不自动启动自动回复，请在界面手动开启")

    # 运行 Qt 事件循环
    sys.exit(app.exec())

if __name__ == '__main__':
    main()
