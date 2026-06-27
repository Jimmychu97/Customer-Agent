# 连接管理模块
import asyncio
import websockets
from websockets import exceptions as ws_exceptions
from typing import Optional, Any
from utils.logger_loguru import get_logger


class ConnectionMixin:
    """连接管理 Mixin"""

    base_url: str = "wss://m-ws.pinduoduo.com/"

    async def _connect_with_retry(self, shop_id: str, user_id: str, username: str, on_success: callable, on_failure: callable):
        """带重连机制的WebSocket连接，断线后无限重连直到收到停止信号"""
        logger = get_logger("PDDChannel")
        attempt = 0

        while True:
            # 重连前先检查停止信号
            if self._stop_event and self._stop_event.is_set():
                logger.info(f"收到停止信号，取消重连: {shop_id}-{username}")
                self.status_manager.update_status(shop_id, user_id, username, ConnectionState.DISCONNECTED)
                return

            # 重连时重建 stop_event（不能复用旧的已 set event）
            if attempt > 0:
                self._stop_event = asyncio.Event()
                self.status_manager.update_status(shop_id, user_id, username, ConnectionState.RECONNECTING)
                logger.info(f"尝试重连 (第{attempt}次): {shop_id}-{username}")

            # 重连时使用静默回调，避免 UI 重复弹出连接成功通知
            current_on_success = on_success if attempt == 0 else lambda: logger.info(f"重连成功: {shop_id}-{username}")

            try:
                disconnected_by_stop = await self._connect_single_attempt(shop_id, user_id, username, current_on_success, on_failure)

                if disconnected_by_stop or (self._stop_event and self._stop_event.is_set()):
                    logger.info(f"连接已主动停止，不再重连: {shop_id}-{username}")
                    return

                logger.warning(f"连接意外断开（非主动停止），准备重连: {shop_id}-{username}")

            except asyncio.CancelledError:
                logger.info(f"重连任务被取消: {shop_id}-{username}")
                return
            except Exception as e:
                if self._stop_event and self._stop_event.is_set():
                    logger.info(f"连接被停止信号中断: {shop_id}-{username}")
                    self.status_manager.update_status(shop_id, user_id, username, ConnectionState.DISCONNECTED)
                    return
                logger.warning(f"连接异常断开，准备重连: {shop_id}-{username}, 错误: {e}")

            attempt += 1
            delay = min(
                self.reconnect_config.initial_delay * (self.reconnect_config.backoff_factor ** min(attempt - 1, 8)),
                self.reconnect_config.max_delay
            )
            logger.info(f"将在 {delay:.1f}s 后重连: {shop_id}-{username}")

            try:
                for _ in range(int(delay * 10)):
                    if self._stop_event and self._stop_event.is_set():
                        logger.info(f"重连等待被停止信号中断: {shop_id}-{username}")
                        self.status_manager.update_status(shop_id, user_id, username, ConnectionState.DISCONNECTED)
                        return
                    await asyncio.sleep(0.1)
            except (asyncio.CancelledError, RuntimeError):
                logger.info(f"重连延迟被中断: {shop_id}-{username}")
                self.status_manager.update_status(shop_id, user_id, username, ConnectionState.DISCONNECTED)
                return

    async def _connect_single_attempt(self, shop_id: str, user_id: str, username: str, on_success: callable, on_failure: callable) -> bool:
        """单次WebSocket连接尝试，返回 True 表示主动停止， False 表示意外断线"""
        return await self.init(shop_id, user_id, username, on_success, on_failure)

    def _is_ws_closed(self, ws: Any) -> bool:
        """检查WebSocket是否已关闭"""
        try:
            closed = getattr(ws, "closed", None)
            if isinstance(closed, bool):
                return closed
            return False
        except Exception:
            return False

    async def _safe_close_websocket(self, ws: Any):
        """安全关闭WebSocket"""
        try:
            close_fn = getattr(ws, "close", None)
            if close_fn:
                result = close_fn()
                if asyncio.iscoroutine(result):
                    await result
        except Exception as e:
            self.logger.debug(f"关闭WebSocket失败: {e}")


# 延迟导入避免循环依赖
from core.connection_status import ConnectionState
__all__ = ['ConnectionMixin']
