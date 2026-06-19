"""
拼多多 MMS 卡券核销 API

基于 MMS 商家后台 cookie 方式，与项目其他接口保持一致。
- preview:        验券（查询券码对应订单信息）
- verification:   执行核销
- query_records:  查询核销记录
"""
import time as _time
from ..base_request import BaseRequest
from typing import Dict, Any, Optional


class MmsVoucherAPI(BaseRequest):
    """MMS 卡券核销，使用商家后台 cookie 鉴权"""

    def __init__(self, shop_id: str, user_id: str):
        super().__init__(shop_id=shop_id, user_id=user_id)

    def _build_headers(self) -> Dict[str, str]:
        anti_content = self.cookies.get('anti_content') or self.cookies.get('anti-content', '')
        return {
            "accept": "*/*",
            "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
            "anti-content": anti_content,
            "content-type": "application/json",
            "origin": "https://mms.pinduoduo.com",
            "referer": "https://mms.pinduoduo.com/orders/order/verify",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
        }

    def preview(self, card_no: str) -> Dict[str, Any]:
        """
        验券 - 查询券码对应订单和商品信息

        Returns:
            {"success": bool, "order_sn", "goods_name", "spec",
             "order_amount"(分), "store_id", "order_display_status", "error_msg"}
        """
        url = "https://mms.pinduoduo.com/maryland/api/carolina/ota/verification/preview"
        raw = self.post(url, json_data={"cardNo": card_no}, headers=self._build_headers())

        if not raw:
            return {"success": False, "error_msg": "请求失败"}
        if not raw.get("success"):
            msg = raw.get("errorMsg", "未知错误")
            self.logger.warning(f"验券失败 card_no={card_no}: {msg}")
            return {"success": False, "error_msg": msg}

        r = raw.get("result", {})
        return {
            "success": True,
            "order_sn": r.get("orderSn"),
            "goods_id": r.get("goodsId"),
            "goods_name": r.get("goodsName"),
            "goods_number": r.get("goodsNumber"),
            "spec": r.get("spec"),
            "order_amount": r.get("orderAmount"),
            "order_display_status": r.get("orderDisplayStatus"),
            "store_id": r.get("storeId"),
            "thumb_img": r.get("thumbImg"),
        }

    def verification(self, card_no: str, order_sn: str = "",
                     store_id: int = 0, store_name: str = "") -> Dict[str, Any]:
        """
        执行核销

        Args:
            card_no:    卡券号
            order_sn:   订单号（从 preview 获取）
            store_id:   门店ID
            store_name: 门店名称

        Returns:
            {"success": bool, "error_msg": str}
        """
        mms_app_id = "ac926292463764"

        url = "https://mms.pinduoduo.com/maryland/api/carolina/ota/verification"
        payload = {
            "cardNo": card_no,
            "orderSn": order_sn or "",
            "storeId": store_id if store_id else "",
            "storeName": store_name or "",
            "AppID": mms_app_id,
            "style": 11,
        }
        raw = self.post(url, json_data=payload, headers=self._build_headers())

        if not raw:
            return {"success": False, "error_msg": "请求失败"}
        if raw.get("success"):
            self.logger.info(f"核销成功 card_no={card_no} order_sn={order_sn}")
            return {"success": True}

        msg = raw.get("errorMsg", "核销失败")
        self.logger.warning(f"核销失败 card_no={card_no}: {msg}")
        return {"success": False, "error_msg": msg}

    def query_records(self, page_num: int = 1, page_size: int = 20,
                      begin_time: int = None, end_time: int = None) -> Dict[str, Any]:
        """
        查询核销记录

        Args:
            page_num:   页码
            page_size:  每页数量
            begin_time: 开始时间戳（秒），默认30天前
            end_time:   结束时间戳（秒），默认当前

        Returns:
            {"success": bool, "total": int, "records": [...], "error_msg": str}
            records 字段：order_sn, card_no, goods_name, spec, order_amount,
                          status(0成功/1失败), verification_time, err_msg, err_code
        """
        now = int(_time.time())
        payload = {
            "pageNum": page_num,
            "pageSize": page_size,
            "verificationBeginTime": begin_time or (now - 30 * 24 * 3600),
            "verificationEndTime": end_time or now,
        }
        url = "https://mms.pinduoduo.com/maryland/api/carolina/ota/verification/query"
        raw = self.post(url, json_data=payload, headers=self._build_headers())

        if not raw:
            return {"success": False, "error_msg": "请求失败", "total": 0, "records": []}
        if not raw.get("success"):
            return {"success": False, "error_msg": raw.get("errorMsg", "查询失败"), "total": 0, "records": []}

        result = raw.get("result", {})
        records = [
            {
                "order_sn": r.get("orderSn"),
                "card_no": r.get("cardNo"),
                "goods_name": r.get("goodsName"),
                "spec": r.get("spec"),
                "order_amount": r.get("orderAmount"),
                "status": r.get("status"),
                "verification_time": r.get("verificationTime"),
                "verify_mall_name": r.get("verifyMallName"),
                "err_msg": r.get("errMsg"),
                "err_code": r.get("errCode"),
            }
            for r in result.get("result", [])
        ]
        return {"success": True, "total": result.get("total", 0), "records": records}
