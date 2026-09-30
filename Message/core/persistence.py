"""
消息持久化工具

将客户发送和客服回复的消息内容写入主数据库（message_records 表），
保证 AI 客服可以回溯完整对话，避免客户说了什么却无法被 AI 知道。

同时提供"部署自检"能力：当 database/ 目录与 Message/ 不是同一版本
（典型症状：DatabaseManager 缺少 save_message，报
"'DatabaseManager' object has no attribute 'save_message'"）时，
明确报 ERROR 并尝试降级写入，避免静默丢数据。
"""
import inspect
import json
from datetime import datetime
from typing import Any, List, Optional

from database.db_manager import db_manager
from utils.logger_loguru import get_logger

logger = get_logger("MessagePersistence")

# 部署不一致、降级写入只报一次，避免每条消息刷一行日志
_mismatch_reported = False
_fallback_reported = False
_capability_cache: Optional[dict] = None

# agent_messages.source 取值（用于区分"人工客服"与"AI 自动回复"）
SOURCE_CUSTOMER = "customer"   # 客户发送
SOURCE_AI = "ai"               # AI 自动回复
SOURCE_MALL_CS = "mall_cs"     # 本店人工客服发送
SOURCE_VOUCHER = "voucher"     # 核销流程发出的引导语/通知
SOURCE_CONTROL = "control"     # STOP AI / START AI 控制命令
SOURCE_SYSTEM = "system"       # 系统消息


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


# ==============================================================================
# 部署能力探测
# ==============================================================================

def _db_capability(force: bool = False) -> dict:
    """探测当前 DatabaseManager 实例的能力（结果缓存，force=True 重新探测）"""
    global _capability_cache
    if _capability_cache is not None and not force:
        return _capability_cache

    info = {
        "instance": None,
        "instance_file": None,
        "has_save_message": False,
        "has_session_scope": False,
        "has_message_model": False,
        "error": None,
    }
    try:
        inst = db_manager  # _LazyDBProxy -> get_db_manager()
        info["instance"] = inst
        info["has_save_message"] = hasattr(inst, "save_message")
        info["has_session_scope"] = hasattr(inst, "session_scope")
        try:
            info["instance_file"] = inspect.getfile(type(inst))
        except TypeError:
            info["instance_file"] = getattr(type(inst), "__module__", "?")
        try:
            from database.models import MessageRecord  # noqa: F401
            info["has_message_model"] = True
        except Exception:
            info["has_message_model"] = False
    except Exception as e:  # DI 未注册、数据库打不开等
        info["error"] = e

    _capability_cache = info
    return info


def _describe_mismatch(info: dict) -> str:
    return (
        "部署版本不一致：DatabaseManager 缺少 save_message（类文件: %s）。"
        "database/ 目录与 Message/ 不是同一版本，请同步 database/ "
        "（至少 db_manager.py、models.py、__init__.py）后重启"
        % (info.get("instance_file") or "?")
    )


def _report_mismatch(info: dict) -> None:
    """部署不一致只报一次 ERROR（含诊断信息）"""
    global _mismatch_reported
    if _mismatch_reported:
        return
    _mismatch_reported = True
    logger.error(
        f"[消息记录] {_describe_mismatch(info)} | "
        f"has_session_scope={info.get('has_session_scope')} "
        f"has_MessageRecord={info.get('has_message_model')}"
    )


