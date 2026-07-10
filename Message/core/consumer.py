"""
消息消费者实现

所有消息并发处理，各 handler 内部自行保证数据安全。
"""

import asyncio
from typing import List, Dict
from utils.logger_loguru import get_logger
from bridge.context import Context
from .queue import queue_manager
from .handlers import MessageHandler
from ..models.queue_models import MessageWrapper


logger = get_logger(__name__)


class MessageConsumer:
    """消息消费者，所有消息并发处理"""

    def __init__(self, queue_name: str, max_concurrent: int = 10):
        self.queue_name = queue_name
        self.handlers: List[MessageHandler] = []
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.running = False
        self.consumer_task = None
        self._tasks: set = set()
        self.logger = get_logger(f"Consumer.{queue_name}")

    def add_handler(self, handler: MessageHandler):
        self.handlers.append(handler)
        self.logger.debug(f"Added handler: {handler.__class__.__name__}")

    def is_running(self) -> bool:
        return self.running

    async def start(self):
        if self.running:
            self.logger.warning(f"Consumer {self.queue_name} is already running")
            return
        self.running = True
        self.consumer_task = asyncio.create_task(self._consume_loop())
        self.logger.info(f"Consumer {self.queue_name} started")

    async def _consume_loop(self):
        queue = queue_manager.get_or_create_queue(self.queue_name)
        try:
            while self.running:
                try:
                    wrapper = await queue.get(timeout=1.0)
                    if wrapper:
                        task = asyncio.create_task(self._process_message(wrapper))
                        self._tasks.add(task)
                        task.add_done_callback(self._tasks.discard)
                except Exception as e:
                    self.logger.error(f"Consumer error: {e}")
                    await asyncio.sleep(0.1)
        finally:
            self.logger.info(f"Consumer {self.queue_name} stopped")

    async def stop(self):
        self.running = False
        if self.consumer_task:
            self.consumer_task.cancel()
            try:
                await self.consumer_task
            except asyncio.CancelledError:
                pass
        if self._tasks:
            pending = [t for t in self._tasks if not t.done()]
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            self._tasks.clear()

    async def _process_message(self, wrapper: MessageWrapper):
        """处理单个消息，全部并发"""
        from bridge.context import ContextType
        try:
            metadata = wrapper.to_metadata()
            try:
                kwargs = getattr(wrapper.context, 'kwargs', None)
                if kwargs:
                    metadata['shop_id'] = getattr(kwargs, 'shop_id', None)
                    metadata['user_id'] = getattr(kwargs, 'user_id', None)
                    metadata['from_uid'] = getattr(kwargs, 'from_uid', None)
            except Exception:
                pass

            # 图片消息：完全独立异步，不占 semaphore
            if wrapper.context.type == ContextType.IMAGE:
                for handler in self.handlers:
                    if handler.can_handle(wrapper.context):
                        asyncio.create_task(handler.handle(wrapper.context, metadata))
                        self.logger.debug(f"Message {wrapper.message_id} dispatched async to {handler.__class__.__name__}")
                        break
                return

            # 其他消息：受 semaphore 限制
            async with self.semaphore:
                for handler in self.handlers:
                    try:
                        if handler.can_handle(wrapper.context):
                            success = await handler.handle(wrapper.context, metadata)
                            if success:
                                self.logger.debug(f"Message {wrapper.message_id} handled by {handler.__class__.__name__}")
                                break
                    except Exception as e:
                        self.logger.error(f"Handler {handler.__class__.__name__} error: {e}")
                        continue
        except Exception as e:
            self.logger.error(f"Failed to process message {wrapper.message_id}: {e}")

    def _extract_user_id(self, context: Context) -> str:
        try:
            from_uid = context.kwargs.from_uid if hasattr(context, 'kwargs') else None
            channel = context.channel_type
            channel_str = str(channel.value) if hasattr(channel, 'value') else str(channel)
            return f"{channel_str}_{from_uid or 'unknown'}"
        except Exception:
            return "unknown_unknown"


class MessageConsumerManager:
    """消息消费者管理器"""

    def __init__(self):
        self._consumers: Dict[str, MessageConsumer] = {}
        self.logger = get_logger("ConsumerManager")

    def create_consumer(self, queue_name: str, max_concurrent: int = 10) -> MessageConsumer:
        if queue_name in self._consumers:
            self.logger.warning(f"Consumer {queue_name} already exists")
            return self._consumers[queue_name]
        consumer = MessageConsumer(queue_name, max_concurrent)
        self._consumers[queue_name] = consumer
        self.logger.info(f"Created consumer: {queue_name}")
        return consumer

    def get_consumer(self, queue_name: str) -> MessageConsumer:
        return self._consumers.get(queue_name)

    async def start_consumer(self, queue_name: str):
        consumer = self.get_consumer(queue_name)
        if consumer:
            await consumer.start()
        else:
            self.logger.error(f"Consumer {queue_name} not found")

    async def stop_consumer(self, queue_name: str):
        consumer = self.get_consumer(queue_name)
        if consumer:
            await consumer.stop()
        else:
            self.logger.error(f"Consumer {queue_name} not found")

    def list_consumers(self) -> List[str]:
        return list(self._consumers.keys())

    async def stop_all(self):
        for consumer in self._consumers.values():
            await consumer.stop()
        self.logger.info("All consumers stopped")


# 全局消费者管理器实例
message_consumer_manager = MessageConsumerManager()
