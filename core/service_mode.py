"""
商品级三态服务模式解析模块

提供按「平台 shop_id + goods_id」解析 product_knowledge.service_mode 的统一入口，
供卡券核销 / 无物流发货 / 消息路由调用。三态含义：
    0 - 卡券核销（商品确实被配置成核销商品时才成立）
    1 - 仅发货（不发卡券核销，走无物流发货）
    2 - 二选一兼容（客户给卡券走核销，只给订单号走发货）

「查不到商品」不等价于「商品配置成卡券核销」：需要区分这两种情况的调用方一律用
*_optional 版本（查不到返回 None），由上层降级为「向客户确认要哪种服务」，
不再默认向客户索要卡券。

数据库访问一律采用函数内延迟导入，避免与 database 包产生循环依赖。
"""
from dataclasses import dataclass
from typing import Any, Dict, Optional
import re

from utils.logger_loguru import get_logger

logger = get_logger("ServiceMode")

SERVICE_MODE_VOUCHER = 0
SERVICE_MODE_SHIP = 1
SERVICE_MODE_AUTO = 2
# 「未配置」显式取值：product_knowledge.service_mode 是 NOT NULL DEFAULT，
# 没有它的话「卖家没配过」和「卖家配成卡券核销」在库里完全一样（都是 0），
# 于是仅发货商品会被要卡券。存量行一律不动（仍是 0），只有新建的行取这个值。
SERVICE_MODE_UNSET = 3
# 兼容兜底值：只用于存量非法值（normalize_service_mode）与旧调用方。
# 它不是「默认卡券核销」——服务模式未确定时必须走 *_optional 版本拿到 None。
DEFAULT_SERVICE_MODE = SERVICE_MODE_VOUCHER
SERVICE_MODE_LABELS = {0: "卡券核销", 1: "仅发货", 2: "二选一兼容", 3: "未配置"}

# 客户只发序列号这类普通文本时，消息里没有商品ID，只能从最近的订单卡/商品卡里回溯
_GOODS_ID_PATTERNS = (
    re.compile(r'"goods_id"\s*:\s*"?(\d{6,})"?'),
    re.compile(r"商品ID\s*[:：]\s*(\d{6,})"),
    re.compile(r"goods_id\D{0,3}(\d{6,})"),
)


def normalize_service_mode(value: Any) -> int:
    """将任意输入统一成合法的服务模式值

    Args:
        value: 原始值，可能是 None、int、字符串数字或非法内容

    Returns:
        0/1/2 之一；无法识别时返回 DEFAULT_SERVICE_MODE
    """
    try:
        if value is None or isinstance(value, bool):
            return DEFAULT_SERVICE_MODE
        mode = int(str(value).strip())
        return mode if mode in SERVICE_MODE_LABELS else DEFAULT_SERVICE_MODE
    except Exception:
        return DEFAULT_SERVICE_MODE


def resolve_service_mode_optional(shop_platform_id: str, goods_id) -> Optional[int]:
    """按平台的 shop_id + goods_id 查询商品配置的服务模式（查不到返回 None）

    与 resolve_service_mode 的区别：商品不在知识库、取值为空或非法时返回 None，
    调用方据此区分「商品确实配置成卡券核销」和「根本没配置」，不再默认索要卡券。

    Args:
        shop_platform_id: 平台侧店铺 ID（shops.shop_id）
        goods_id: 商品 ID，可为 str 或 int

    Returns:
        服务模式 0/1/2；商品未配置（含显式「未配置」=3）或任何异常时返回 None，绝不抛异常
    """
    if goods_id in (None, "", 0, "0"):
        return None
    try:
        from database import db_manager
        from database.models import Shop, ProductKnowledge

        with db_manager.get_session() as session:
            shop = session.query(Shop).filter(Shop.shop_id == str(shop_platform_id)).first()
            if not shop:
                return None
            product = session.query(ProductKnowledge).filter(
                ProductKnowledge.shop_id == shop.id,
                ProductKnowledge.goods_id == int(goods_id),
            ).first()
            if not product or product.service_mode is None:
                return None
            try:
                mode = int(str(product.service_mode).strip())
            except Exception:
                return None
            # 「未配置」按「没有依据」处理，让上层降级为二选一，绝不默认索要卡券
            if mode == SERVICE_MODE_UNSET or mode not in SERVICE_MODE_LABELS:
                return None
            return mode
    except Exception as e:
        logger.warning(f"解析服务模式失败（shop_id={shop_platform_id}, goods_id={goods_id}）: {e}")
        return None


