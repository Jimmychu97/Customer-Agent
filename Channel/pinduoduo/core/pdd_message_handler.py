# 消息处理模块
import json
import asyncio
from websockets import exceptions as ws_exceptions
from bridge.context import Context, ContextType, ChannelType
from Channel.pinduoduo.pdd_message import PDDChatMessage
from database import db_manager
from utils.logger_loguru import get_logger


class MessageHandlerMixin:
    """消息处理 Mixin"""

    async def _setup_message_consumer(self, queue_name: str, reuse: bool = False):
        """设置消息消费者和处理器链

        Args:
            queue_name: 队列名称
            reuse: True=如果消费者已存在且运行中则直接复用（重连场景）
        """
        from Message import message_consumer_manager, queue_manager, handler_chain
        from Agent.CustomerAgent.custom.customer_agent import CustomerAgent

        try:
            existing_consumer = message_consumer_manager.get_consumer(queue_name)
            if existing_consumer and reuse and existing_consumer.can_be_reused():
                # 只有「还在跑 + 绑定在当前事件循环」才复用；
                # 每次启动自动回复都是新线程新循环，复用旧循环的消费者会让消息入队后无人消费
                self.logger.info(f"复用已有消费者: {queue_name}")
                return
            if existing_consumer:
                if existing_consumer.is_running():
                    self.logger.warning(
                        f"消费者 {queue_name} 绑定的事件循环已失效（已重启线程），重建消费者"
                    )
                else:
                    self.logger.info(f"消费者 {queue_name} 已存在，先停止并重新创建")
                try:
                    await message_consumer_manager.stop_consumer(queue_name)
                except Exception as e:
                    self.logger.warning(f"停止旧消费者失败: {queue_name}, {e}")

            # asyncio.Queue 绑定事件循环，新建消费者前必须重建，否则队列仍挂在已退出的循环上
            try:
                queue_manager.recreate_queue(queue_name)
            except Exception as e:
                self.logger.warning(f"重新创建队列失败: {queue_name}, {e}")

            consumer = message_consumer_manager.create_consumer(queue_name, max_concurrent=10)

            try:
                from core.di_container import container
                bot = container.get(CustomerAgent)
            except Exception:
                bot = CustomerAgent()
            handlers = handler_chain(use_ai=True, businessHours=self.businessHours, bot=bot)
            for handler in handlers:
                consumer.add_handler(handler)

            await message_consumer_manager.start_consumer(queue_name)
            self.logger.info(f"消息消费者已启动: {queue_name}")

        except Exception as e:
            self.logger.error(f"设置消息消费者失败: {e}")
            raise

    async def _process_websocket_message(self, message: str, shop_id: str, user_id: str, username: str, queue_name: str):
        """处理单条WebSocket消息"""
        from Message import put_message

        try:
            if not message or not message.strip():
                self.logger.debug(f"收到空消息，跳过处理: {shop_id}-{username}")
                return

            message_data = json.loads(message)
            msg_type = message_data.get("message", {}).get("type", "unknown")
            from_uid_log = message_data.get("message", {}).get("from_uid", "unknown")
            self.logger.debug(f"收到消息: type={msg_type}, from_uid={from_uid_log}, shop_id={shop_id}")

            try:
                pdd_message = PDDChatMessage(message_data)
            except Exception as pdd_error:
                self.logger.error(f"创建PDD消息对象失败: {shop_id}-{username}, 错误: {pdd_error}")
                return

            try:
                context = self._convert_to_context(pdd_message, shop_id, user_id, username)
                if not context:
                    self.logger.debug(f"消息转换失败，跳过处理: {shop_id}-{username}")
                    return
            except Exception as ctx_error:
                self.logger.error(f"转换Context失败: {shop_id}-{username}, 错误: {ctx_error}")
                return

            if context:
                if context.type == ContextType.MALL_CS:
                    if not self._try_handle_ai_control_command(context):
                        self._record_mall_cs_message(context)
                if self._should_process_immediately(context):
                    await self._handle_immediate_message(context, shop_id, user_id)
                    self.logger.debug(f"立即处理消息: {context.type}, ID: {pdd_message.msg_id}")
                elif self._should_queue_message(context):
                    self._record_incoming_message(context)
                    msg_id = await put_message(queue_name, context)
                    self.logger.debug(f"消息已入队: {queue_name}, ID: {msg_id}, 类型: {context.type}")
                else:
                    self.logger.debug(f"忽略消息: {context.type}, ID: {pdd_message.msg_id}")
            else:
                self.logger.warning("消息转换失败，跳过处理")

        except json.JSONDecodeError:
            self.logger.error(f"JSON解析失败: {message}")
        except Exception as e:
            self.logger.error(f"处理WebSocket消息失败: {e}")

    def _should_process_immediately(self, context: Context) -> bool:
        """判断消息是否需要立即处理"""
        immediate_types = {
            ContextType.SYSTEM_STATUS,
            ContextType.AUTH,
            ContextType.WITHDRAW,
            ContextType.SYSTEM_HINT,
            ContextType.MALL_CS,
            ContextType.TRANSFER
        }
        return context.type in immediate_types

    def _should_queue_message(self, context: Context) -> bool:
        """判断消息是否需要放入队列处理"""
        queue_types = {
            ContextType.TEXT,
            ContextType.IMAGE,
            ContextType.VIDEO,
            ContextType.EMOTION,
            ContextType.GOODS_INQUIRY,
            ContextType.ORDER_INFO,
            ContextType.GOODS_CARD,
            ContextType.GOODS_SPEC,
        }
        return context.type in queue_types

    def _record_incoming_message(self, context: Context):
        """将客户发送的消息内容落库（只记录进入队列处理的消息）"""
        try:
            from Message.core.persistence import record_incoming_message
            record_incoming_message(context)
        except Exception as e:
            self.logger.debug(f"记录客户消息失败: {e}")

    def _record_mall_cs_message(self, context: Context):
        """记录本店客服（mall_cs）发出的消息

        PDD 消息中 mall_cs 的 from/to 与客户消息相反：from_uid 是本店客服账号，
        to_uid 才是客户 UID。这里按客户 UID 归属，写入消息记录表并同步到 AI 会话历史，
        让 AI 之后能知道人工已经回复过什么。
        """
        try:
            kwargs = context.kwargs
            channel = getattr(context.channel_type, "value", None) or "pinduoduo"
            shop_id = getattr(kwargs, "shop_id", None)
            user_id = getattr(kwargs, "user_id", None)
            customer_uid = getattr(kwargs, "to_uid", None) or getattr(kwargs, "from_uid", None)
            content = context.content or ""
            if not customer_uid:
                return

            from Message.core.persistence import save_outgoing_raw, record_agent_session_message
            save_outgoing_raw(
                shop_id=shop_id,
                user_id=user_id,
                from_uid=str(customer_uid),
                content=content,
                msg_type="mall_cs",
                channel=channel,
            )
            record_agent_session_message(
                channel=channel,
                customer_uid=str(customer_uid),
                role="assistant",
                content=content,
            )
        except Exception as e:
            self.logger.debug(f"记录本店客服消息失败: {e}")

    def _try_handle_ai_control_command(self, context: Context) -> bool:
        """识别并执行客服发送的 STOP AI / START AI 控制命令（不区分大小写）

        只针对「当前客户」：
        - STOP AI  → 暂停该客户的自动回复，默认 3 分钟后自动恢复
        - START AI → 立即恢复该客户的自动回复

        命令必须由人工客服发出（MALL_CS），同账号下其他客户不受影响。
        """
        try:
            import re
            raw = str(context.content or "")
            # 去掉空白与标点后再比对，兼容 "STOP AI" / "stop ai." / "STOPAI"
            normalized = re.sub(r"[\s\W_]+", "", raw).upper()

            if normalized not in ("STOPAI", "STARTAI"):
                return False

            kwargs = context.kwargs
            channel = getattr(context.channel_type, "value", None) or "pinduoduo"
            shop_id = getattr(kwargs, "shop_id", None)
            user_id = getattr(kwargs, "user_id", None)
            # mall_cs 消息的 from_uid 是客服账号，to_uid 才是客户
            customer_uid = getattr(kwargs, "to_uid", None) or getattr(kwargs, "from_uid", None)
            if not customer_uid:
                self.logger.warning("[AI控制] 无法确定客户 UID，忽略控制命令")
                return False

            from Message.core.ai_pause import ai_pause_manager, DEFAULT_PAUSE_SECONDS
            if normalized == "STARTAI":
                ai_pause_manager.resume(channel, shop_id, user_id, customer_uid)
                self.logger.info(f"[AI控制] 收到 START AI，客户 {customer_uid} 立即恢复自动回复")
            else:
                ai_pause_manager.pause(channel, shop_id, user_id, customer_uid, DEFAULT_PAUSE_SECONDS)
                self.logger.info(f"[AI控制] 收到 STOP AI，客户 {customer_uid} 暂停自动回复 {DEFAULT_PAUSE_SECONDS}s")

            # 记录控制命令到消息记录表（仅审计，不写入 AI 会话历史）
            from Message.core.persistence import save_outgoing_raw
            save_outgoing_raw(
                shop_id=shop_id,
                user_id=user_id,
                from_uid=str(customer_uid),
                content=raw,
                msg_type="mall_cs_control",
                channel=channel,
            )
            return True
        except Exception as e:
            self.logger.error(f"[AI控制] 处理控制命令失败: {e}")
            return False

    async def _handle_immediate_message(self, context: Context, shop_id: str, user_id: str):
        """立即处理消息"""
        username = context.kwargs.username
        recipient_uid = context.kwargs.from_uid
        try:
            from Channel.pinduoduo.utils.API.send_message import SendMessage
            send_message = SendMessage(shop_id, user_id)
            if context.type == ContextType.AUTH:
                auth_info = context.content
                if isinstance(auth_info, dict):
                    result = auth_info.get('result')
                    if result == 'ok':
                        self.logger.info(f"{username}认证成功")
                    else:
                        self.logger.warning(f"{username}认证失败")

            elif context.type == ContextType.WITHDRAW:
                self.logger.info(f"收到撤回消息: {context.content}")
                send_message.send_text(recipient_uid, "[玫瑰]")

            elif context.type == ContextType.SYSTEM_STATUS:
                self.logger.debug(f"系统状态消息: {context.content}")

            elif context.type == ContextType.SYSTEM_HINT:
                self.logger.info(f"系统提示: {context.content}")

            elif context.type == ContextType.MALL_CS:
                self.logger.debug(f"收到客服消息: {context.content}")

            elif context.type == ContextType.SYSTEM_BIZ:
                self.logger.info(f"系统业务消息: {context.content}")

            elif context.type == ContextType.MALL_SYSTEM_MSG:
                self.logger.info(f"商城系统消息: {context.content}")

            elif context.type == ContextType.TRANSFER:
                self.logger.info(f"转接消息: {context.content}")
                send_message.send_text(recipient_uid, "[玫瑰]")

        except Exception as e:
            self.logger.error(f"立即处理消息失败: {e}")

    def _convert_to_context(self, pdd_message: PDDChatMessage, shop_id: str, user_id: str, username: str) -> Context:
        """将拼多多消息转换为Context格式"""
        shop_info = db_manager.get_shop(self.channel_name, shop_id)
        shop_name = shop_info.get("shop_name", "")

        context_type = pdd_message.user_msg_type

        content = pdd_message.content
        if isinstance(content, dict):
            content = json.dumps(content, ensure_ascii=False)
        elif content is None:
            content = ""
        else:
            content = str(content)

        context = Context.create_pinduoduo_context(
            content=content,
            msg_id=str(pdd_message.msg_id) if pdd_message.msg_id is not None else "",
            from_user=str(pdd_message.from_user) if pdd_message.from_user is not None else "",
            from_uid=str(pdd_message.from_uid) if pdd_message.from_uid is not None else "",
            to_user=str(pdd_message.to_user) if pdd_message.to_user is not None else "",
            to_uid=str(pdd_message.to_uid) if pdd_message.to_uid is not None else "",
            nickname=str(pdd_message.nickname) if pdd_message.nickname is not None else "",
            timestamp=pdd_message.timestamp,
            user_msg_type=pdd_message.user_msg_type,
            shop_id=str(shop_id),
            user_id=str(user_id),
            username=str(username),
            shop_name=str(shop_name),
            raw_data=pdd_message.raw_data,
            channel_type=ChannelType.PINDUODUO
        )
        return context


__all__ = ['MessageHandlerMixin']
