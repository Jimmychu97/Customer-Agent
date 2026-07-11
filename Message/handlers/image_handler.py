"""
图片消息处理器

流程：千问 VL 读取图片内容 → 路由到核销处理链
"""
import asyncio
from collections import defaultdict
from typing import Dict, Any, Optional
from bridge.context import Context, ContextType
from .base import BaseHandler, send_to_user
from .voucher_handler import VoucherDetectionHandler, _has_success_order_today
from .ai_handler import AIReplyHandler
from utils.logger_loguru import get_logger

logger = get_logger("ImageHandler")

# 每个用户的图片计数器，给"稍等"加序号防止拼多多去重
_image_counters: Dict[str, int] = defaultdict(int)

# 模块级客户端缓存（所有 ImageHandler 实例共享，避免重复建连）
_vl_client = None
_vl_client_lock = asyncio.Lock()



async def _get_vl_client():
    """获取或创建千问 VL 客户端（模块级缓存，加锁防并发重复初始化）"""
    global _vl_client
    if _vl_client is not None:
        return _vl_client
    async with _vl_client_lock:
        if _vl_client is not None:  # double-check
            return _vl_client
        from config import config
        from Agent.CustomerAgent.custom.llm_client import LLMClient
        api_key = config.get("vl_llm.api_key", "")
        api_base = config.get("vl_llm.api_base", "")
        model_name = config.get("vl_llm.model_name", "")
        if not all([api_key, api_base, model_name]):
            return None
        client = LLMClient(api_key=api_key, api_base=api_base, model_name=model_name, temperature=0.1)
        await client.initialize()
        _vl_client = client
        return client


class ImageHandler(BaseHandler):
    """图片消息处理器：千问提取内容 → 路由到核销或 AI"""

    def __init__(self):
        super().__init__("ImageHandler")
        self._voucher_handler = VoucherDetectionHandler()
        self._ai_handler = AIReplyHandler()

    def can_handle(self, context: Context) -> bool:
        return context.type == ContextType.IMAGE

    async def handle(self, context: Context, metadata: Dict[str, Any]) -> bool:
        image_url = context.content or ""
        if not image_url:
            return False

        from_uid = metadata.get("from_uid", "")
        shop_id = metadata.get("shop_id", "")
        user_id = metadata.get("user_id", "")
        session_id = f"pinduoduo{from_uid}"

        self.logger.info(f"[图片] 开始识别, URL={image_url}")

        _image_counters[from_uid] += 1
        seq = _image_counters[from_uid]
        await send_to_user(shop_id, user_id, from_uid, f"{seq}.稍等正在查看~")
        recognized_text = await self._recognize_image(image_url)

        if not recognized_text:
            self.logger.warning("[图片] 千问识别失败")
            # 识别失败也 fire-and-forget，快速释放 user_lock
            asyncio.create_task(
                self._handle_recognition_failure(shop_id, user_id, from_uid, session_id, image_url)
            )
            return True

        self.logger.info(f"[图片] 千问识别内容: {recognized_text[:300]}")

        # 写会话历史：fire-and-forget
        asyncio.create_task(self._write_session_async(session_id, "user",
            f"[用户发送图片]\nURL: {image_url}\n\n[图片内容]\n{recognized_text}"))

        fake_context = context.model_copy(update={
            "type": ContextType.TEXT,
            "content": recognized_text,
        })

        # 已核销成功的用户，千问识别内容直接交给 AI 客服回答
        if await asyncio.to_thread(_has_success_order_today, shop_id, from_uid):
            self.logger.info(f"[图片] 用户 {from_uid} 已核销，内容交给 AI 处理")
            asyncio.create_task(self._ai_handler.handle(fake_context, metadata))
        else:
            # 未核销：用专用 prompt 二次提取核销信息，避免 OCR 原文误匹配
            voucher_text = await self._voucher_handler._ocr_image(image_url)
            if voucher_text:
                # 有核销信息，用提取结果替换 content
                voucher_context = context.model_copy(update={
                    "type": ContextType.TEXT,
                    "content": voucher_text,
                })
                asyncio.create_task(self._voucher_handler.handle(voucher_context, metadata))
            else:
                # 无核销信息，原始 OCR 文字交给 AI
                self.logger.info(f"[图片] 未提取到核销信息，内容交给 AI 处理")
                asyncio.create_task(self._ai_handler.handle(fake_context, metadata))
        return True

    async def _recognize_image(self, image_url: str, retry: bool = False) -> Optional[str]:
        """千问 VL 读取图片"""
        try:
            client = await _get_vl_client()
            if not client:
                self.logger.warning("[图片] 千问 VL 配置不完整")
                return None

            prompt = "OCR模式：原样输出图中所有文字，禁止分析、禁止加工、禁止总结。如果是实物外观照片则简述外观状况。" if not retry else "输出图中所有可见内容。"
            response = await client.chat(
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": image_url}},
                        {"type": "text", "text": prompt},
                    ]
                }],
                tool_choice="none",
                max_tokens=300,
            )
            result = (response.content or "").strip()
            return result if result else None

        except Exception as e:
            self.logger.error(f"[图片] 千问识别失败: {e}")
            return None

    async def _handle_recognition_failure(
        self, shop_id: str, user_id: str, from_uid: str, session_id: str, image_url: str
    ) -> None:
        """识别失败回复"""
        order_status = await asyncio.to_thread(self._get_latest_order_status, shop_id, from_uid)
        if order_status == 1:
            recognized_text = await self._recognize_image(image_url, retry=True)
            if recognized_text:
                asyncio.create_task(self._write_session_async(session_id, "user",
                    f"[用户发送图片]\nURL: {image_url}\n\n[千问提取内容]\n{recognized_text}"))
                await send_to_user(shop_id, user_id, from_uid,
                    f"图片内容：{recognized_text[:200]}")
                return
        await send_to_user(shop_id, user_id, from_uid,
            "图片看不清😅 请直接发文字：序列号/IMEI 或 卡券号")

    def _get_latest_order_status(self, shop_id: str, from_uid: str) -> Optional[int]:
        """查询该用户最近一条订单状态（同步，供 to_thread 调用）"""
        try:
            from database import db_manager
            from database.models import Order
            from datetime import datetime, timedelta
            one_day_ago = datetime.now() - timedelta(days=1)
            with db_manager.session_scope() as session:
                order = session.query(Order).filter(
                    Order.shop_id == shop_id,
                    Order.session_id == from_uid,
                    Order.created_at >= one_day_ago,
                ).order_by(Order.id.desc()).first()
                return order.status if order else None
        except Exception as e:
            self.logger.warning(f"[图片] 查询订单状态失败: {e}")
            return None

    async def _write_session_async(self, session_id: str, role: str, content: str) -> None:
        """异步写入会话历史（to_thread 包裹同步 DB 操作）"""
        try:
            await asyncio.to_thread(self._write_session_sync, session_id, role, content)
        except Exception as e:
            self.logger.warning(f"[图片] 写入会话历史失败: {e}")

    def _write_session_sync(self, session_id: str, role: str, content: str) -> None:
        from Agent.CustomerAgent.custom.agent_config import AgentConfig
        from Agent.CustomerAgent.custom.session_manager import SessionManager
        agent_config = AgentConfig.load_from_config()
        SessionManager(
            db_path=agent_config.db_path,
            token_window=agent_config.token_window,
            compress_ratio=agent_config.compress_ratio,
            retain_count=agent_config.retain_count,
            model_name=agent_config.model_name,
        ).add_message(session_id=session_id, role=role, content=content)