def resolve_service_mode(shop_platform_id: str, goods_id) -> int:
    """兼容接口：查不到商品配置时返回 DEFAULT_SERVICE_MODE

    注意：需要区分「未配置」的调用方必须改用 resolve_service_mode_optional。
    """
    mode = resolve_service_mode_optional(shop_platform_id, goods_id)
    return DEFAULT_SERVICE_MODE if mode is None else mode


# ==============================================================================
# 「客户这一单」的统一判定（订单优先；确定不了就明说未确定）
# ==============================================================================

EVIDENCE_MESSAGE_ORDER = "message_order"    # 本条消息自带商品（订单卡/商品卡）
EVIDENCE_CONVERSATION = "conversation_card"  # 会话绑定的商品（卡片推送直带，不查平台）
EVIDENCE_CLAIMED_ORDER = "claimed_order"    # 已认领到本客户的这一笔待发货单
EVIDENCE_ORDER_DETAIL = "order_detail"      # 按订单号查到的订单详情
EVIDENCE_PENDING_UNIQUE = "pending_unique"  # 全店唯一待发货单
EVIDENCE_PENDING_COMMON = "pending_common"  # 待发货单商品+规格一致
EVIDENCE_HISTORY = "history_message"        # 历史消息里的商品卡（弱依据）
EVIDENCE_LOCAL_ORDER = "local_order"        # 本地落库、且订单号对得上的本单
EVIDENCE_UNSET = "unset"                    # 商品存在但明确「未配置」服务模式
EVIDENCE_NONE = "none"

# 弱依据：能支撑「先别索要卡券」，但不足以反过来说「这单一定要卡券」
WEAK_EVIDENCES = frozenset({EVIDENCE_HISTORY})


@dataclass(frozen=True)
class ServiceModeResolution:
    """一次模式判定的结论与依据

    未确定时 mode 为 None，它**不等于**卡券核销；调用方必须按「二选一」降级，
    不得据此向客户索要卡券。
    """
    mode: Optional[int]
    evidence: str
    goods_id: Optional[str] = None
    spec: str = ""
    order_sn: Optional[str] = None

    @property
    def is_known(self) -> bool:
        return self.mode is not None

    @property
    def is_weak(self) -> bool:
        return self.evidence in WEAK_EVIDENCES


