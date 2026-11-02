"""账号会话心跳：定时验活、失效自动重登、定期刷新 cookies 保活

为什么需要：
拼多多的登录态会在几天内悄悄过期（HTTP 接口报 `40002/会话已过期`，浏览器直接跳登录页），
过期后订单查询三条通道全废，客服只能回"查不到"。心跳把"发现过期→自动重登"做成常态，
不用等人发现。

可配置（环境变量）：
    ORDER_HEARTBEAT=0            关闭心跳（测试用）
    ORDER_HEARTBEAT_INTERVAL=180 心跳间隔（秒）
    ORDER_COOKIE_REFRESH_HOURS=6 委托浏览器刷新 cookies 的周期（小时，保活）
    ORDER_RELOGIN_COOLDOWN=1800  同一账号自动重登冷却（秒）
"""
import os
import threading
import time
from typing import Any, Dict, List, Optional

from utils.logger_loguru import get_logger

logger = get_logger("SessionHealth")

_ENABLED = os.environ.get("ORDER_HEARTBEAT", "1") != "0"
_INTERVAL = float(os.environ.get("ORDER_HEARTBEAT_INTERVAL", "180"))
_REFRESH_INTERVAL = float(os.environ.get("ORDER_COOKIE_REFRESH_HOURS", "6")) * 3600.0
_RELOGIN_COOLDOWN = float(os.environ.get("ORDER_RELOGIN_COOLDOWN", "1800"))
# 刷新失败后的退避：登录态真失效时每 3 分钟硬刷一次只会把风控越打越死
_REFRESH_FAIL_BACKOFF = float(os.environ.get("ORDER_REFRESH_FAIL_BACKOFF", "600"))

_lock = threading.Lock()
_thread: Optional[threading.Thread] = None
_stop = threading.Event()
_state: Dict[str, Dict[str, Any]] = {}
_checks = {"checks": 0, "relogins": 0, "refreshes": 0, "failures": 0}


def status() -> Dict[str, Any]:
    """当前心跳状态（排查用）"""
    with _lock:
        return {"enabled": _ENABLED, "running": bool(_thread and _thread.is_alive()),
                "interval": _INTERVAL, "counters": dict(_checks),
                "accounts": {k: dict(v) for k, v in _state.items()}}


def _load_accounts() -> List[Dict[str, Any]]:
    """从数据库取所有拼多多账号"""
    try:
        from database import db_manager
        from database.models import Account, Shop
    except Exception as e:
        logger.debug(f"[心跳] 导入数据库失败: {e}")
        return []
    try:
        with db_manager.session_scope() as session:
            shops = {s.id: s.shop_id for s in session.query(Shop).all()}
            rows = session.query(Account).all()
            return [{"shop_id": shops.get(r.shop_id, ""), "user_id": r.user_id,
                     "username": r.username, "cookies": r.cookies or ""} for r in rows]
    except Exception as e:
        logger.debug(f"[心跳] 读取账号失败: {e}")
        return []


def _check_valid(account: Dict[str, Any]) -> bool:
    """轻量验活（chats/getToken）：网络异常时保守视为有效"""
    try:
        import json

        from Channel.pinduoduo.cookie_utils import check_cookies_valid

        try:
            cookies = json.loads(account.get("cookies") or "{}")
        except Exception:
            cookies = {}
        if not cookies:
            return False
        return check_cookies_valid("pinduoduo", account["shop_id"], account["user_id"], cookies)
    except Exception as e:
        logger.debug(f"[心跳] 验活异常 {account.get('username')}: {e}")
        return True


def _relogin(account: Dict[str, Any]) -> bool:
    try:
        from Channel.pinduoduo.utils.API.account_repair import relogin_account_sync

        return relogin_account_sync(account["shop_id"], account["user_id"])
    except Exception as e:
        logger.warning(f"[心跳] 重新登录异常 {account.get('username')}: {e}")
        return False


def _refresh(account: Dict[str, Any]) -> bool:
    """用已验证的浏览器会话刷新 cookies（保活，不用密码）"""
    try:
        import asyncio

        from Channel.pinduoduo.pdd_login import refresh_pdd_cookies
        from Channel.pinduoduo.utils.API.account_repair import _save_cookies

        result = asyncio.run(refresh_pdd_cookies(account["username"]))
        if isinstance(result, dict) and result.get("cookies"):
            _save_cookies(account["shop_id"], account["user_id"], result["cookies"])
            return True
    except Exception as e:
        logger.debug(f"[心跳] 刷新 cookies 异常 {account.get('username')}: {e}")
    return False


