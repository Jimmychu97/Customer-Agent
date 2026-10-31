"""
简化的消息消费者实现
移除复杂的用户隔离机制，保持核心功能
"""

import asyncio
from typing import List, Dict, Any, Optional
from utils.logger_loguru import get_logger
from bridge.context import Context
from .queue import queue_manager
from .handlers import MessageHandler
from ..models.queue_models import MessageWrapper


logger = get_logger(__name__)


class MessageConsumer:
    """消息消费者 - 简化版"""

    def __init__(self, queue_name: str, max_concurrent: int = 10):
        self.queue_name = queue_name
        self.max_concurrent = max_concurrent
        self.handlers: List[MessageHandler] = []
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.running = False
        self.consumer_task = None
        self._tasks: set = set()
        # 消费者任务所属的事件循环：线程/循环换了就不能复用（否则消息入队无人消费）
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self.logger = get_logger(f"Consumer.{queue_name}")

    def add_handler(self, handler: MessageHandler):
        """添加处理器"""
        self.handlers.append(handler)
        self.logger.debug(f"Added handler: {handler.__class__.__name__}")

    def is_running(self) -> bool:
        """检查消费者是否正在运行"""
        return self.running

    def is_bound_to_current_loop(self) -> bool:
        """消费者任务是否仍绑定在当前运行的事件循环上

        每次「启动自动回复」都会新建线程和事件循环；旧消费者的任务留在已退出的
        循环里，此时复用会让消息入队却永远无人消费。
        """
        loop = self._loop
        if loop is None or loop.is_closed():
            return False
        try:
            return loop is asyncio.get_running_loop()
        except RuntimeError:
            return False

    def can_be_reused(self) -> bool:
        """可复用 = 还在跑 且 绑定在当前事件循环"""
        return self.running and self.is_bound_to_current_loop()

    async def start(self):
        """启动消费者"""
        if self.running:
            self.logger.warning(f"Consumer {self.queue_name} is already running")
            return

        self.running = True
        self._loop = asyncio.get_running_loop()
        self.consumer_task = asyncio.create_task(self._consume_loop())
        self.logger.info(f"Consumer {self.queue_name} started")

    async def _consume_loop(self):
        """消费循环"""
        queue = queue_manager.get_or_create_queue(self.queue_name)

        try:
            while self.running:
                try:
                    wrapper = await queue.get(timeout=1.0)
                    if wrapper:
                        # 使用信号量控制并发数，跟踪任务以便优雅停止
                        task = asyncio.create_task(self._process_message(wrapper))
                        self._tasks.add(task)
                        task.add_done_callback(self._tasks.discard)
                except Exception as e:
                    self.logger.error(f"Consumer error: {e}")
                    await asyncio.sleep(0.1)
        finally:
            self.logger.info(f"Consumer {self.queue_name} stopped")

    async def stop(self):
        """停止消费者"""
        self.running = False

        # 取消消费任务
        if hasattr(self, 'consumer_task'):
            self.consumer_task.cancel()
            try:
                await self.consumer_task
            except asyncio.CancelledError:
                pass
            except RuntimeError as e:
                # 任务绑定在已关闭的事件循环上，无法在当前循环里等待
                self.logger.warning(f"Consumer {self.queue_name} 任务所在循环已关闭，跳过等待: {e}")

        # 等待所有正在处理的任务完成
        if self._tasks:
            pending = [t for t in self._tasks if not t.done()]
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            self._tasks.clear()

    async def _process_message(self, wrapper: MessageWrapper):
        """处理单个消息"""
        async with self.semaphore:
            try:
                processed = False
                metadata = wrapper.to_metadata()
                # 追加渠道上下文到metadata，供发送使用
                try:
                    kwargs = getattr(wrapper.context, 'kwargs', None)
                    if kwargs:
                        metadata['shop_id'] = getattr(kwargs, 'shop_id', None)
                        metadata['user_id'] = getattr(kwargs, 'user_id', None)
                        metadata['from_uid'] = getattr(kwargs, 'from_uid', None)
                except Exception:
                    pass
                # 保留用于日志的用户键
                metadata['user_key'] = self._extract_user_id(wrapper.context)

                for handler in self.handlers:
                    try:
                        if handler.can_handle(wrapper.context):
                            success = await handler.handle(wrapper.context, metadata)
                            if success:
                                processed = True
                                self.logger.debug(f"Message {wrapper.message_id} handled by {handler.__class__.__name__}")
                                break
                    except Exception as e:
                        self.logger.error(f"Handler {handler.__class__.__name__} error: {e}")
                        # 尝试下一个处理器
                        continue

                if not processed:
                    self.logger.warning(f"Message {wrapper.message_id} not processed by any handler")

            except Exception as e:
                self.logger.error(f"Failed to process message {wrapper.message_id}: {e}")

    def _extract_user_id(self, context: Context) -> str:
        """提取用户ID"""
        try:
            from_uid = context.kwargs.from_uid if hasattr(context, 'kwargs') else None
            channel = context.channel_type

            # 处理可能的None值
            if from_uid is None:
                from_uid = "unknown"
            if channel is None:
                channel = "unknown"

            # 处理channel可能是字符串或枚举对象的情况
            if hasattr(channel, 'value'):
                channel_str = str(channel.value)
            else:
                channel_str = str(channel)

            return f"{channel_str}_{from_uid}"
        except Exception as e:
            self.logger.error(f"Failed to extract user ID: {e}")
            return "unknown_unknown"


class MessageConsumerManager:
    """消息消费者管理器"""

    def __init__(self):
        self._consumers: Dict[str, MessageConsumer] = {}
        self.logger = get_logger("ConsumerManager")

    def create_consumer(self, queue_name: str, max_concurrent: int = 10) -> MessageConsumer:
        """创建消费者"""
        if queue_name in self._consumers:
            self.logger.warning(f"Consumer {queue_name} already exists")
            return self._consumers[queue_name]

        consumer = MessageConsumer(queue_name, max_concurrent)
        self._consumers[queue_name] = consumer
        self.logger.info(f"Created consumer: {queue_name}")
        return consumer

    def get_consumer(self, queue_name: str) -> MessageConsumer:
        """获取消费者"""
        return self._consumers.get(queue_name)

    async def start_consumer(self, queue_name: str):
        """启动消费者"""
        consumer = self.get_consumer(queue_name)
        if consumer:
            await consumer.start()
        else:
            self.logger.error(f"Consumer {queue_name} not found")

    async def stop_consumer(self, queue_name: str):
        """停止消费者并移出注册表（下次启动会新建，避免复用已失效的事件循环）"""
        consumer = self._consumers.pop(queue_name, None)
        if not consumer:
            self.logger.debug(f"Consumer {queue_name} not found")
            return
        try:
            await consumer.stop()
        except Exception as e:
            self.logger.warning(f"停止消费者 {queue_name} 时异常（忽略）: {e}")

    def list_consumers(self) -> List[str]:
        """列出所有消费者"""
        return list(self._consumers.keys())

    async def stop_all(self):
        """停止所有消费者"""
        consumers = list(self._consumers.values())
        self._consumers.clear()
        for consumer in consumers:
            try:
                await consumer.stop()
            except Exception as e:
                self.logger.warning(f"停止消费者 {consumer.queue_name} 时异常（忽略）: {e}")
        self.logger.info("All consumers stopped")


# 全局消费者管理器实例
message_consumer_manager = MessageConsumerManager()
