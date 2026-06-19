"""
优惠券查询工具
"""
from typing import Optional, Union
from pydantic import BaseModel, Field
from Agent.CustomerAgent.custom.tool_decorator import agent_tool
from Channel.pinduoduo.utils.API.voucher_manager import VoucherManager
from utils.logger_loguru import get_logger

logger = get_logger("CouponQueryTool")


class GetCouponListParams(BaseModel):
    shop_id: Optional[Union[str, int]] = Field(default=None, description="店铺ID")
    user_id: Optional[Union[str, int]] = Field(default=None, description="用户ID")
    status: Optional[int] = Field(default=None, description="状态筛选：0-停用，1-启用，2-已结束")


class GetVoucherStatsParams(BaseModel):
    coupon_id: str = Field(..., description="优惠券ID")
    shop_id: Optional[Union[str, int]] = Field(default=None, description="店铺ID")
    user_id: Optional[Union[str, int]] = Field(default=None, description="用户ID")


@agent_tool(
    name="get_coupon_list",
    description="获取店铺的优惠券列表",
    param_model=GetCouponListParams,
)
def get_coupon_list(params: GetCouponListParams) -> str:
    try:
        if not all([params.shop_id, params.user_id]):
            return "查询失败：缺少必要参数"

        voucher_manager = VoucherManager(shop_id=params.shop_id, user_id=params.user_id)
        result = voucher_manager.get_coupon_list(params.status)

        if result["success"]:
            coupons = result.get("coupons", [])
            if not coupons:
                return "当前没有优惠券"

            coupon_type_map = {1: "满减券", 2: "折扣券", 3: "免费券"}
            status_map = {0: "停用", 1: "启用", 2: "已结束"}
            lines = [f"共 {result.get('total', 0)} 张优惠券:\n"]
            for c in coupons:
                line = (
                    f"[{c['coupon_id']}] {c['coupon_name']} "
                    f"类型:{coupon_type_map.get(c['coupon_type'], '未知')} "
                    f"状态:{status_map.get(c['status'], '未知')} "
                    f"剩余:{c['remaining_quantity']}/{c['total_quantity']}"
                )
                lines.append(line)
            return "\n".join(lines)
        else:
            return f"查询失败: {result.get('error_msg', '未知错误')}"

    except Exception as e:
        logger.error(f"查询优惠券列表异常: {e}")
        return f"查询优惠券列表时发生错误: {e}"


@agent_tool(
    name="get_voucher_stats",
    description="获取指定优惠券的核销使用统计",
    param_model=GetVoucherStatsParams,
)
def get_voucher_stats(params: GetVoucherStatsParams) -> str:
    try:
        if not all([params.coupon_id, params.shop_id, params.user_id]):
            return "查询失败：缺少必要参数"

        voucher_manager = VoucherManager(shop_id=params.shop_id, user_id=params.user_id)
        result = voucher_manager.get_voucher_usage_stats(params.coupon_id)

        if result["success"]:
            stats = result.get("stats", {})
            total = result.get("total_quantity", 1)
            used = stats.get("used", 0)
            return (
                f"优惠券: {result.get('coupon_name', '')}\n"
                f"总发放: {total}\n"
                f"未领取: {stats.get('unreceived', 0)}\n"
                f"已领取未使用: {stats.get('received', 0)}\n"
                f"已使用: {used}\n"
                f"已过期: {stats.get('expired', 0)}\n"
                f"使用率: {used / total * 100:.1f}%"
            )
        else:
            return f"查询失败: {result.get('error_msg', '未知错误')}"

    except Exception as e:
        logger.error(f"查询核销统计异常: {e}")
        return f"查询核销统计时发生错误: {e}"
