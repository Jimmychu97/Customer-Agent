"""
拼多多商品详情 API

使用 /glide/v2/mms/query/commit/on_shop/detail 接口获取商品详情及SKU规格。
"""
from typing import Dict, Any
from ..base_request import BaseRequest


class GoodsCommitAPI(BaseRequest):
    """商品详情 API，使用商家后台 cookie 鉴权"""

    def __init__(self, shop_id: str, user_id: str):
        super().__init__(shop_id=shop_id, user_id=user_id)

    def _build_headers(self) -> Dict[str, str]:
        anti_content = self.cookies.get('anti_content') or self.cookies.get('anti-content', '')
        return {
            "accept": "application/json, text/plain, */*",
            "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
            "anti-content": anti_content,
            "content-type": "application/json;charset=UTF-8",
            "origin": "https://mms.pinduoduo.com",
            "referer": "https://mms.pinduoduo.com/chat-merchant/index.html",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
        }

    def get_goods_detail(self, goods_id) -> Dict[str, Any]:
        """
        获取商品详情（含 SKU 规格）

        Args:
            goods_id: 商品ID

        Returns:
            {"success": bool, "result": dict, "error_msg": str}
            result 包含 skus、min_group_price 等字段
        """
        url = "https://mms.pinduoduo.com/glide/v2/mms/query/commit/on_shop/detail"
        raw = self.post(url, json_data={"goods_id": goods_id}, headers=self._build_headers())

        if not raw:
            return {"success": False, "error_msg": "请求失败"}
        if not raw.get("success"):
            msg = raw.get("error_msg") or raw.get("errorMsg", "未知错误")
            self.logger.warning(f"获取商品详情失败 goods_id={goods_id}: {msg}")
            return {"success": False, "error_msg": msg}

        return {"success": True, "result": raw.get("result", {})}
