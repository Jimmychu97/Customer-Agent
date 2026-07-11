"""
AI回复处理器
"""

from typing import Dict, Any, Optional
from bridge.context import Context, ContextType
from .base import BaseHandler, send_to_user
from .preprocessor import MessagePreprocessor
from Agent.bot import Bot


class AIReplyHandler(BaseHandler):
    """专注的AI回复处理器"""

    def __init__(self, bot: Bot = None, auto_reply_types: set = None):
        super().__init__("AIReplyHandler")
        if bot is None:
            try:
                from core.di_container import container
                from Agent.CustomerAgent.custom.customer_agent import CustomerAgent
                bot = container.get(CustomerAgent)
            except Exception as e:
                from utils.logger_loguru import get_logger
                get_logger("AIReplyHandler").warning(f"从DI容器获取CustomerAgent失败: {e}, 将使用无Bot模式")
        self.bot = bot
        self.preprocessor = MessagePreprocessor()
        self.auto_reply_types = auto_reply_types or {
            ContextType.TEXT,
            ContextType.GOODS_INQUIRY,
            ContextType.GOODS_SPEC,
            ContextType.ORDER_INFO,
            ContextType.VIDEO,
            ContextType.EMOTION
        }

    def can_handle(self, context: Context) -> bool:
        return context.type in self.auto_reply_types

    async def handle(self, context: Context, metadata: Dict[str, Any]) -> bool:
        try:
            shop_id = metadata.get('shop_id')
            user_id = metadata.get('user_id')
            from_uid = metadata.get('from_uid', '')

            if not all([shop_id, user_id, from_uid]):
                self.logger.warning(f"缺少发送信息: shop_id={shop_id}, user_id={user_id}, from_uid={from_uid}")
                return False

            # 核销进行中，跳过 AI 回复
            from Message.handlers.voucher_handler import _verifying_users
            if from_uid in _verifying_users:
                self.logger.info(f"[AI] 用户 {from_uid} 核销进行中，跳过 AI 回复")
                return False

            self.logger.info(f"[AI] 开始处理 from_uid={from_uid} content={str(context.content)[:50]!r}")

            if context.type == ContextType.VIDEO:
                await send_to_user(shop_id, user_id, from_uid, "亲，收到视频啦！😊 不过我无法识别视频内容哦～有什么问题直接文字告诉我吧！")
                return True
<<<<<<< HEAD

            # 图片消息不应到这里（应由 ImageHandler 处理）
            if context.type == ContextType.IMAGE:
                self.logger.warning("[AI] 收到图片消息，应由 ImageHandler 处理，跳过")
                return False
=======
>>>>>>> 15334c8044a085aca4996a48865d7fe1271e0767

            processed_content = self.preprocessor.process(context.content, context.type)
            reply = await self._get_ai_reply(processed_content, context)

            if not reply:
                self.logger.warning("AI回复生成失败，使用备用回复")
                await send_to_user(shop_id, user_id, from_uid, "亲，感谢您的咨询！我稍后为您解答，请稍等片刻～😊")
                return True

            await send_to_user(shop_id, user_id, from_uid, reply)
            await self.log_message(context, "AI回复发送成功", f"回复: {reply}...")
            return True

        except Exception as e:
            self.logger.error(f"AI回复处理失败: {e}")
            return False

    async def _get_ai_reply(self, query: str, context: Context) -> Optional[str]:
        """获取AI回复"""
        if not self.bot:
            return None
        try:
            if hasattr(self.bot, 'async_reply'):
                res = await self.bot.async_reply(query, context)
                return getattr(res, 'content', str(res))
            elif hasattr(self.bot, 'reply'):
                res = self.bot.reply(query, context)
                return getattr(res, 'content', str(res))
            return None
        except Exception as e:
            self.logger.error(f"AI Bot调用失败: {e}")
            return None