def resolve_service_mode_with_evidence(
    shop_platform_id: str,
    *,
    message_goods_id: Any = None,
    message_spec: str = "",
    order_sn: Optional[str] = None,
    order_detail_goods_id: Any = None,
    order_detail_spec: str = "",
    claimed_order: Optional[Dict[str, Any]] = None,
    picked_order: Optional[Dict[str, Any]] = None,
    common_goods: Optional[Dict[str, Any]] = None,
    history_goods_id: Any = None,
    local_order_mode: Optional[int] = None,
) -> ServiceModeResolution:
    """按「客户这一单到底是哪件商品」把多路线索收敛成一次判定

    优先级（高→低）：
    1. 本条消息自带商品（订单卡/商品卡）
    2. 已认领到本客户的这一笔待发货单
    3. 按订单号查到的订单详情
    4. 全店唯一待发货单（只用于判模式，不对外发单号）
    5. 待发货单商品+规格一致
    6. 历史消息里的商品卡（弱依据）
    7. 本地落库、且订单号对得上的本单

    纯函数：不查数据库、不查平台；线索由调用方在本次消息处理里查好后传入，
    既避免重复查平台，也便于离线测试。
    """
    claimed = claimed_order or {}
    picked = picked_order or {}
    common = common_goods or {}

    candidates = (
        (EVIDENCE_MESSAGE_ORDER, message_goods_id, message_spec, order_sn),
        (EVIDENCE_CLAIMED_ORDER, claimed.get("goods_id"), str(claimed.get("spec") or ""),
         str(claimed.get("order_sn") or order_sn or "")),
        (EVIDENCE_ORDER_DETAIL, order_detail_goods_id, order_detail_spec, order_sn),
        (EVIDENCE_PENDING_UNIQUE, picked.get("goods_id"), str(picked.get("spec") or ""),
         str(picked.get("order_sn") or order_sn or "")),
        (EVIDENCE_PENDING_COMMON, common.get("goods_id"), str(common.get("spec") or ""), order_sn),
        (EVIDENCE_HISTORY, history_goods_id, "", order_sn),
    )

    for evidence, goods_id, spec, sn in candidates:
        if goods_id in (None, "", 0, "0"):
            continue
        # 统一走 database.goods_config：SKU 级 service_mode > 免卡券开关 > 商品级配置。
        # 只查商品级会漏掉「按 SKU 配的服务模式」，导致订单查到了商品却判不出模式。
        mode = None
        try:
            from database.goods_config import resolve_service_mode as _resolve_by_goods

            mode = _resolve_by_goods(goods_id, spec or None, shop_platform_id)
        except Exception as e:
            logger.debug(f"统一配置查询失败 goods_id={goods_id}: {e}")
            mode = resolve_service_mode_optional(shop_platform_id, goods_id)
        if mode is None:
            # 商品查得到但没配服务模式：继续看下一个线索，别在这里塌成「卡券核销」
            logger.info(f"服务模式未配置，尝试下一个线索 goods_id={goods_id} 依据={evidence}")
            continue
        logger.info(
            f"服务模式判定 mode={mode} 依据={evidence} goods_id={goods_id} spec={spec!r}"
        )
        return ServiceModeResolution(
            mode=mode, evidence=evidence, goods_id=str(goods_id),
            spec=spec or "", order_sn=(sn or None),
        )

    if local_order_mode is not None:
        logger.info(
            f"服务模式判定 mode={local_order_mode} 依据={EVIDENCE_LOCAL_ORDER} order_sn={order_sn}"
        )
        return ServiceModeResolution(
            mode=local_order_mode, evidence=EVIDENCE_LOCAL_ORDER, order_sn=(order_sn or None),
        )

    logger.info(f"服务模式未确定（无可核对线索） shop_id={shop_platform_id} order_sn={order_sn}")
    return ServiceModeResolution(mode=None, evidence=EVIDENCE_NONE, order_sn=(order_sn or None))


def infer_goods_id_from_session(
    shop_platform_id: str, customer_uid: str, limit: int = 30
) -> Optional[str]:
    """回溯该客户最近的订单卡/商品卡，拿到商品ID

    客户只发「序列号 VQ627F3QQH」这类普通文本时，消息本身不带商品ID，
    直接按商品解析服务模式会退化成默认的卡券核销。

    Args:
        shop_platform_id: 平台侧店铺 ID
        customer_uid: 客户 UID
        limit: 回溯的消息条数

    Returns:
        商品ID 字符串；找不到返回 None，绝不抛异常
    """
    if not shop_platform_id or not customer_uid:
        return None
    try:
        from database import db_manager

        rows = db_manager.get_recent_messages(
            str(shop_platform_id), str(customer_uid), limit=limit
        )
    except Exception as e:
        logger.debug(f"回溯商品ID失败（shop_id={shop_platform_id}, uid={customer_uid}）: {e}")
        return None

    for row in reversed(rows or []):
        content = row.get("content") or ""
        if not content:
            continue
        for pattern in _GOODS_ID_PATTERNS:
            matched = pattern.search(content)
            if matched:
                return matched.group(1)
    return None


