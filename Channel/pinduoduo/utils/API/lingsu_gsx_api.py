"""
灵速验机查询 API

接口地址：https://unlock.imeicheck.cn/api/submit
鉴权方式：URL 参数传 key（apikey），无需签名
配置项在 config.json 的 lingsu_api 节点下：
    lingsu_api.apikey - 由平台分配的 API 密钥（固定）

调用时变量：
    imei   - 用户提供的 IMEI 或序列号
    api_id - 服务ID，取自 GoodsCommit.zhenxin_server_id（按商品 goods_id+spec 匹配）

响应格式：纯文本验机报告
"""
import requests
from typing import Dict, Any
from utils.logger_loguru import get_logger

logger = get_logger("LingsuGsxAPI")

_API_URL = "https://unlock.imeicheck.cn/api/submit"


def _get_cfg() -> Dict[str, str]:
    """从 config 读取 lingsu_api 固定配置"""
    from config import config
    return {
        "apikey": config.get("lingsu_api.apikey", ""),
    }


def query_lingsu(imei: str, api_id: str) -> Dict[str, Any]:
    """
    查询灵速验机信息

    Args:
        imei:   用户提供的 IMEI 或序列号
        api_id: 服务ID（变量，由调用方指定，取自 GoodsCommit.zhenxin_server_id）

    Returns:
        {"success": bool, "result": str, "error_msg": str}
        result 为纯文本验机报告
    """
    cfg    = _get_cfg()
    apikey = cfg["apikey"]

    if not apikey:
        logger.warning("LingsuAPI apikey 未配置，跳过查询")
        return {"success": False, "error_msg": "LingsuAPI apikey 未配置"}

    if not api_id:
        logger.warning("LingsuAPI api_id 未传入，跳过查询")
        return {"success": False, "error_msg": "LingsuAPI api_id 未传入"}

    params = {
        "api_id": api_id,
        "key":    apikey,
        "format": "text",
        "imei":   imei,
    }

    try:
        resp = requests.get(_API_URL, params=params, timeout=30)
        if resp.status_code != 200:
            return {"success": False, "error_msg": f"HTTP {resp.status_code}"}

        result_text = resp.text.strip()
        if not result_text:
            logger.error(f"灵速接口返回空响应 imei={imei} api_id={api_id}")
            return {"success": False, "error_msg": "接口返回空响应"}

        logger.info(f"灵速查询成功 imei={imei} api_id={api_id}")
        return {"success": True, "result": result_text}

    except Exception as e:
        logger.error(f"灵速查询失败 imei={imei} api_id={api_id}: {e}")
        return {"success": False, "error_msg": str(e)}
