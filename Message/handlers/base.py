"""
处理器基类和通用工具

send_to_user() 是所有 handler 发消息的统一入口。
每个用户维护一个独立的异步发送队列，保证同一用户消息串行发出，不同用户并发。
"""
import asyncio
from typing import Dict, Any, Optional
from utils.logger_loguru import get_logger
from bridge.context import Context
from ..core.handlers import MessageHandler

# 每个用户一个发送队列：{from_uid: asyncio.Queue}
_send_queues: Dict[str, asyncio.Queue] = {}
_send_queues_lock = asyncio.Lock()
# 每个用户一个 worker task：{from_uid: asyncio.Task}
_send_workers: Dict[str, asyncio.Task] = {}

_base_logger = get_logger("SendQueue")


async def _get_send_queue(from_uid: str) -> asyncio.Queue:
    """获取或创建用户发送队列，并确保 worker 在运行"""
    async with _send_queues_lock:
        if from_uid not in _send_queues:
            _send_queues[from_uid] = asyncio.Queue()
        queue = _send_queues[from_uid]
        # worker 不存在或已结束则重新创建
        worker = _send_workers.get(from_uid)
        if worker is None or worker.done():
            _send_workers[from_uid] = asyncio.create_task(_send_worker(from_uid, queue))
        return queue


# 同一用户连续发消息的最小间隔（秒），防止拼多多频率限制丢消息
_SEND_INTERVAL = 3.0


async def _send_worker(from_uid: str, queue: asyncio.Queue):
    """用户发送队列 worker，串行消费发送任务，每条间隔 _SEND_INTERVAL 秒"""
    while True:
        try:
            item = await asyncio.wait_for(queue.get(), timeout=300)
            coro, event, need_interval = item
            try:
                await coro
            except Exception as e:
                _base_logger.error(f"[SendQueue] 发送失败 from_uid={from_uid}: {e}")
            finally:
                if event:
                    event.set()
                queue.task_done()
            if need_interval:
                await asyncio.sleep(_SEND_INTERVAL)
        except asyncio.TimeoutError:
            async with _send_queues_lock:
                _send_workers.pop(from_uid, None)
            break


async def send_to_user(
    shop_id: str, user_id: str, from_uid: str, text: str,
    delay: float = 0, wait: bool = False, need_interval: bool = True
) -> None:
    """
    统一发消息入口，入队后串行发出，保证同一用户不并发。

    Args:
        shop_id: 店铺ID
        user_id: 客服账号ID
        from_uid: 用户UID
        text: 消息内容
        delay: 入队前等待秒数
        wait: 是否等待消息实际发出后再返回
        need_interval: 发完后是否等待 _SEND_INTERVAL 秒（即时提示语传 False，AI 回复传 True）
    """
    if delay:
        await asyncio.sleep(delay)
    queue = await _get_send_queue(from_uid)

    async def _do_send():
        await asyncio.to_thread(_send_sync, shop_id, user_id, from_uid, text)

    event = asyncio.Event() if wait else None
    await queue.put((_do_send(), event, need_interval))
    if event:
        await event.wait()


def _send_sync(shop_id: str, user_id: str, from_uid: str, text: str) -> None:
    from Channel.pinduoduo.utils.API.send_message import SendMessage
    result = SendMessage(shop_id, user_id).send_text(from_uid, text)
    if isinstance(result, dict) and result.get("success"):
        _base_logger.info(f"[SendQueue] 发送成功 from_uid={from_uid} text={text[:30]!r}")
    else:
        _base_logger.warning(f"[SendQueue] 发送失败 from_uid={from_uid} result={result}")


class BaseHandler(MessageHandler):
    """处理器基类，提供通用功能"""

    def __init__(self, name: Optional[str] = None):
        super().__init__()
        self.name = name or self.__class__.__name__

    async def log_message(self, context: Context, action: str, extra_info: str = ""):
        """统一的日志记录（不记录完整内容以保护隐私）"""
        user_info = self._get_user_info(context)
        content_preview = str(context.content)[:50] + "..." if context.content else ""
        self.logger.info(f"{self.name} {action} - {user_info} - {content_preview} {extra_info}")

    def _get_user_info(self, context: Context) -> str:
        """提取用户信息"""
        try:
            if hasattr(context, 'kwargs') and context.kwargs:
                from_uid = getattr(context.kwargs, 'from_uid', None)
                username = getattr(context.kwargs, 'username', None)
                if username:
                    return f"用户:{username}({from_uid})"
                elif from_uid:
                    return f"用户:{from_uid}"
            return "用户:unknown"
        except Exception:
            return "用户:unknown"
