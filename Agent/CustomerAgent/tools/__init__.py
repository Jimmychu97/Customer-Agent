"""
工具模块

导入所有工具以触发 @agent_tool 装饰器注册。
"""
from Agent.CustomerAgent.tools import send_goods_link
from Agent.CustomerAgent.tools import move_conversation
from Agent.CustomerAgent.tools import get_product_list
from Agent.CustomerAgent.tools import get_product_knowledge
from Agent.CustomerAgent.tools import search_customer_service_knowledge
# 核销功能工具
from Agent.CustomerAgent.tools import verify_voucher        # noqa: F401  — 注册核销码验证工具
from Agent.CustomerAgent.tools import use_voucher          # noqa: F401  — 注册核销码使用工具
from Agent.CustomerAgent.tools import get_voucher_code     # noqa: F401  — 注册核销码生成工具
from Agent.CustomerAgent.tools import coupon_query        # noqa: F401  — 注册优惠券查询工具

__all__ = [
    "send_goods_link",
    "move_conversation",
    "get_product_list",
    "get_product_knowledge",
    "search_customer_service_knowledge",
    "verify_voucher",
    "use_voucher", 
    "get_voucher_code",
    "coupon_query",
]