def _fallback_save(info: dict, *, channel, shop_id, account_id, from_uid, nickname,
                   msg_id, direction, msg_type, content, raw_data) -> bool:
    """DatabaseManager 版本过旧时的降级写入（直接用 SQLAlchemy 写 message_records）"""
    global _fallback_reported
    if not (info.get("has_session_scope") and info.get("has_message_model")):
        if not _fallback_reported:
            _fallback_reported = True
            logger.error(
                "[消息记录] 无法降级写入：session_scope 或 MessageRecord 均不可用，"
                "该实例上的消息记录会全部丢失，请尽快同步 database/ 目录"
            )
        return False
    try:
        from database.models import MessageRecord
        with info["instance"].session_scope() as session:
            session.add(MessageRecord(
                channel=channel or "pinduoduo",
                shop_id=str(shop_id) if shop_id is not None else "",
                account_id=str(account_id) if account_id is not None else None,
                from_uid=str(from_uid) if from_uid is not None else "",
                nickname=str(nickname) if nickname is not None else None,
                msg_id=str(msg_id) if msg_id is not None else None,
                direction=direction or "in",
                msg_type=str(msg_type) if msg_type is not None else None,
                content=content,
                raw_data=raw_data,
                created_at=datetime.now(),
            ))
        if not _fallback_reported:
            _fallback_reported = True
            logger.warning(
                "[消息记录] 已启用降级写入（DatabaseManager 版本过旧，缺少 save_message）；"
                "功能可用但请尽快同步 database/ 目录"
            )
        return True
    except Exception as e:
        if not _fallback_reported:
            _fallback_reported = True
            logger.error(f"[消息记录] 降级写入失败: {e}")
        return False


def check_deployment() -> List[str]:
    """部署自检：返回问题列表（空列表 = 一切正常）

    检查项：
    1. DatabaseManager 是否具备 save_message（新版写入接口）
    2. database/models.py 是否包含 MessageRecord（message_records 表模型）
    3. 数据库里 message_records 表是否存在
    """
    problems: List[str] = []
    info = _db_capability(force=True)

    if info.get("error") is not None:
        problems.append(f"无法获取 DatabaseManager 实例: {info['error']}")

    if not info.get("has_save_message"):
        problems.append(_describe_mismatch(info))

    if not info.get("has_message_model"):
        problems.append("database/models.py 缺少 MessageRecord（message_records 表模型），请同步 database/ 目录")

    inst = info.get("instance")
    if inst is not None and hasattr(inst, "engine"):
        try:
            from sqlalchemy import text as _sql_text
            with inst.engine.connect() as conn:
                row = conn.execute(_sql_text(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='message_records'"
                )).fetchone()
            if not row:
                problems.append("数据库中缺少 message_records 表（同步 database/ 目录后重启会自动建表）")
        except Exception as e:
            problems.append(f"检查 message_records 表失败: {e}")

    return problems


# ==============================================================================
# 写入接口
# ==============================================================================

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
    """写入一条消息记录（重复消息由 DatabaseManager 去重，仍返回 True）"""
    info = _db_capability()

    if info.get("has_save_message"):
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

    # 能力缺失：明确报错（一次），并尝试降级写入
    _report_mismatch(info)
    return _fallback_save(
        info,
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
    msg_id: Optional[str] = None,
) -> bool:
    """记录一条客服回复，仅需基础字段（供人工客服消息、核销等无完整 Context 的场景使用）"""
    return save_message_record(
        channel=channel,
        shop_id=shop_id,
        account_id=user_id,
        from_uid=from_uid,
        msg_id=msg_id,
        direction="out",
        msg_type=msg_type,
        content=_serialize_content(content),
    )


def record_agent_session_message(
    channel: str,
    customer_uid: str,
    role: str,
    content: Any,
    source: Optional[str] = None,
    dedup_seconds: int = 120,
) -> bool:
    """将消息写入 AI 会话历史（agent_messages），让 AI 能读到人工/系统已发出的内容

    channel: 渠道，如 pinduoduo
    customer_uid: 客户 UID（会话归属）
    role: user / assistant / system / tool
    content: 消息内容
    source: 来源标记（customer / ai / mall_cs / voucher / control / system），便于区分人工与 AI
    dedup_seconds: 大于 0 时，同会话 + 同角色 + 同内容在该秒数内重复写入会被跳过
        （默认 120 秒，用于避免"自己发出的消息被平台回推"造成历史重复）
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
            source=source,
            dedup_seconds=dedup_seconds,
        )
        return True
    except Exception as e:
        logger.warning(f"写入 AI 会话历史失败 channel={channel} uid={customer_uid}: {e}")
        return False
