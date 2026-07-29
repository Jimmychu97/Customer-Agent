"""
gsxunlocking.com 验机查询 API

接口地址：https://www.gsxunlocking.com/api/uapi
鉴权方式：URL 参数传 key
配置项在 config.json 的 gsxunlocking 节点下：
    gsxunlocking.key - API 密钥

调用时变量：
    imei - 用户提供的 IMEI 或序列号
    srv  - 服务ID，取自 GoodsCommit.zhenxin_server_id（按商品 goods_id+spec 匹配）

响应格式：
    {"code": 0, "result": "验机报告文本"}  code=0 成功，code=1 失败
"""
import threading
import requests
from typing import Dict, Any
from utils.logger_loguru import get_logger

logger = get_logger("GsxUnlockingAPI")

_API_URL = "https://www.gsxunlocking.com/api/uapi"

# 并发限制：最多 20 个并发请求
_semaphore = threading.Semaphore(20)


def _get_cfg() -> Dict[str, str]:
    try:
        # 尝试从项目根目录导入 config
        import sys
        import os
        # 添加项目根目录到 sys.path
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
        if project_root not in sys.path:
            sys.path.insert(0, project_root)
        
        from config import config
        return {"key": config.get("gsxunlocking.key", "")}
    except ImportError:
        # 如果导入失败，尝试直接读取 config.json
        import json
        import os
        config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))), "config.json")
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    config_data = json.load(f)
                return {"key": config_data.get("gsxunlocking", {}).get("key", "")}
            except Exception:
                return {"key": ""}
        return {"key": ""}


def query_gsxunlocking(imei: str, srv: str) -> Dict[str, Any]:
    """
    查询 gsxunlocking.com 验机信息

    Args:
        imei: 用户提供的 IMEI 或序列号
        srv:  服务ID（取自 GoodsCommit.zhenxin_server_id）

    Returns:
        {"success": bool, "result": str, "error_msg": str}
        result 为验机报告文本字符串
    """
    cfg = _get_cfg()
    key = cfg["key"]

    if not key:
        logger.warning("GsxUnlocking key 未配置，跳过查询")
        return {"success": False, "error_msg": "GsxUnlocking key 未配置"}

    if not srv:
        logger.warning("GsxUnlocking srv 未传入，跳过查询")
        return {"success": False, "error_msg": "GsxUnlocking srv 未传入"}

    params = {
        "key": key,
        "srv": srv,
        "imei": imei,
    }

    acquired = _semaphore.acquire(timeout=60)
    if not acquired:
        logger.error("GsxUnlocking 并发限制：等待超时")
        return {"success": False, "error_msg": "服务繁忙，请稍后重试"}

    try:
        resp = requests.get(_API_URL, params=params, timeout=60)
        if resp.status_code != 200:
            return {"success": False, "error_msg": f"HTTP {resp.status_code}"}

        text = resp.text.strip()
        if not text:
            logger.error(f"GsxUnlocking 返回空响应 imei={imei} srv={srv}")
            return {"success": False, "error_msg": "接口返回空响应"}

        # JSON 解析（code=0 成功，code!=0 失败）
        if text.startswith("{") or text.startswith("["):
            try:
                data = resp.json()
                item = data[0] if isinstance(data, list) else data
                code = item.get("code")
                if code != 0:
                    msg = item.get("result") or item.get("message") or item.get("msg") or "未知错误"
                    logger.warning(f"GsxUnlocking 查询失败 imei={imei} srv={srv}: {msg}")
                    return {"success": False, "error_msg": str(msg)}
                result_text = item.get("result", text)
                logger.info(f"GsxUnlocking 查询成功 imei={imei} srv={srv}")
                return {"success": True, "result": result_text}
            except (ValueError, KeyError) as e:
                logger.warning(f"GsxUnlocking JSON 解析失败 imei={imei} srv={srv}: {e}")
                return {"success": False, "error_msg": "响应解析失败"}

        # 非 JSON 响应，无法判断结果，视为失败
        logger.warning(f"GsxUnlocking 返回非 JSON 响应 imei={imei} srv={srv}: {text[:100]!r}")
        return {"success": False, "error_msg": f"非预期响应格式: {text[:100]}"}

    except Exception as e:
        logger.error(f"GsxUnlocking 查询异常 imei={imei} srv={srv}: {e}")
        return {"success": False, "error_msg": str(e)}
    finally:
        _semaphore.release()
