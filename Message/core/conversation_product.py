"""会话当前商品绑定

记录「这个客户当前在办哪个商品」，用于判定走「卡券核销」还是「发货」，
也供 AI 判断是否还应该向客户索要卡券。

数据来源：客户在聊天里发出的订单卡片(info.goodsID/info.spec)、商品卡片、
规格卡片，由拼多多 WebSocket 推送直接带出，**无需额外调用平台订单接口**
（订单列表接口没有买家 UID，且经常被风控，靠它定位商品并不可靠）。
"""
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from utils.logger_loguru import get_logger

logger = get_logger("ConversationProduct")

# 绑定有效期（分钟）：超过该时长的商品不再视为"当前商品"，避免把几天前的商品套到新会话
BIND_TTL_MINUTES = 120


def bind_product(
    shop_id: str,
    from_uid: str,
    goods_id: Any,
    spec_name: Optional[str] = None,
    order_sn: Optional[str] = None,
) -> bool:
    """记录/更新会话当前商品（每个会话只保留一行，后到覆盖）

    Args:
        shop_id: 平台店铺ID
        from_uid: 客户UID
        goods_id: 商品ID（订单卡片/商品卡片里的 goodsID）
        spec_name: 规格名称，缺省时若该商品在 goods_commit 中只有一个规格则自动回填
        order_sn: 平台订单号（仅订单卡片有）

    Returns:
        bool: 是否写入成功
    """
    if not shop_id or not from_uid or not goods_id:
        return False

    goods_id = str(goods_id).strip()
    if not goods_id:
        return False

    spec_name = (spec_name or "").strip() or None

    try:
        from database import db_manager
        from database.models import ConversationProduct

        if not spec_name:
            spec_name = _backfill_single_spec(goods_id)

        with db_manager.session_scope() as session:
            row = session.query(ConversationProduct).filter_by(
                shop_id=str(shop_id), from_uid=str(from_uid)
            ).first()
            if row is None:
                row = ConversationProduct(
                    shop_id=str(shop_id),
                    from_uid=str(from_uid),
                    goods_id=goods_id,
                    spec_name=spec_name,
                    order_sn=order_sn,
                )
                session.add(row)
            else:
                row.goods_id = goods_id
                # 空 spec 不覆盖已有非空 spec，避免商品卡片把订单卡片的规格冲掉
                if spec_name:
                    row.spec_name = spec_name
                if order_sn:
                    row.order_sn = order_sn
            session.flush()

        logger.info(
            f"[会话商品] 已绑定 shop_id={shop_id} from_uid={from_uid} "
            f"goods_id={goods_id} spec={spec_name!r} order_sn={order_sn!r}"
        )
        return True
    except Exception as e:
        logger.warning(f"[会话商品] 绑定失败 shop_id={shop_id} from_uid={from_uid}: {e}")
        return False


def get_bound_product(shop_id: str, from_uid: str) -> Optional[Dict[str, Any]]:
    """读取会话当前商品（超期返回 None）

    Returns:
        {"goods_id": str, "spec_name": str|None, "order_sn": str|None} 或 None
    """
    if not shop_id or not from_uid:
        return None

    try:
        from database import db_manager
        from database.models import ConversationProduct

        since = datetime.now() - timedelta(minutes=BIND_TTL_MINUTES)
        with db_manager.session_scope() as session:
            row = session.query(ConversationProduct).filter(
                ConversationProduct.shop_id == str(shop_id),
                ConversationProduct.from_uid == str(from_uid),
                ConversationProduct.updated_at >= since,
            ).first()
            if row is None:
                return None
            return {
                "goods_id": row.goods_id,
                "spec_name": row.spec_name or None,
                "order_sn": row.order_sn or None,
            }
    except Exception as e:
        logger.warning(f"[会话商品] 读取失败 shop_id={shop_id} from_uid={from_uid}: {e}")
        return None


def clear_binding(shop_id: str, from_uid: str) -> None:
    """删除会话商品绑定（会话结束或商品明确切换时调用）"""
    if not shop_id or not from_uid:
        return
    try:
        from database import db_manager
        from database.models import ConversationProduct

        with db_manager.session_scope() as session:
            session.query(ConversationProduct).filter_by(
                shop_id=str(shop_id), from_uid=str(from_uid)
            ).delete()
    except Exception as e:
        logger.debug(f"[会话商品] 清理失败: {e}")


def _backfill_single_spec(goods_id: str) -> Optional[str]:
    """商品只有一个规格时直接回填，多规格返回 None（等订单卡片补 spec）"""
    try:
        from database import db_manager
        from database.models import GoodsCommit

        with db_manager.session_scope() as session:
            specs = {
                s for (s,) in session.query(GoodsCommit.spec_name).filter(
                    GoodsCommit.goods_id == goods_id,
                    GoodsCommit.spec_name.isnot(None),
                ).all() if s
            }
        return specs.pop() if len(specs) == 1 else None
    except Exception as e:
        logger.debug(f"[会话商品] 回填规格失败 goods_id={goods_id}: {e}")
        return None


__all__ = ["BIND_TTL_MINUTES", "bind_product", "get_bound_product", "clear_binding"]
