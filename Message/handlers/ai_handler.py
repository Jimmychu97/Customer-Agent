"""
AI回复处理器
专注的AI处理，移除复杂预处理和发送逻辑
"""

import asyncio
from typing import Dict, Any, Optional
from bridge.context import Context, ContextType
from .base import BaseHandler
from .preprocessor import MessagePreprocessor
from Agent.bot import Bot



class AIReplyHandler(BaseHandler):
    """专注的AI回复处理器"""

    def __init__(self, bot: Bot = None, auto_reply_types: set = None):
        super().__init__("AIReplyHandler")
        # 从 DI 容器获取 CustomerAgent（如果未传入）
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
            ContextType.IMAGE,
            ContextType.VIDEO,
            ContextType.EMOTION
        }

    def can_handle(self, context: Context) -> bool:
        """检查是否可以处理该消息"""
        # 支持多种消息类型
        return context.type in self.auto_reply_types

    async def handle(self, context: Context, metadata: Dict[str, Any]) -> bool:
        """处理AI回复"""
        try:
            # 图片/视频消息直接回固定文字，不走 AI
            if context.type == ContextType.IMAGE:
                from config import config as _cfg
                if not _cfg.get("business_hours.image_reply_enabled", True):
                    reply = "亲，我无法识别图片内容哦～😊 请直接用文字描述您的问题，我来帮您解答！"
                    return await self._send_reply(context, reply, metadata)

                # 先发等待提示
                await self._send_reply(context, "好的，正在识别图片内容，请耐心等待....（10-20秒）😊", metadata)

                image_url = self._extract_image_url(context.content)
                if image_url:
                    reply = await self._get_ai_reply_with_image(image_url, context, metadata)
                    if reply is None:
                        return True  # 核销处理器已接管
                    if reply:
                        return await self._send_reply(context, reply, metadata)
                reply = "亲，图片读取失败了～😅 请直接把序列号/IMEI码或卡券号以文字发给我哦！"
                return await self._send_reply(context, reply, metadata)

            if context.type == ContextType.VIDEO:
                reply = "亲，收到视频啦！😊 不过我无法识别视频内容哦～有什么问题直接文字告诉我吧！"
                return await self._send_reply(context, reply, metadata)

            # 1. 预处理消息
            processed_content = self.preprocessor.process(context.content, context.type)

            # 2. 调用AI生成回复
            reply = await self._get_ai_reply(processed_content, context)
            if not reply:
                self.logger.warning("AI回复生成失败，使用备用回复")
                return await self._handle_fallback(context, metadata)

            # 3. 检查是否有未完成的核销订单，追加引导提示
            pending_hint = self._get_pending_voucher_hint(
                metadata.get("shop_id"), metadata.get("from_uid")
            )
            if pending_hint:
                reply = reply + "\n\n" + pending_hint

            # 4. 发送回复
            success = await self._send_reply(context, reply, metadata)
            if success:
                await self.log_message(context, "AI回复发送成功", f"回复: {reply}...")
            else:
                self.logger.warning("AI回复发送失败")
                return await self._handle_fallback(context, metadata)

            return True

        except Exception as e:
            self.logger.error(f"AI回复处理失败: {e}")
            return await self._handle_fallback(context, metadata)

    def _extract_image_url(self, content: Optional[str]) -> Optional[str]:
        """从图片消息 content 中提取图片 URL"""
        if not content:
            return None
        try:
            import json
            data = json.loads(content) if isinstance(content, str) else content
            if isinstance(data, dict):
                return data.get("url") or data.get("thumb_url")
            if isinstance(data, str) and data.startswith("http"):
                return data
        except Exception:
            if isinstance(content, str) and content.startswith("http"):
                return content
        return None

    async def _get_ai_reply_with_image(self, image_url: str, context: Context, metadata: Dict[str, Any] = None) -> Optional[str]:
        """图片识别：调用专用接口（不走工具循环），识别后检查是否含核销信息"""
        if not self.bot:
            return None
        try:
            # 使用专用图片识别接口，不走 Agent 工具循环
            if not hasattr(self.bot, 'recognize_image'):
                return None
            recognized_text = await self.bot.recognize_image(image_url, context)

            if not recognized_text:
                return None

            self.logger.info(f"[图片识别] LLM识别结果: {recognized_text!r}")

            if metadata:
                from Message.handlers.voucher_handler import (
                    _extract_imei, _extract_serial, _extract_coupon,
                    VoucherDetectionHandler
                )
                shop_id = metadata.get("shop_id")
                from_uid = metadata.get("from_uid")

                # 根据待核销订单缺失项，决定图片只提取哪一种信息
                # 一张图片只对应一个条件（序列号/IMEI 或 卡券号），不同时提取两种
                handler = VoucherDetectionHandler()
                pending = handler._get_pending_order_state(shop_id, from_uid)

                # 提取图片里的序列号/IMEI 与卡券号。
                # 注意：不再根据待核销单的缺失项把另一个已识别到的值丢掉——
                # 客户重复发送同一张卡券/序列号时，交由 VoucherDetectionHandler
                # 按卡券号/序列号复用同一张单，避免重复建单与空参数核销。
                sn = _extract_imei(recognized_text) or _extract_serial(recognized_text)
                coupon = _extract_coupon(recognized_text, exclude_imei=sn)

                self.logger.info(f"[图片识别] pending={pending!r} 提取结果 sn={sn!r} coupon={coupon!r}")
                if sn or coupon:
                    self.logger.info("[图片识别] 转交 VoucherDetectionHandler 处理")
                    voucher_context = Context(
                        type=ContextType.TEXT,
                        content=recognized_text,
                        channel_type=context.channel_type,
                        kwargs=context.kwargs,
                    )
                    # 传入已提取好的值，避免 handle 内部重新提取时误识别 IMEI2 等为卡券号
                    image_metadata = {**metadata, "_image_sn": sn, "_image_coupon": coupon}
                    handled = await handler.handle(voucher_context, image_metadata)
                    if handled:
                        return None  # 核销处理器已发送消息
                    self.logger.info("[图片识别] 核销处理器未接管，走普通AI回复")
                self.logger.info("[图片识别] 未检测到核销信息，走普通AI回复")

            return recognized_text
        except Exception as e:
            self.logger.warning(f"LLM 图片识别失败: {e}")
        return None

    async def _get_ai_reply(self, query: str, context: Context) -> Optional[str]:
        """获取AI回复"""
        if not self.bot:
            return None

        try:
            # 优先使用异步接口，其次回退到同步接口
            if hasattr(self.bot, 'async_reply'):
                res = await self.bot.async_reply(query, context)
                return getattr(res, 'content', str(res))
            elif hasattr(self.bot, 'reply'):
                res = self.bot.reply(query, context)
                return getattr(res, 'content', str(res))
            else:
                self.logger.warning("Bot不支持reply或async_reply方法")
                return None

        except Exception as e:
            self.logger.error(f"AI Bot调用失败: {e}")
            return None

    async def _send_reply(self, context: Context, reply: str, metadata: Dict[str, Any]) -> bool:
        """发送回复"""
        try:
            shop_id = metadata.get('shop_id')
            user_id = metadata.get('user_id')
            from_uid = metadata.get('from_uid')

            if not all([shop_id, user_id, from_uid]):
                self.logger.warning(f"缺少发送信息: shop_id={shop_id}, user_id={user_id}, from_uid={from_uid}")
                return False

            from Channel.pinduoduo.utils.API.send_message import SendMessage
            result = await asyncio.to_thread(
                lambda: SendMessage(shop_id, user_id).send_text(from_uid, reply)
            )
            success = isinstance(result, dict) and result.get("success")
            if success:
                self._record_outgoing(context, reply)
                if context.type in (ContextType.IMAGE, ContextType.VIDEO):
                    self._record_session_reply(context, reply)
            else:
                self.logger.warning(f"[AI] 发送失败 from_uid={from_uid} result={result}")
            return bool(success)

        except Exception as e:
            self.logger.error(f"发送回复失败: {e}")
            return False

    def _record_outgoing(self, context: Context, content: str):
        """记录 AI 客服回复内容到数据库（客服回复统一为文本消息）"""
        try:
            from Message.core.persistence import record_outgoing_message
            record_outgoing_message(context, content, msg_type="text")
        except Exception as e:
            self.logger.debug(f"记录客服回复失败: {e}")

    def _record_session_reply(self, context: Context, content: str):
        """将图片/视频等未走 CustomerAgent 的固定回复写入 AI 会话历史"""
        try:
            from Message.core.persistence import record_agent_session_message
            channel = getattr(context.channel_type, "value", None) or "pinduoduo"
            from_uid = getattr(context.kwargs, "from_uid", None)
            if not from_uid:
                return
            record_agent_session_message(
                channel=channel,
                customer_uid=str(from_uid),
                role="assistant",
                content=content,
            )
        except Exception as e:
            self.logger.debug(f"记录固定回复会话历史失败: {e}")

    def _get_pending_voucher_hint(self, shop_id: Optional[str], from_uid: Optional[str]) -> str:
        """查询该用户是否有未完成的核销订单，返回引导缺少项的提示语

        统计范围与 VoucherDetectionHandler._select_order 一致：
        待核销(0) + 核销失败(2) 的单都算（都可以继续补/纠正），已核销成功(1) 的不算。
        """
        if not shop_id or not from_uid:
            return ""
        try:
            from database import db_manager
            from database.models import Order
            from datetime import datetime as dt, timedelta
            from Message.handlers.voucher_handler import PENDING_WINDOW_MINUTES
            since = dt.now() - timedelta(minutes=PENDING_WINDOW_MINUTES)
            with db_manager.session_scope() as session:
                order = session.query(Order).filter(
                    Order.shop_id == shop_id,
                    Order.session_id == from_uid,
                    Order.status.notin_((1, 4)),
                    Order.created_at >= since,
                ).order_by(Order.id.desc()).first()
                if not order:
                    return ""
                has_sn = bool(order.sn_imei)
                has_coupon = bool(order.coupons)

                # 服务模式按「客户下单的商品」分流：发货单不索要卡券
                from core.service_mode import SERVICE_MODE_SHIP, SERVICE_MODE_VOUCHER

                if order.service_mode == SERVICE_MODE_SHIP:
                    if not has_sn:
                        return (
                            "🔔 待发货，还缺序列号/IMEI！\n"
                            "拨号输入 *#06# 可查 IMEI，或设置 → 通用 → 关于本机 → 序列号，发给我即可完成查询。"
                        )
                    if not order.order_sn:
                        return (
                            "🔔 待发货，还缺拼多多订单号！\n"
                            "【待收货 → 点击商品 → 复制订单号】发给我即可完成发货。"
                        )
                    return ""

                # 服务模式未确定（商品没配置）：不默认索要卡券，让客户发订单卡片（卡里带商品ID）
                if order.service_mode != SERVICE_MODE_VOUCHER:
                    if has_sn and not has_coupon:
                        return (
                            "🔔 还缺拼多多订单号！\n"
                            "【待收货 → 我的订单 → 选该商品 → 复制订单号】发我即可完成发货与查询。"
                        )
                    if has_coupon and not has_sn:
                        return (
                            "🔔 还缺序列号/IMEI！\n"
                            "拨号输入 *#06# 可查 IMEI，或设置 → 通用 → 关于本机 → 序列号。"
                        )
                    return ""

                if has_sn and not has_coupon:
                    return (
                        "🔔 核销进行中，还缺卡券号！\n"
                        "待收货 → 点击商品 → 查看卡券 → 复制卡券号发给我即可完成核销。"
                    )
                if has_coupon and not has_sn:
                    return (
                        "🔔 核销进行中，还缺序列号/IMEI！\n"
                        "拨号输入 *#06# 可查 IMEI，或设置 → 通用 → 关于本机 → 序列号，发给我即可完成核销。"
                    )
                return ""
        except Exception as e:
            self.logger.warning(f"[核销提示] 查询失败: {e}")
            return ""

    async def _handle_fallback(self, context: Context, metadata: Dict[str, Any]) -> bool:
        """备用回复处理"""
        try:
            # 简单的自动回复
            reply_text = "亲，感谢您的咨询！我稍后为您解答，请稍等片刻～😊"

            # 记录备用回复
            self.logger.info("使用备用回复")

            # 尝试发送备用回复
            success = await self._send_reply(context, reply_text, metadata)
            if not success:
                # 如果发送失败，记录日志并返回False让下游有机会处理
                await self.log_message(context, "备用回复发送失败", f"内容: {reply_text}")
                return False

            await self.log_message(context, "备用回复发送成功", f"内容: {reply_text}")
            return True

        except Exception as e:
            self.logger.error(f"备用回复处理失败: {e}")
            return True  # 即使失败也返回True，避免重复处理