def resolve_service_mode_for_customer_optional(
    shop_platform_id: str, customer_uid: str, goods_id: Any = None
) -> Optional[int]:
    """按客户解析服务模式：消息里有商品ID就用它，没有就回溯该客户最近的商品

    Args:
        shop_platform_id: 平台侧店铺 ID
        customer_uid: 客户 UID（回溯商品用）
        goods_id: 消息中已知的商品 ID，可为空

    Returns:
        服务模式 0/1/2；商品未知或未配置时返回 None（由调用方决定如何降级）
    """
    resolved_goods_id = goods_id if goods_id not in (None, "", 0, "0") else None
    if not resolved_goods_id:
        resolved_goods_id = infer_goods_id_from_session(shop_platform_id, customer_uid)
    if not resolved_goods_id:
        return None
    return resolve_service_mode_optional(shop_platform_id, resolved_goods_id)


def resolve_service_mode_for_customer(
    shop_platform_id: str, customer_uid: str, goods_id: Any = None
) -> int:
    """兼容接口：查不到时返回 DEFAULT_SERVICE_MODE（新代码请用 *_optional 版本）"""
    mode = resolve_service_mode_for_customer_optional(
        shop_platform_id, customer_uid, goods_id)
    return DEFAULT_SERVICE_MODE if mode is None else mode


def resolve_service_mode_from_orders_db(
    shop_platform_id: str, customer_uid: str
) -> ServiceModeResolution:
    """按「该客户名下唯一一笔未完成订单」的落库结论判定模式（纯 DB 读）

    给 AI 侧用：AI 拿不到订单号，也不能在回复前查平台，只能读处理器已经写回库的结论，
    这样 AI 的话术与消息分派的判定口径一致，不会一个说要卡券、一个走发货。

    只有「无歧义」才采用：该客户未完成订单为 0 笔或多笔（可能分属不同服务模式）一律返回
    未确定，避免拿别的单的模式去要卡券。
    """
    if not shop_platform_id or not customer_uid:
        return ServiceModeResolution(mode=None, evidence=EVIDENCE_NONE)
    try:
        from database import db_manager
        from database.models import Order

        with db_manager.session_scope() as session:
            rows = (
                session.query(Order)
                .filter(
                    Order.shop_id == str(shop_platform_id),
                    Order.session_id == str(customer_uid),
                    Order.status != 1,
                    Order.service_mode.isnot(None),
                )
                .order_by(Order.id.desc())
                .all()
            )
            modes = {row.service_mode for row in rows}
            order_sns = [row.order_sn for row in rows if row.order_sn]
    except Exception as e:
        logger.debug(f"按库判定服务模式失败 shop_id={shop_platform_id} uid={customer_uid}: {e}")
        return ServiceModeResolution(mode=None, evidence=EVIDENCE_NONE)

    if len(modes) != 1:
        if len(modes) > 1:
            logger.info(f"该客户有多笔不同模式的未完成单 {sorted(modes)}，不据此判定")
        return ServiceModeResolution(mode=None, evidence=EVIDENCE_NONE,
                                     order_sn=(order_sns[0] if order_sns else None))
    mode = modes.pop()
    # 「未配置」（3）按未确定处理，避免据此索要卡券
    if mode == SERVICE_MODE_UNSET or mode not in SERVICE_MODE_LABELS:
        return ServiceModeResolution(mode=None, evidence=EVIDENCE_UNSET,
                                     order_sn=(order_sns[0] if order_sns else None))
    return ServiceModeResolution(
        mode=mode, evidence=EVIDENCE_LOCAL_ORDER,
        order_sn=(order_sns[0] if order_sns else None),
    )


def describe_service_mode(mode: int) -> str:
    """返回服务模式的中文标签

    Args:
        mode: 服务模式值，允许为 int 或字符串数字

    Returns:
        中文标签；无法识别的值返回 '未知'
    """
    try:
        return SERVICE_MODE_LABELS.get(int(str(mode).strip()), "未知")
    except Exception:
        return "未知"
