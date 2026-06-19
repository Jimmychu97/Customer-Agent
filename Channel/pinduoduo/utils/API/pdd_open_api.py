"""
拼多多开放平台 API 封装

实现签名算法 + 官方核销接口：
- pdd.voucher.ota.card.prepare.verification  验券
- pdd.voucher.ota.card.verification          核销
"""
import hashlib
import time
import requests
from typing import Any, Dict, Optional
from utils.logger_loguru import get_logger

logger = get_logger("PddOpenAPI")

PDD_GATEWAY = "https://gw-api.pinduoduo.com/api/router"


def _sign(params: Dict[str, Any], client_secret: str) -> str:
    """
    拼多多开放平台签名算法（MD5）

    1. 所有参数按 key ASCII 升序排列
    2. key+value 无缝拼接
    3. 头尾拼接 client_secret
    4. MD5 后转大写
    """
    sorted_keys = sorted(params.keys())
    sign_str = client_secret
    for k in sorted_keys:
        sign_str += str(k) + str(params[k])
    sign_str += client_secret
    return hashlib.md5(sign_str.encode("utf-8")).hexdigest().upper()


def _call(type_: str, biz_params: Dict[str, Any],
          client_id: str, client_secret: str, access_token: str) -> Dict[str, Any]:
    """
    发起一次开放平台 API 请求

    Args:
        type_: 接口名，如 pdd.voucher.ota.card.prepare.verification
        biz_params: 业务参数字典
        client_id: 应用 client_id
        client_secret: 应用 client_secret
        access_token: 商家授权 access_token

    Returns:
        完整响应 dict，失败时返回 {"error_response": {...}}
    """
    params: Dict[str, Any] = {
        "type": type_,
        "client_id": client_id,
        "timestamp": str(int(time.time())),
        "access_token": access_token,
        "data_type": "JSON",
    }
    params.update(biz_params)
    params["sign"] = _sign(params, client_secret)

    try:
        resp = requests.post(
            PDD_GATEWAY,
            json=params,
            headers={"content-type": "application/json"},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"PDD 开放平台请求异常 type={type_}: {e}")
        return {"error_response": {"error_msg": str(e), "error_code": -1}}


class PddVoucherAPI:
    """
    拼多多官方卡券核销 API

    使用前需在 config.json 配置：
        pdd_open.client_id
        pdd_open.client_secret
        pdd_open.access_token
    """

    def __init__(self):
        from config import config
        pdd = config.get_model().pdd_open
        self.client_id = pdd.client_id
        self.client_secret = pdd.client_secret
        self.access_token = pdd.access_token

    def _is_configured(self) -> bool:
        return bool(self.client_id and self.client_secret and self.access_token)

    def prepare_verification(self, card_no: str, store_id: Optional[int] = None) -> Dict[str, Any]:
        """
        验券接口 pdd.voucher.ota.card.prepare.verification

        Args:
            card_no: 卡密（用户提供的券码）
            store_id: 门店ID（可选）

        Returns:
            {
                "success": bool,
                "status": int,        # 1-未核销 2-已核销 3-已过期 4-已销毁
                "status_tips": str,
                "goods_name": str,
                "order_sn": str,
                "remain_times": int,
                "error_msg": str      # 失败时
            }
        """
        if not self._is_configured():
            return {"success": False, "error_msg": "未配置拼多多开放平台凭证（client_id/client_secret/access_token）"}

        biz: Dict[str, Any] = {"request": {"card_no": card_no}}
        if store_id:
            biz["request"]["store_id"] = store_id

        raw = _call(
            "pdd.voucher.ota.card.prepare.verification",
            biz, self.client_id, self.client_secret, self.access_token,
        )

        # 公共错误
        if "error_response" in raw:
            err = raw["error_response"]
            logger.error(f"验券失败: {err}")
            return {"success": False, "error_msg": err.get("error_msg", "未知错误")}

        resp = raw.get("response", {})
        if not resp.get("success"):
            msg = resp.get("errorMsg", "验券接口返回失败")
            logger.warning(f"验券不通过 card_no={card_no}: {msg}")
            return {"success": False, "error_msg": msg}

        result = resp.get("result", {})
        card_vo = result.get("card_vo", {})
        order_goods = result.get("order_goods_vo", {})
        order_vo = result.get("order_vo", {})

        return {
            "success": True,
            "status": card_vo.get("status"),          # 1=未核销
            "status_tips": card_vo.get("status_tips"),
            "remain_times": card_vo.get("remain_times"),
            "goods_name": order_goods.get("goods_name"),
            "order_sn": order_vo.get("order_sn"),
            "order_amount_fen": order_vo.get("order_amount_fen"),
        }

    def verification(self, card_no: str, store_id: Optional[int] = None) -> Dict[str, Any]:
        """
        核销接口 pdd.voucher.ota.card.verification

        Args:
            card_no: 卡密
            store_id: 门店ID（可选）

        Returns:
            {"success": bool, "error_msg": str}
        """
        if not self._is_configured():
            return {"success": False, "error_msg": "未配置拼多多开放平台凭证"}

        biz: Dict[str, Any] = {"request": {"card_no": card_no}}
        if store_id:
            biz["request"]["store_id"] = store_id

        raw = _call(
            "pdd.voucher.ota.card.verification",
            biz, self.client_id, self.client_secret, self.access_token,
        )

        if "error_response" in raw:
            err = raw["error_response"]
            logger.error(f"核销失败: {err}")
            return {"success": False, "error_msg": err.get("error_msg", "未知错误")}

        resp = raw.get("response", {})
        if resp.get("success"):
            logger.info(f"核销成功 card_no={card_no}")
            return {"success": True}
        else:
            msg = resp.get("errorMsg", "核销接口返回失败")
            logger.warning(f"核销不通过 card_no={card_no}: {msg}")
            return {"success": False, "error_msg": msg}
