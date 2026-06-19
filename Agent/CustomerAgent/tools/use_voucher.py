"""
核销码使用工具
"""
from typing import Optional, Union
from pydantic import BaseModel, Field
from Agent.CustomerAgent.custom.tool_decorator import agent_tool
from Channel.pinduoduo.utils.API.voucher_manager import VoucherManager
from utils.logger_loguru import get_logger

logger = get_logger("VoucherUseTool")


class UseVoucherParams(BaseModel):
    voucher_code: str = Field(..., description="要使用的核销码")
    order_id: str = Field(..., description="订单ID")
    order_amount: float = Field(..., description="订单金额")
    shop_id: Optional[Union[str, int]] = Field(default=None, description="店铺ID")
    user_id: Optional[Union[str, int]] = Field(default=None, description="用户ID")


@agent_tool(
    name="use_voucher",
    description="为用户使用核销码，需要提供订单ID和订单金额",
    param_model=UseVoucherParams,
)
def use_voucher(params: UseVoucherParams) -> str:
    try:
        if not all([params.voucher_code, params.order_id, params.shop_id, params.user_id]):
            return "使用失败：缺少必要参数"
        if params.order_amount <= 0:
            return "使用失败：订单金额必须大于0"

        voucher_manager = VoucherManager(shop_id=params.shop_id, user_id=params.user_id)
        result = voucher_manager.use_voucher(params.voucher_code, params.order_id, params.order_amount)

        if result["success"]:
            discount = result.get("discount_amount", 0)
            final_amount = params.order_amount - discount
            return (
                f"核销码使用成功\n"
                f"核销码: {params.voucher_code}\n"
                f"优惠金额: {discount}元\n"
                f"实付金额: {final_amount}元"
            )
        else:
            return f"核销码使用失败: {result.get('error_msg', '未知错误')}"

    except Exception as e:
        logger.error(f"使用核销码异常: {e}")
        return f"使用核销码时发生错误: {e}"
