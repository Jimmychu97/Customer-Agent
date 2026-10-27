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

    # 运行 Qt 事件循环
    sys.exit(app.exec())

if __name__ == '__main__':
    main()
