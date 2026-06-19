"""
GSX 验机查询 API

接口文档：https://api-srv.gkdt.com
鉴权方式：MD5(appid + code + key + style + time + secret)
配置项在 config.json 的 gsx 节点下：
    gsx.appid   - 由 gkdt.com 个人中心分配
    gsx.secret  - 对应 AppSecret
    gsx.key     - 接口编码，默认 10101
    gsx.style   - 返回格式，11=中文，0=英文
"""
import hashlib
import time as _time
from typing import Dict, Any
from utils.logger_loguru import get_logger

logger = get_logger("GsxAPI")

_BASE_URL = "https://api-srv.gkdt.com"


def _get_cfg() -> Dict[str, str]:
    """从 config 读取 gsx 配置"""
    from config import config
    return {
        "appid":  config.get("gsx.appid", ""),
        "secret": config.get("gsx.secret", ""),
        "key":    config.get("gsx.key", "10101"),
        "style":  config.get("gsx.style", "11"),
    }


def _build_sign(appid: str, code: str, key: str, style: str, ts: int, secret: str) -> str:
    """生成 MD5 签名：MD5(appid + code + key + style + time + secret)"""
    raw = f"{appid}{code}{key}{style}{ts}{secret}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def query_gsx(code: str, key: str = None) -> Dict[str, Any]:
    """
    查询 GSX 验机信息

    Args:
        code: 序列号或 IMEI
        key:  接口编码，不传则读配置，配置也为空时默认 10101

    Returns:
        {"success": bool, "data": dict, "error_msg": str}
    """
    import requests

    cfg = _get_cfg()
    appid  = cfg["appid"]
    secret = cfg["secret"]
    style  = cfg["style"]
    use_key = key or cfg["key"] or "10101"

    if not appid or not secret:
        logger.warning("GSX appid/secret 未配置，跳过查询")
        return {"success": False, "error_msg": "GSX appid/secret 未配置"}

    ts   = int(_time.time())
    sign = _build_sign(appid, code, use_key, style, ts, secret)

    params = {
        "appid": appid,
        "code":  code,
        "key":   use_key,
        "style": style,
        "time":  ts,
        "sign":  sign,
    }

    try:
        resp = requests.get(_BASE_URL, params=params, timeout=15)
        if resp.status_code != 200:
            return {"success": False, "error_msg": f"HTTP {resp.status_code}"}
        data = resp.json()
        logger.info(f"GSX查询完成 code={code} key={use_key}")
        return {"success": True, "data": data}
    except Exception as e:
        logger.opt(exception=True).error(f"GSX查询失败 code={code}: {e}")
        return {"success": False, "error_msg": str(e)}
