"""
消息持久化工具

将客户发送和客服回复的消息内容写入主数据库（message_records 表），
保证 AI 客服可以回溯完整对话，避免客户说了什么却无法被 AI 知道。
"""
import json
from typing import Any, Optional

from database.db_manager import db_manager
from utils.logger_loguru import get_logger

logger = get_logger("MessagePersistence")


def _get_kwargs_value(kwargs: Any, name: str, default=None):
    """从 Context.kwargs 中安全取值，兼容 Pydantic 对象和 dict"""
    if kwargs is None:
        return default
    if isinstance(kwargs, dict):
        return kwargs.get(name, default)
    return getattr(kwargs, name, default)


def _serialize_content(content: Any) -> str:
    """将消息内容统一转为字符串，dict/list 转为 JSON"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False)
    except Exception:
        return str(content)


def save_message_record(
    channel: str = "pinduoduo",
    shop_id: Optional[str] = None,
    account_id: Optional[str] = None,
    from_uid: Optional[str] = None,
    nickname: Optional[str] = None,
    msg_id: Optional[str] = None,
    direction: str = "in",
    msg_type: Optional[str] = None,
    content: Optional[str] = None,
    raw_data: Optional[str] = None,
) -> bool:
    """写入一条消息记录"""
    try:
        return db_manager.save_message(
            channel=channel,
            shop_id=shop_id,
            account_id=account_id,
            from_uid=from_uid,
            nickname=nickname,
            msg_id=msg_id,
            direction=direction,
            msg_type=msg_type,
            content=content,
            raw_data=raw_data,
        )
    except Exception as e:
        logger.warning(f"消息落库失败 direction={direction} from_uid={from_uid}: {e}")
        return False


def record_incoming_message(context) -> bool:
    """记录一条客户发送的消息（从 Context 提取字段）"""
    try:
        kwargs = context.kwargs
        channel = getattr(context.channel_type, "value", None) or str(context.channel_type or "")
        return save_message_record(
            channel=channel,
            shop_id=_get_kwargs_value(kwargs, "shop_id"),
            account_id=_get_kwargs_value(kwargs, "user_id"),
            from_uid=_get_kwargs_value(kwargs, "from_uid"),
            nickname=_get_kwargs_value(kwargs, "nickname"),
            msg_id=_get_kwargs_value(kwargs, "msg_id"),
            direction="in",
            msg_type=getattr(context.type, "value", None) or str(context.type or ""),
            content=_serialize_content(context.content),
            raw_data=_serialize_content(_get_kwargs_value(kwargs, "raw_data")),
        )
    except Exception as e:
        logger.warning(f"记录客户消息失败: {e}")
        return False


def record_outgoing_message(context, content, msg_type: str = "text") -> bool:
    """记录一条客服发送的消息（从 Context 提取字段）"""
    try:
        kwargs = context.kwargs
        channel = getattr(context.channel_type, "value", None) or str(context.channel_type or "")
        return save_message_record(
            channel=channel,
            shop_id=_get_kwargs_value(kwargs, "shop_id"),
            account_id=_get_kwargs_value(kwargs, "user_id"),
            from_uid=_get_kwargs_value(kwargs, "from_uid"),
            nickname=_get_kwargs_value(kwargs, "nickname"),
            msg_id=None,
            direction="out",
            msg_type=msg_type,
            content=_serialize_content(content),
        )
    except Exception as e:
        logger.warning(f"记录客服回复失败: {e}")
        return False


def save_outgoing_raw(
    shop_id: Optional[str],
    user_id: Optional[str],
    from_uid: Optional[str],
    content: Any,
    msg_type: str = "text",
    channel: str = "pinduoduo",
) -> bool:
    """记录一条客服回复，仅需基础字段（供核销等无完整 Context 的场景使用）"""
    return save_message_record(
        channel=channel,
        shop_id=shop_id,
        account_id=user_id,
        from_uid=from_uid,
        direction="out",
        msg_type=msg_type,
        content=_serialize_content(content),
    )


def record_agent_session_message(channel: str, customer_uid: str, role: str, content: Any) -> bool:
    """将消息写入 AI 会话历史（agent_messages），让 AI 能读到人工/系统已发出的内容

    channel: 渠道，如 pinduoduo
    customer_uid: 客户 UID（会话归属）
    role: user / assistant / system
    content: 消息内容
    """
    try:
        from Agent.CustomerAgent.custom.agent_config import AgentConfig
        from Agent.CustomerAgent.custom.session_manager import SessionManager
        agent_config = AgentConfig.load_from_config()
        SessionManager(
            db_path=agent_config.db_path,
            token_window=agent_config.token_window,
            compress_ratio=agent_config.compress_ratio,
            retain_count=agent_config.retain_count,
            model_name=agent_config.model_name,
        ).add_message(
            session_id=f"{channel}{customer_uid}",
            role=role,
            content=_serialize_content(content),
        )
        return True
    except Exception as e:
        logger.warning(f"写入 AI 会话历史失败 channel={channel} uid={customer_uid}: {e}")
        return False