def check_once(now: Optional[float] = None) -> Dict[str, Any]:
    """跑一轮心跳：验活 → 失效重登 → 到期刷新 cookies"""
    if not _ENABLED:
        return status()
    now = now if now is not None else time.time()
    for account in _load_accounts():
        shop_id, user_id = str(account.get("shop_id") or ""), str(account.get("user_id") or "")
        if not shop_id or not user_id:
            continue
        key = f"{shop_id}:{user_id}"
        with _lock:
            entry = _state.setdefault(key, {"valid": None, "last_check": 0.0,
                                            "last_relogin": 0.0, "last_refresh": 0.0})
            _checks["checks"] += 1
            last_relogin = entry["last_relogin"]
            last_refresh = entry["last_refresh"]
            prev_valid = entry["valid"]

        valid = _check_valid(account)
        if not valid:
            if last_relogin <= 0 or now - last_relogin >= _RELOGIN_COOLDOWN:
                logger.warning(f"[心跳] 账号 {account.get('username')} 登录已失效，自动重新登录…")
                ok = _relogin(account)
                with _lock:
                    if ok:
                        _checks["relogins"] += 1
                        entry["last_relogin"] = now
                    else:
                        _checks["failures"] += 1
                valid = ok and _check_valid(account)
                logger.info(f"[心跳] 账号 {account.get('username')} 自动重登{'成功' if valid else '失败'}")
            else:
                logger.debug(f"[心跳] 账号 {account.get('username')} 失效，但还在重登冷却期")
        elif last_refresh <= 0 or now - last_refresh >= _REFRESH_INTERVAL:
            if _refresh(account):
                with _lock:
                    _checks["refreshes"] += 1
                    entry["last_refresh"] = now
                logger.info(f"[心跳] 账号 {account.get('username')} cookies 已刷新保活")
            else:
                # 浏览器 profile 被踢/过期时，光刷 cookies 是刷不活的：
                # 直接升级成「密码重新登录」，否则订单查询三条通道会一直全废。
                # 失败也要退避，别每 3 分钟硬刷一轮（线上 16:27~17:57 就是这么刷的）。
                with _lock:
                    entry["last_refresh"] = now - _REFRESH_INTERVAL + _REFRESH_FAIL_BACKOFF
                    can_relogin = last_relogin <= 0 or now - last_relogin >= _RELOGIN_COOLDOWN
                logger.warning(
                    f"[心跳] 账号 {account.get('username')} cookies 刷新失败"
                    f"（登录态大概率已失效），"
                    f"{'改用密码重新登录…' if can_relogin else '重登冷却中，稍后再试'}")
                if can_relogin:
                    ok = _relogin(account)
                    with _lock:
                        if ok:
                            _checks["relogins"] += 1
                            entry["last_relogin"] = now
                            entry["last_refresh"] = now
                        else:
                            _checks["failures"] += 1
                    logger.info(
                        f"[心跳] 账号 {account.get('username')} 密码重登"
                        f"{'成功，cookies 已更新' if ok else '失败（需人工在界面重新登录）'}")
                    if ok:
                        valid = _check_valid(account)

        with _lock:
            entry["valid"] = bool(valid)
            entry["last_check"] = now
            if prev_valid is not None and prev_valid != bool(valid):
                logger.info(f"[心跳] 账号 {account.get('username')} 状态变化: "
                            f"{'有效' if prev_valid else '失效'} → {'有效' if valid else '失效'}")
    return status()


def _loop() -> None:
    logger.info(f"[心跳] 已启动，间隔 {int(_INTERVAL)}s")
    while not _stop.wait(_INTERVAL):
        try:
            check_once()
        except Exception as e:
            logger.warning(f"[心跳] 本轮异常: {e}")


def start() -> bool:
    """启动心跳线程（幂等）"""
    global _thread
    if not _ENABLED:
        return False
    with _lock:
        if _thread and _thread.is_alive():
            return False
        _stop.clear()
        _thread = threading.Thread(target=_loop, name="session-heartbeat", daemon=True)
        _thread.start()
    return True


def stop() -> None:
    _stop.set()


def ensure_started() -> bool:
    """惰性启动：第一次查订单时顺手把心跳拉起来"""
    return start()
