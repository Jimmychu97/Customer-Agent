"""
GSX 验机查询 API

接口文档：https://api-srv.gkdt.com
鉴权方式：参数按 ASCII 排序后 & 拼接，末尾加 &secret=xxx，整体 MD5
接口地址：https://api-srv.gkdt.com/inquiry/async

配置项在 config.json 的 gsx 节点下：
    gsx.appid   - 由 gkdt.com 个人中心分配（固定）
    gsx.secret  - 对应 AppSecret（固定）
    gsx.style   - 返回文本格式：11=中文，0=英文（固定）

调用时变量：
    code - 序列号或 IMEI（由用户消息提取）
    key  - 接口编码，取自 GoodsCommit.sku_id（按商品 goods_id+spec 匹配）
"""
import hashlib
import time as _time
from typing import Dict, Any
from utils.logger_loguru import get_logger

logger = get_logger("GsxAPI")

_API_URL = "https://api-srv.gkdt.com/inquiry/async"


def _get_cfg() -> Dict[str, str]:
    """从 config 读取 gsx 固定配置"""
    from config import config
    return {
        "appid":  config.get("gsx.appid", ""),
        "secret": config.get("gsx.secret", ""),
        "style":  config.get("gsx.style", "11"),
    }


def _build_sign(params: Dict[str, str], secret: str) -> str:
    """
    按文档规则生成签名：
    1. 参数按 key ASCII 升序排列，空值不参与
    2. & 拼接：k1=v1&k2=v2...
    3. 末尾追加 &secret=xxx
    4. 整体 MD5
    """
    sorted_str = "&".join(f"{k}={v}" for k, v in sorted(params.items()) if v)
    raw = sorted_str + f"&secret={secret}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def query_gsx(code: str, key: str) -> Dict[str, Any]:
    """
    查询 GSX 验机信息

    Args:
        code: 序列号或 IMEI（变量，由用户消息提取）
        key:  接口编码（变量，由调用方指定，如 10127=苹果机型查询）

    Returns:
        {"success": bool, "data": dict, "error_msg": str}
    """
    import requests

    cfg    = _get_cfg()
    appid  = cfg["appid"]
    secret = cfg["secret"]
    style  = cfg["style"]

    if not appid or not secret:
        logger.warning("GSX appid/secret 未配置，跳过查询")
        return {"success": False, "error_msg": "GSX appid/secret 未配置"}

    ts = int(_time.time())
    params = {
        "appid": appid,
        "code":  code,
        "key":   key,
        "style": style,
        "time":  str(ts),
    }
    params["sign"] = _build_sign(params, secret)

    try:
        resp = requests.get(_API_URL, params=params, timeout=30)
        if resp.status_code != 200:
            return {"success": False, "error_msg": f"HTTP {resp.status_code}"}

        text = resp.text.strip()
        if not text or not text.startswith(("{", "[")):
            logger.error(f"GSX接口返回非 JSON 响应 code={code} key={key}: {text[:100]!r}")
            return {"success": False, "error_msg": f"GSX接口返回异常：{text[:100]}"}

        data = resp.json()
        if data.get("code") != 200:
            msg = data.get("message", "未知错误")
            logger.warning(f"GSX查询业务失败 code={code} key={key}: {msg}")
            return {"success": False, "error_msg": msg}

        logger.info(f"GSX查询成功 code={code} key={key}")
        return {"success": True, "data": data.get("data", data)}

    except Exception as e:
        logger.error(f"GSX查询失败 code={code} key={key}: {e}")
        return {"success": False, "error_msg": str(e)}
