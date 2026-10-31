"""
订单无物流发货工具

对指定订单执行无物流（虚拟商品）发货。
先通过订单列表确认该单确实处于待发货状态（shipping_status=0 且 pay_status=2），
确认后才调用发货接口，避免对已发货/已退款订单误操作。
"""
from typing import Optional, Union

from pydantic import BaseModel, Field

from Agent.CustomerAgent.custom.tool_decorator import agent_tool
from Channel.pinduoduo.utils.API.order_api import MmsOrderAPI
from utils.logger_loguru import get_logger

logger = get_logger("ShipOrderTool")


class ShipOrderParams(BaseModel):
    """无物流发货参数"""
    shop_id: Optional[Union[str, int]] = Field(default=None, description="店铺ID")
    user_id: Optional[Union[str, int]] = Field(default=None, description="用户ID（账号ID）")
    order_sn: str = Field(..., description="订单号，如 261028-296296138371626")


@agent_tool(
    name="ship_order",
    description="对指定订单执行无物流发货（虚拟商品）",
    param_model=ShipOrderParams,
)
def ship_order(params: ShipOrderParams) -> str:
    """
    对指定订单执行无物流（虚拟商品）发货。

    Args:
        params: ShipOrderParams，包含 shop_id, user_id, order_sn

    Returns:
        str: 中文结果文案，成功为发货成功提示，失败为失败原因
    """
    try:
        if not all([params.shop_id, params.user_id, params.order_sn]):
            logger.error(
                f"发货失败: 缺少必要参数 (shop_id={params.shop_id}, "
                f"user_id={params.user_id}, order_sn={params.order_sn})"
            )
            return "发货失败：缺少必要参数（shop_id / user_id / order_sn）"

        api = MmsOrderAPI(str(params.shop_id), str(params.user_id))

        pending = api.list_pending_shipment_orders(page_size=100)
        if not pending.get("success"):
            error_msg = pending.get("error_msg") or "查询待发货订单失败"
            logger.error(f"发货失败: 查询待发货订单出错 order_sn={params.order_sn}: {error_msg}")
            return f"发货失败：查询待发货订单出错 - {error_msg}"

        matched = next(
            (o for o in pending.get("orders", []) if o.get("order_sn") == params.order_sn),
            None,
        )
        if not matched or not matched.get("pending_shipment"):
            logger.warning(f"发货跳过: 订单不在待发货状态 order_sn={params.order_sn}")
            return f"未找到订单 {params.order_sn} 或该订单不在待发货状态"

        result = api.ship_without_logistics(params.order_sn)
        if result.get("success"):
            logger.info(f"无物流发货成功 order_sn={params.order_sn}")
            return f"订单 {params.order_sn} 无物流发货成功"

        error_msg = result.get("error_msg") or "未知错误"
        logger.error(f"无物流发货失败 order_sn={params.order_sn}: {error_msg}")
        return f"订单 {params.order_sn} 发货失败：{error_msg}"

    except Exception as e:
        logger.error(f"无物流发货异常 order_sn={params.order_sn}: {str(e)}")
        return f"无物流发货异常: {str(e)}"
