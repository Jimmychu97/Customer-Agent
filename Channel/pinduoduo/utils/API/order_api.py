"""
拼多多商家后台订单 API

基于 MMS 商家后台 cookie 鉴权，提供订单列表查询与无物流（虚拟）发货。
接口地址、请求字段、响应字段均来自真机抓包实测，不推测、不扩展：
- recentOrderList:  订单列表查询（支持分页，接口无独立详情端点）
- shipping/single:  无物流（虚拟商品）发货
"""
import time as _time
from typing import Dict, Any, List, Optional

from ..base_request import BaseRequest

# 待发货判定常量：未发货(shipping_status=0) 且 已支付(pay_status=2)
SHIPPING_STATUS_UNSHIPPED = 0
PAY_STATUS_PAID = 2

# 无物流发货默认虚拟发货内容（抓包 seq=462 原值："5" + 反馈文案）
DEFAULT_VIRTUAL_CONTENT = ["5", "序列号查询反馈内容,在聊天窗口。请注意查收~"]

# 分页查询上限
ORDER_PAGE_SIZE_MAX = 100
DETAIL_MAX_PAGES = 5
# 按订单号翻页查详情时的页大小：实测 50 可用，100 会被平台回「单页数据量过大或非法」
DETAIL_PAGE_SIZE = 50
DETAIL_PAGE_SIZE_FALLBACK = 20

# 单笔发货成功码：orderShipResultList[].shippingResultCode（实测成功值为 100）
SHIP_RESULT_CODE_SUCCESS = 100

# 商家后台主机（仅用于 origin / referer 请求头；业务 URL 在类内按抓包原值完整声明）
MMS_HOST = "https://mms.pinduoduo.com"

# 平台把风控/限流伪装成「操作太过频繁」（error_code=40002）
_RATE_LIMIT_HINTS = ("40002", "太过频繁")


def _is_rate_limited(code=None, msg: str = "") -> bool:
    text = f"{code or ''} {msg or ''}"
    return any(hint in text for hint in _RATE_LIMIT_HINTS)


# 平台嫌单页太大时的报错（pageSize=100 会踩到）
_PAGE_SIZE_HINTS = ("单页数据量过大", "数据量过大", "非法")


def _is_page_size_invalid(msg: str = "") -> bool:
    text = str(msg or "")
    return any(hint in text for hint in _PAGE_SIZE_HINTS)


