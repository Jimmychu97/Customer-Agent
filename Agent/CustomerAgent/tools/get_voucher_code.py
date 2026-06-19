"""
核销码生成工具
"""
from typing import Optional, Union
from pydantic import BaseModel, Field
from Agent.CustomerAgent.custom.tool_decorator import agent_tool
from Channel.pinduoduo.utils.API.voucher_manager import VoucherManager
from utils.logger_loguru import get_logger

logger = get_logger("VoucherGenerateTool")


class GetVoucherCodeParams(BaseModel):
    coupon_id: str = Field(..., description="优惠券ID")
    customer_user_id: str = Field(..., description="顾客用户ID（领取核销码的用户）")
    shop_id: Optional[Union[str, int]] = Field(default=None, description="店铺ID")
    user_id: Optional[Union[str, int]] = Field(default=None, description="客服用户ID")


@agent_tool(
    name="get_voucher_code",
    description="为用户获取指定优惠券的核销码",
    param_model=GetVoucherCodeParams,
)
def get_voucher_code(params: GetVoucherCodeParams) -> str:
    try:
        if not all([params.coupon_id, params.customer_user_id, params.shop_id, params.user_id]):
            return "生成失败：缺少必要参数"

        voucher_manager = VoucherManager(shop_id=params.shop_id, user_id=params.user_id)
        result = voucher_manager.get_voucher_code(params.coupon_id, params.customer_user_id)

        if result["success"]:
            return (
                f"核销码生成成功\n"
                f"核销码: {result.get('voucher_code', '')}\n"
                f"优惠券: {result.get('coupon_name', '')}\n"
                f"有效期至: {result.get('end_time', '')}"
            )
        else:
            return f"核销码生成失败: {result.get('error_msg', '未知错误')}"

    except Exception as e:
        logger.error(f"生成核销码异常: {e}")
        return f"生成核销码时发生错误: {e}"
