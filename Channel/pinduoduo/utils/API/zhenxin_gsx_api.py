"""
真心验机查询 API

接口文档：http://www.iunlock8.com:9501
鉴权方式：URL 参数直接传 apikey，无需签名
接口地址：http://www.iunlock8.com:9501/api/service

配置项在 config.json 的 zhenxin_api 节点下：
    zhenxin_api.apikey - 由平台分配的 API 密钥（固定）

调用时变量：
    imei - 用户提供的 IMEI 或序列号
    sid  - 服务ID，取自 GoodsCommit.server_id（按商品 goods_id+spec 匹配）

响应格式示例：
    [{"code": 0, "result": "型号: iPhone 17 Pro...<br>网络锁: ..."}]
    code=0 表示成功，result 为 HTML 格式的验机报告文本
"""
import re
import requests
from typing import Dict, Any
from utils.logger_loguru import get_logger

logger = get_logger("ZhenxinGsxAPI")

_API_URL = "http://www.iunlock8.com:9501/api/service"


def _get_cfg() -> Dict[str, str]:
    """从 config 读取 zhenxin_api 固定配置"""
    from config import config
    return {
        "apikey": config.get("zhenxin_api.apikey", ""),
    }


def _strip_html(text: str) -> str:
    """将 HTML 格式的 result 转为纯文本，<br> 替换为换行"""
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    return text.strip()


def query_zhenxin(imei: str, sid: str) -> Dict[str, Any]:
    """
    查询真心验机信息

    Args:
        imei: 用户提供的 IMEI 或序列号
        sid:  服务ID（变量，由调用方指定，取自 GoodsCommit.server_id）

    Returns:
        {"success": bool, "result": str, "raw": dict, "error_msg": str}
        result 为去除 HTML 标签后的纯文本验机报告
    """
    cfg    = _get_cfg()
    apikey = cfg["apikey"]

    if not apikey:
        logger.warning("ZhenxinAPI apikey 未配置，跳过查询")
        return {"success": False, "error_msg": "ZhenxinAPI apikey 未配置"}

    if not sid:
        logger.warning("ZhenxinAPI sid 未传入，跳过查询")
        return {"success": False, "error_msg": "ZhenxinAPI sid 未传入"}

    params = {
        "sid":    sid,
        "apikey": apikey,
        "imei":   imei,
    }

    try:
        resp = requests.get(_API_URL, params=params, timeout=30)
        if resp.status_code != 200:
            return {"success": False, "error_msg": f"HTTP {resp.status_code}"}

        text = resp.text.strip()
        if not text or not text.startswith(("{", "[")):
            logger.error(f"真心接口返回非 JSON 响应 imei={imei} sid={sid}: {text[:100]!r}")
            return {"success": False, "error_msg": f"接口返回异常：{text[:100]}"}

        data = resp.json()

        # 响应为数组，取第一个元素
        item = data[0] if isinstance(data, list) else data

        if item.get("code") != 0:
            msg = item.get("result") or item.get("msg", "未知错误")
            logger.warning(f"真心查询业务失败 imei={imei} sid={sid}: {msg}")
            return {"success": False, "error_msg": msg}

        result_text = _strip_html(item.get("result", ""))
        logger.info(f"真心查询成功 imei={imei} sid={sid}")
        return {"success": True, "result": result_text, "raw": item}

    except Exception as e:
        logger.error(f"真心查询失败 imei={imei} sid={sid}: {e}")
        return {"success": False, "error_msg": str(e)}
