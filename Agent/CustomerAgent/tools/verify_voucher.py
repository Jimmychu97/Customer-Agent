"""
核销码验证工具
"""
from typing import Optional, Union
from pydantic import BaseModel, Field
from Agent.CustomerAgent.custom.tool_decorator import agent_tool
from Channel.pinduoduo.utils.API.voucher_manager import VoucherManager
from utils.logger_loguru import get_logger

logger = get_logger("VoucherVerifyTool")


class VerifyVoucherParams(BaseModel):
    voucher_code: str = Field(..., description="用户提供的核销码")
    shop_id: Optional[Union[str, int]] = Field(default=None, description="店铺ID")
    user_id: Optional[Union[str, int]] = Field(default=None, description="用户ID")
    order_amount: Optional[float] = Field(default=None, description="订单金额（可选，用于满减券验证）")


@agent_tool(
    name="verify_voucher",
    description="验证用户提供的核销码是否有效",
    param_model=VerifyVoucherParams,
)
def verify_voucher(params: VerifyVoucherParams) -> str:
    try:
        if not all([params.voucher_code, params.shop_id, params.user_id]):
            return "验证失败：缺少必要参数"

        voucher_manager = VoucherManager(shop_id=params.shop_id, user_id=params.user_id)
        result = voucher_manager.verify_voucher(params.voucher_code, params.order_amount)

        if result["success"]:
            return (
                f"核销码验证成功\n"
                f"优惠券名称: {result.get('coupon_name', '')}\n"
                f"可使用优惠: {result.get('discount_amount', 0)}元"
            )
        else:
            return f"核销码验证失败: {result.get('error_msg', '未知错误')}"

    except Exception as e:
        logger.error(f"验证核销码异常: {e}")
        return f"验证核销码时发生错误: {e}"