class MmsOrderAPI(BaseRequest):
    """拼多多商家后台订单查询与无物流发货，使用商家后台 cookie 鉴权"""

    ORDER_LIST_URL = "https://mms.pinduoduo.com/mangkhut/mms/recentOrderList"
    SHIP_SINGLE_URL = "https://mms.pinduoduo.com/robot/shop/orders/shipping/single"
    # 合并发货页的「待发货」新接口：实测不受 recentOrderList 的 40002 风控影响
    MERGE_ORDER_LIST_URL = "https://mms.pinduoduo.com/mars/shop/mergeShipping/newOrderList"

    def __init__(self, shop_id: str, user_id: str):
        super().__init__(shop_id=shop_id, user_id=user_id)

    def _build_headers(self) -> Dict[str, str]:
        """构造商家后台接口请求头

        anti-content 必须是**现算的签名**（页面 JS 生成，几分钟就过期）。
        旧实现取 cookies 里的 `anti_content`，但登录流程从没写过这个键，所以发出去的一直是
        空签名 —— 平台固定回 40002，订单永远查不到。这里优先用 anti_content 模块里
        刚抓到的签名，其次才是历史遗留的 cookies 值。
        """
        from Channel.pinduoduo import anti_content as _anti

        anti_value = (_anti.current(self.account_name)
                      or self.cookies.get('anti_content')
                      or self.cookies.get('anti-content', ''))
        return {
            "accept": "*/*",
            "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
            "anti-content": anti_value,
            "content-type": "application/json",
            "origin": MMS_HOST,
            "referer": f"{MMS_HOST}/orders/list?tab=1",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
        }

    def _ensure_anti_content(self, force: bool = False) -> None:
        """请求前确保签名新鲜（阻塞，可能开一次浏览器；只在 40002 重试时用）"""
        try:
            from Channel.pinduoduo import anti_content as _anti

            _anti.ensure(self.account_name, self.proxy, force=force)
        except Exception as e:
            self.logger.debug(f"[anti-content] 确保签名失败（继续用旧值）: {e}")

    def _warm_anti_content(self) -> None:
        """请求前催一下签名（**不阻塞**）：缓存没有就让后台线程去抄，别卡住消息处理"""
        try:
            from Channel.pinduoduo import anti_content as _anti

            _anti.ensure_background(self.account_name, self.proxy)
        except Exception as e:
            self.logger.debug(f"[anti-content] 后台取签名失败: {e}")

    def _post_order_list(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """打一次 recentOrderList；40002 时换一份新签名再打一次"""
        raw = self.post(self.ORDER_LIST_URL, json_data=payload, headers=self._build_headers())
        if not raw or raw.get("success"):
            return raw
        code = raw.get("errorCode") or raw.get("error_code")
        msg = raw.get("errorMsg") or raw.get("error_msg") or ""
        if not _is_rate_limited(code, msg):
            return raw
        from Channel.pinduoduo import anti_content as _anti

        self.logger.info(
            f"[订单查询] 订单列表返回风控（{code}: {msg}），换新 anti-content 重试一次 "
            f"账号={self.account_name}")
        _anti.invalidate(self.account_name)
        self._ensure_anti_content(force=True)
        raw2 = self.post(self.ORDER_LIST_URL, json_data=payload,
                         headers=self._build_headers())
        if raw2 and not raw2.get("success"):
            code2 = raw2.get("errorCode") or raw2.get("error_code")
            msg2 = raw2.get("errorMsg") or raw2.get("error_msg") or ""
            if _is_rate_limited(code2, msg2):
                # 换了新签名还是被拒：把缓存作废，下次重新抓（别把坏签名留 20 分钟）
                self.logger.warning(
                    f"[订单查询] 换新签名后仍被风控（{code2}），作废缓存 账号={self.account_name}")
                _anti.invalidate(self.account_name)
        return raw2

    def _normalize_order(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """把响应 pageItems 中的原始订单裁剪为业务字典，pending_shipment 由判定规则算出"""
        shipping_status = item.get("shipping_status")
        pay_status = item.get("pay_status")
        status_str = str(item.get("order_status_str") or "")
        return {
            "order_sn": item.get("order_sn"),
            "order_status": item.get("order_status"),
            "order_status_str": status_str,
            "shipping_status": shipping_status,
            "pay_status": pay_status,
            "goods_id": item.get("goods_id"),
            "goods_name": item.get("goods_name"),
            "spec": item.get("spec"),
            "goods_number": item.get("goods_number"),
            "order_amount": item.get("order_amount"),
            "order_time": item.get("order_time"),
            "thumb_url": item.get("thumb_url"),
            "no_need_ship": item.get("no_need_ship"),
            "tracking_number": item.get("tracking_number"),
            # 数字口径优先；平台偶尔只给中文状态（线上出现「原始 50 笔 / 待发货 0 笔」
            # 而客户明明刚下单），此时按状态文案兜底，避免待发货被漏判。
            "pending_shipment": (
                (shipping_status == SHIPPING_STATUS_UNSHIPPED and pay_status == PAY_STATUS_PAID)
                or ("待发货" in status_str)
            ),
        }

    def list_orders(self, page_num: int = 1, page_size: int = 20,
                    order_type: int = 0, after_sale_type: int = 0, sort_type: int = 10,
                    begin_time: int = None, end_time: int = None) -> Dict[str, Any]:
        """
        查询订单列表

        Args:
            page_num:       页码，从 1 开始
            page_size:      每页数量
            order_type:     订单类型（0 为全部订单 tab）
            after_sale_type: 售后类型（0 为全部）
            sort_type:      排序类型，不同 tab 用不同值（实测 10/11）
            begin_time:     下单时间范围起点（秒级时间戳），默认 30 天前
            end_time:       下单时间范围终点（秒级时间戳），默认当前

        Returns:
            {"success": bool, "total": int, "orders": [...], "error_msg": str}
        """
        try:
            now = int(_time.time())
            payload = {
                "orderType": order_type,
                "afterSaleType": after_sale_type,
                "remarkStatus": -1,
                "urgeShippingStatus": -1,
                "groupStartTime": begin_time or (now - 30 * 24 * 3600),
                "groupEndTime": end_time or now,
                "pageNumber": page_num,
                "pageSize": page_size,
                "hideRegionBlackDelayShipping": False,
                "mobile": "",
            }
            # sortType 在部分 tab 的抓包中出现、部分缺席，仅在有值时下发
            if sort_type is not None:
                payload["sortType"] = sort_type
            self._warm_anti_content()
            raw = self._post_order_list(payload)

            if not raw:
                return {"success": False, "total": 0, "orders": [], "error_msg": "请求失败"}
            if not raw.get("success"):
                msg = raw.get("errorMsg") or raw.get("error_msg") or "查询订单列表失败"
                self.logger.warning(
                    f"查询订单列表失败 page_num={page_num} "
                    f"error_code={raw.get('errorCode') or raw.get('error_code')}: {msg}"
                )
                return {"success": False, "total": 0, "orders": [], "error_msg": msg}

            result = raw.get("result") or {}
            page_items = result.get("pageItems") or []
            orders = [self._normalize_order(i) for i in page_items if isinstance(i, dict)]
            return {"success": True, "total": result.get("totalItemNum") or 0,
                    "orders": orders, "error_msg": ""}

        except Exception as e:
            self.logger.error(f"查询订单列表异常 page_num={page_num}: {str(e)}")
            return {"success": False, "total": 0, "orders": [], "error_msg": str(e)}

    def _normalize_new_order(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """裁剪合并发货页接口返回的订单字段（该接口固定只返回待发货订单）"""
        shipping_status = item.get("shipping_status")
        pay_status = item.get("pay_status")
        status_str = str(item.get("order_status_str") or "")
        return {
            "order_sn": item.get("order_sn"),
            "goods_id": item.get("goods_id"),
            "goods_name": item.get("goods_name"),
            "spec": item.get("spec"),
            "goods_number": item.get("goods_number"),
            "order_amount": item.get("order_amount"),
            "order_time": item.get("order_time"),
            "order_status": item.get("order_status"),
            "order_status_str": status_str,
            "shipping_status": shipping_status,
            "pay_status": pay_status,
            "nickname": item.get("nickname"),
            "receive_name": item.get("receive_name"),
            "pending_shipment": (
                (shipping_status == SHIPPING_STATUS_UNSHIPPED and pay_status == PAY_STATUS_PAID)
                or ("待发货" in status_str)
            ),
        }

    def list_merge_shipping_orders(self, page_no: int = 1) -> Dict[str, Any]:
        """查询待发货订单（合并发货页新接口）

        recentOrderList 对被风控的账号固定返回 40002，而这个接口仍然可用，
        因此按客户识别「有没有待发货订单」一律走这里。

        Args:
            page_no: 页码，从 1 开始

        Returns:
            {"success": bool, "total": int, "orders": [...], "error_msg": str}
        """
        try:
            raw = self.post(self.MERGE_ORDER_LIST_URL, json_data={"pageNo": page_no},
                            headers=self._build_headers())
            if not raw:
                return {"success": False, "total": 0, "orders": [], "error_msg": "请求失败"}
            if raw.get("success") is not True:
                msg = raw.get("errorMsg") or raw.get("error_msg") or "查询待发货订单失败"
                self.logger.warning(f"待发货订单查询失败 page_no={page_no}: {msg}")
                return {"success": False, "total": 0, "orders": [], "error_msg": msg}

            result = raw.get("result") or {}
            groups = result.get("mergeShippingMap") or {}
            orders = []
            for items in groups.values():
                for item in items or []:
                    if isinstance(item, dict):
                        orders.append(self._normalize_new_order(item))
            pending = [o for o in orders if o.get("pending_shipment")]
            self.logger.info(
                f"待发货订单查询完成 page_no={page_no} 原始={len(orders)} 待发货={len(pending)}"
            )
            return {"success": True, "total": result.get("totalNo") or len(pending),
                    "orders": pending, "error_msg": ""}
        except Exception as e:
            self.logger.error(f"待发货订单查询异常 page_no={page_no}: {e}")
            return {"success": False, "total": 0, "orders": [], "error_msg": str(e)}

    def list_pending_shipment_orders(self, page_num: int = 1,
                                     page_size: int = 20) -> Dict[str, Any]:
        """
        查询待发货订单（未发货且已支付）

        改用合并发货页接口：recentOrderList 对被风控账号只会返回 40002。

        Args:
            page_num:  页码（page_size 仅为兼容旧调用，接口固定返回待发货订单）

        Returns:
            {"success": bool, "total": int, "orders": [...], "scanned": int, "error_msg": str}
        """
        result = self.list_merge_shipping_orders(page_no=page_num)
        orders = result.get("orders") or []
        return {"success": result.get("success"), "total": len(orders), "orders": orders,
                "scanned": len(orders), "error_msg": result.get("error_msg") or ""}

    def get_order_by_sn(self, order_sn: str, begin_days: int = 30) -> Dict[str, Any]:
        """
        按订单号查找订单（平台无独立详情接口，靠列表分页精确匹配）

        **页大小坑**：recentOrderList 对 pageSize=100 固定回「单页数据量过大或非法」，
        而 50 实测可用。旧代码用 ORDER_PAGE_SIZE_MAX(=100) 翻页，于是「按订单号查详情」
        永远失败 → 发货拿不到 goods_id/spec → 报告出不来（线上 21:56 就是这样）。
        这里用 50 起步，平台仍嫌大就自动降到 20 再试。

        Args:
            order_sn:   订单号，如 "261028-296296138371626"
            begin_days: 回溯天数

        Returns:
            {"success": bool, "order": dict|None, "error_msg": str}
        """
        try:
            if not order_sn:
                return {"success": False, "order": None, "error_msg": "订单号不能为空"}

            now = int(_time.time())
            begin_time = now - max(1, begin_days) * 24 * 3600

            page_size = DETAIL_PAGE_SIZE
            for page_num in range(1, DETAIL_MAX_PAGES + 1):
                result = self.list_orders(
                    page_num=page_num,
                    page_size=page_size,
                    begin_time=begin_time,
                    end_time=now,
                )
                if not result.get("success"):
                    err = str(result.get("error_msg") or "")
                    if _is_page_size_invalid(err) and page_size > DETAIL_PAGE_SIZE_FALLBACK:
                        # 平台嫌单页太大：降一档再打一次
                        page_size = DETAIL_PAGE_SIZE_FALLBACK
                        self.logger.warning(
                            f"[订单查询] 平台拒绝 pageSize={DETAIL_PAGE_SIZE}（{err}），"
                            f"降到 {page_size} 重试 order_sn={order_sn}")
                        result = self.list_orders(
                            page_num=page_num, page_size=page_size,
                            begin_time=begin_time, end_time=now)
                    if not result.get("success"):
                        # recentOrderList 被风控时，用「合并发货页」接口兜底：
                        # 该接口不受同一套 40002 限制，且返回里带 goods_id / spec，
                        # 正好是发货要用的查询项目（线上就是这里拿不到 spec 才丢了报告）。
                        merge = self.list_merge_shipping_orders(page_no=1)
                        for order in (merge.get("orders") or []):
                            if order.get("order_sn") == order_sn:
                                self.logger.info(
                                    f"[订单查询] 订单列表不可用，改用合并发货页命中 "
                                    f"order_sn={order_sn} spec={order.get('spec')!r}")
                                return {"success": True, "order": order, "error_msg": ""}
                        return {"success": False, "order": None,
                                "error_msg": result.get("error_msg") or "查询订单失败"}

                orders = result.get("orders") or []
                for order in orders:
                    if order.get("order_sn") == order_sn:
                        self.logger.info(f"订单命中 order_sn={order_sn} page_num={page_num}")
                        return {"success": True, "order": order, "error_msg": ""}

                # 本页不足一页，说明已到末尾，无需继续翻页
                if len(orders) < page_size:
                    break

            self.logger.warning(f"订单未找到 order_sn={order_sn} 回溯天数={begin_days}")
            return {"success": False, "order": None,
                    "error_msg": f"未在最近 {begin_days} 天订单中找到该订单号"}

        except Exception as e:
            self.logger.error(f"按订单号查询异常 order_sn={order_sn}: {str(e)}")
            return {"success": False, "order": None, "error_msg": str(e)}

    @staticmethod
    def _ship_result(error_msg: str = "", result_code: int = None,
                     result_msg: str = "", warning_info: str = "") -> Dict[str, Any]:
        """构造发货结果字典：error_msg 为空即成功"""
        return {"success": not error_msg, "error_msg": error_msg, "result_code": result_code,
                "result_msg": result_msg, "warning_info": warning_info}

    @staticmethod
    def _extract_ship_result(raw: Dict[str, Any], order_sn: str) -> Optional[Dict[str, Any]]:
        """取出 result.orderShipResultList 中 orderSn 匹配的单笔受理结果，未返回时为 None"""
        items = (raw.get("result") or {}).get("orderShipResultList") or []
        return next(
            (i for i in items if isinstance(i, dict) and i.get("orderSn") == order_sn), None
        )

    def ship_without_logistics(self, order_sn: str, virtual_content: list = None) -> Dict[str, Any]:
        """
        无物流（虚拟商品）发货

        请求体严格照真机抓包，字段名 virtualGoodesContent 是平台原始拼写，不可"纠正"。

        响应结构（实测）分三层，必须逐层判定：
        1. 顶层 success=true 只表示请求被受理，成功时 errorCode 为 1000000（不是 0）、errorMsg 为 null；
        2. result.orderShipResultList 为目标订单的受理条目，需按 orderSn 精确匹配；
        3. 单笔成败看该条目的 shippingResultCode（成功=100），失败原因在
           shippingResultMsg / warningInfo 中。

        Args:
            order_sn:        订单号，为空时直接拒绝且不发起请求
            virtual_content: 虚拟发货内容，默认 ["5", "序列号查询反馈内容,在聊天窗口。请注意查收~"]

        Returns:
            {"success": bool, "error_msg": str, "result_code": int|None,
             "result_msg": str, "warning_info": str}
        """
        try:
            if not order_sn or not str(order_sn).strip():
                self.logger.warning("无物流发货被拒绝：订单号为空，未发起任何请求")
                return self._ship_result("订单号不能为空")

            content = list(virtual_content) if virtual_content else list(DEFAULT_VIRTUAL_CONTENT)
            payload = {
                "orderShipRequestList": [
                    {"orderSn": order_sn, "deliveryType": 0, "virtualGoodesContent": content}
                ],
                "isSingleShipment": 1,
                "overWrite": 1,
                "functionType": 6,
                "isVirtualGoods": True,
                "operateFrom": "MMS_VIRTUAL",
            }
            raw = self.post(self.SHIP_SINGLE_URL, json_data=payload, headers=self._build_headers())

            if not raw:
                self.logger.warning(f"发货失败 order_sn={order_sn}: 响应为空或请求失败")
                return self._ship_result("请求失败")

            # 第一层：顶层仅表示请求是否被受理（成功时 errorCode=1000000，不能用 errorCode==0 判定）
            if raw.get("success") is not True:
                msg = raw.get("errorMsg") or raw.get("error_msg") or "发货失败"
                self.logger.warning(f"发货失败 order_sn={order_sn}: {msg}")
                return self._ship_result(msg)

            # 第二层：必须能按 orderSn 找到该单的受理结果
            item = self._extract_ship_result(raw, order_sn)
            if item is None:
                msg = f"发货响应未返回该订单的受理结果 order_sn={order_sn}"
                self.logger.warning(msg)
                return self._ship_result(msg)

            # 第三层：单笔结果以 shippingResultCode 为准（实测成功值 100）
            code = item.get("shippingResultCode")
            result_msg = item.get("shippingResultMsg") or ""
            warning_info = item.get("warningInfo") or ""

            if code == SHIP_RESULT_CODE_SUCCESS:
                self.logger.info(
                    f"发货成功 order_sn={order_sn} shippingResultCode={code}"
                    + (f" warningInfo={warning_info}" if warning_info else "")
                )
                return self._ship_result("", code, result_msg, warning_info)

            error_msg = "；".join(p for p in (result_msg, warning_info) if p) or (
                f"发货失败（shippingResultCode={code}）"
            )
            self.logger.warning(
                f"发货失败 order_sn={order_sn} shippingResultCode={code}: {error_msg}"
            )
            return self._ship_result(error_msg, code, result_msg, warning_info)

        except Exception as e:
            self.logger.error(f"无物流发货异常 order_sn={order_sn}: {str(e)}")
            return self._ship_result(str(e))
