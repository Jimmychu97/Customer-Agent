"""商品验机配置与服务形态的唯一查询入口
=========================================

按 `goods_id`（+ 可选 `spec_name`）读取 `GoodsCommit` 上的：
  - 验机编码：`server_id`（安卓 GSX）/ `zhenxin_server_id`（苹果真心）
  - `device_type`
  - `service_mode`：0 卡券核销 / 1 仅发货 / 2 二选一兼容（SKU 级，优先）
  - `no_coupon_needed`：旧版免卡券开关（`service_mode` 为空时的兼容依据）

被三处共用，避免各写一份、口径打架：
  1. 核销/发货处理器（决定走哪条路、查哪个验机服务）
  2. AI 消息构建（判断是否还该向客户索要卡券）
  3. 待办/话术提示

判定顺序（**以"客户这一单的商品"为准，不依赖平台订单查询**）：
  1. SKU 级 `goods_commit.service_mode`
  2. SKU 级 `no_coupon_needed`（True→仅发货，False→卡券核销）
  3. 商品级 `product_knowledge.service_mode`
  4. 都没有 → None（未确定，上层按"二选一"处理，绝不主动索要卡券）
"""
from typing import Any, Dict, Optional

from utils.logger_loguru import get_logger

logger = get_logger("GoodsConfig")

SERVICE_MODE_VOUCHER = 0
SERVICE_MODE_SHIP = 1
SERVICE_MODE_AUTO = 2

_EMPTY_CONFIG: Dict[str, Optional[Any]] = {
    "server_id": None,
    "zhenxin_server_id": None,
    "device_type": None,
    "service_mode": None,
    "no_coupon_needed": False,
}


def get_verify_config(goods_id: Any, spec_name: Optional[str] = None) -> Dict[str, Any]:
    """读取某个 goods_id (+ 可选规格) 的验机配置与服务模式

    有规格时按「商品 + 规格」精确匹配；规格为空时按商品 ID 查找服务器配置。
    商品下多个 SKU 只有在查询编码完全一致时才允许商品级兜底，编码不一致时返回空配置，
    防止把多规格商品误查成另一种套餐。

    Args:
        goods_id: 商品ID（可 str/int）
        spec_name: 规格名称（与 goods_commit.spec_name 对应，可为空）

    Returns:
        {"server_id", "zhenxin_server_id", "device_type", "service_mode", "no_coupon_needed"}
    """
    goods_key = str(goods_id or "").strip()
    spec_key = str(spec_name or "").strip()
    if not goods_key or goods_key == "0":
        return dict(_EMPTY_CONFIG)

    try:
        from database import db_manager
        from database.models import GoodsCommit

        with db_manager.session_scope() as session:
            query = session.query(GoodsCommit).filter(
                GoodsCommit.goods_id == goods_key,
            )
            if spec_key:
                gc = query.filter(GoodsCommit.spec_name == spec_key).first()
            else:
                rows = query.filter(
                    (GoodsCommit.server_id.isnot(None))
                    | (GoodsCommit.zhenxin_server_id.isnot(None))
                ).all()
                signatures = {
                    (row.server_id, row.zhenxin_server_id, row.device_type,
                     row.service_mode, bool(row.no_coupon_needed))
                    for row in rows
                }
                if len(signatures) != 1:
                    logger.warning(
                        f"[验机配置] 商品级兜底失败，查询编码不唯一 "
                        f"goods_id={goods_key} rows={len(rows)}")
                    return dict(_EMPTY_CONFIG)
                gc = rows[0] if rows else None
                if gc:
                    logger.info(
                        f"[验机配置] 规格为空，按商品ID兜底 goods_id={goods_key} "
                        f"zhenxin={gc.zhenxin_server_id} server_id={gc.server_id}")
            if not gc:
                return dict(_EMPTY_CONFIG)
            mode = gc.service_mode
            try:
                mode = int(mode) if mode is not None else None
            except Exception:
                mode = None
            if mode not in (SERVICE_MODE_VOUCHER, SERVICE_MODE_SHIP, SERVICE_MODE_AUTO):
                mode = None
            return {
                "server_id": str(gc.server_id) if gc.server_id else None,
                "zhenxin_server_id": str(gc.zhenxin_server_id) if gc.zhenxin_server_id else None,
                "device_type": gc.device_type,
                "service_mode": mode,
                "no_coupon_needed": bool(gc.no_coupon_needed),
            }
    except Exception as e:
        logger.error(f"[验机配置] 查询失败 goods_id={goods_id} spec={spec_name!r}: {e}")
        return dict(_EMPTY_CONFIG)


def _sku_service_mode(goods_id: Any, spec_name: Optional[str]) -> Optional[int]:
    """取该商品的服务模式配置（规格精确优先，取不到就按商品任一规格，用于模式判定）

    与验机编码不同，服务形态不是"查哪个接口"的凭据，同一商品各规格通常是同一种形态；
    规格名对不上时按商品取一条，比直接判成"未确定"更贴近实际。
    """
    cfg = get_verify_config(goods_id, spec_name)
    if cfg.get("service_mode") is not None or cfg.get("no_coupon_needed"):
        return cfg.get("service_mode") if cfg.get("service_mode") is not None else SERVICE_MODE_SHIP

    try:
        from database import db_manager
        from database.models import GoodsCommit

        with db_manager.session_scope() as session:
            gc = session.query(GoodsCommit).filter(
                GoodsCommit.goods_id == str(goods_id)).first()
            if gc is None:
                return None
            if gc.service_mode is not None:
                try:
                    mode = int(gc.service_mode)
                except Exception:
                    return None
                return mode if mode in (SERVICE_MODE_VOUCHER, SERVICE_MODE_SHIP,
                                       SERVICE_MODE_AUTO) else None
            if gc.no_coupon_needed:
                return SERVICE_MODE_SHIP
    except Exception as e:
        logger.debug(f"[服务模式] 读取 SKU 配置失败 goods_id={goods_id}: {e}")
    return None


def resolve_service_mode(
    goods_id: Any, spec_name: Optional[str] = None, shop_platform_id: Optional[str] = None
) -> Optional[int]:
    """按「客户这一单的商品」判定服务模式（查不到返回 None=未确定）

    优先级：SKU 级 service_mode > SKU 级 no_coupon_needed(仅显式 True) > 商品级 service_mode。
    None 不等价于「卡券核销」，调用方必须按二选一降级，不得据此索要卡券。

    注意：`no_coupon_needed` 只认显式 True。历史迁移会把该列填成 0（无法与"没配"区分），
    所以 False 一律当作"没有信号"，继续看商品级配置，避免把所有商品都判成核销。
    """
    if goods_id in (None, "", 0, "0"):
        return None

    mode = _sku_service_mode(goods_id, spec_name)
    if mode is not None:
        logger.info(
            f"[服务模式] 依据=sku_config goods_id={goods_id} spec={spec_name!r} mode={mode}")
        return mode

    # 回退到商品级配置（存量数据都在 product_knowledge.service_mode）
    try:
        from core.service_mode import resolve_service_mode_optional

        mode = resolve_service_mode_optional(shop_platform_id, goods_id)
    except Exception as e:
        logger.debug(f"[服务模式] 商品级配置查询失败 goods_id={goods_id}: {e}")
        mode = None
    if mode is not None:
        logger.info(
            f"[服务模式] 依据=product_config goods_id={goods_id} mode={mode}")
    return mode


def is_no_coupon(
    goods_id: Any, spec_name: Optional[str] = None, shop_platform_id: Optional[str] = None
) -> bool:
    """该商品/规格是否「不要卡券」（走发货而不是卡券核销）

    仅用于话术与分支：只有**确定**为仅发货时才返回 True；未配置返回 False，
    调用方据此不能断定"该要卡券"，仍需按未确定处理。
    """
    return resolve_service_mode(goods_id, spec_name, shop_platform_id) == SERVICE_MODE_SHIP


def resolve_service_mode_for_session(
    shop_platform_id: Optional[str],
    from_uid: Optional[str],
    fallback_goods_id: Any = None,
    fallback_spec: Optional[str] = None,
) -> Dict[str, Any]:
    """按「客户这一单的商品」判定服务模式（处理器与 AI 共用这一个入口）

    优先级（**同一会话里客户可能换商品，所以本条消息自带的更可信**）：
      1. 本条消息自带的商品（订单卡/商品卡/规格卡推送里直带 goodsID+spec）
      2. 会话里绑定的商品（同一客户之前发过的卡片，TTL 内有效）
      都没有就返回 None（未确定），平台订单查询由调用方另作兜底。

    Returns:
        {"mode": int|None, "goods_id": str|None, "spec": str|None,
         "order_sn": str|None, "evidence": str}
    """
    result = {"mode": None, "goods_id": None, "spec": None,
              "order_sn": None, "evidence": "none"}

    bound = None
    try:
        if shop_platform_id and from_uid:
            from Message.core.conversation_product import get_bound_product

            bound = get_bound_product(str(shop_platform_id), str(from_uid))
    except Exception as e:
        logger.debug(f"[服务模式] 读取会话绑定失败 shop={shop_platform_id} uid={from_uid}: {e}")

    # 1) 本条消息自带的商品最可信
    if fallback_goods_id not in (None, "", 0, "0"):
        mode = resolve_service_mode(fallback_goods_id, fallback_spec, shop_platform_id)
        if mode is not None:
            result.update(mode=mode, goods_id=str(fallback_goods_id), spec=fallback_spec,
                          order_sn=(bound or {}).get("order_sn"),
                          evidence="message_goods")
            logger.info(
                f"[服务模式] 依据=message_goods goods_id={fallback_goods_id} "
                f"spec={fallback_spec!r} mode={mode}")
            return result

    # 2) 会话绑定的商品
    if bound:
        goods_id = bound.get("goods_id")
        spec = bound.get("spec_name")
        mode = resolve_service_mode(goods_id, spec, shop_platform_id)
        if mode is not None:
            result.update(mode=mode, goods_id=str(goods_id), spec=spec,
                          order_sn=bound.get("order_sn"), evidence="conversation_card")
            logger.info(
                f"[服务模式] 依据=conversation_card goods_id={goods_id} spec={spec!r} "
                f"mode={mode}")
            return result

    if bound:
        result["goods_id"] = str(bound.get("goods_id") or "") or None
        result["spec"] = bound.get("spec_name")
        result["order_sn"] = bound.get("order_sn")
        result["evidence"] = "conversation_card_unconfigured"
    return result


__all__ = [
    "SERVICE_MODE_VOUCHER",
    "SERVICE_MODE_SHIP",
    "SERVICE_MODE_AUTO",
    "get_verify_config",
    "resolve_service_mode",
    "resolve_service_mode_for_session",
    "is_no_coupon",
]
