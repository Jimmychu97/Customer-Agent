# -*- coding: utf-8 -*-
"""拼多多 anti-content 签名：现取现用

为什么必须有这个模块：
    `mms/recentOrderList`（订单列表 / 订单详情靠它翻页）**每次请求都要带一个现算的
    anti-content 签名**。签名由页面 JS 生成，登录时存进 cookies 的那一个几分钟就失效，
    而旧代码是 `cookies.get('anti_content')` —— 登录流程根本没往 cookies 里写过这个键，
    于是发出去的永远是空签名，平台固定回 `40002 操作太过频繁`。

    这就是「订单查询老是出问题 → 订单详情拿不到 → spec 为空 → 查不到验机编码」的源头。
    修复方式：在已登录的持久化浏览器里打开订单页，把页面**自己发出的那次请求**的
    anti-content 头抄下来，缓存起来给 HTTP 直连用；失效了再抄一次。

设计要点：
    - 按账号（username）缓存，TTL 内直接复用，不打浏览器；
    - 单飞（single-flight）：同一账号并发调用只有一个去开浏览器，其余拿缓存值；
    - 负缓存：取失败后 NEGATIVE_TTL 内不再反复开浏览器（避免把机器拖死）；
    - 取失败时返回**旧值**而不是空串：旧签名成功率也比空签名高。
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional

from utils.logger_loguru import get_logger

logger = get_logger("AntiContent")

# 一个 anti-content 大约能撑十几分钟；保守取 20 分钟，过期就换新的
DEFAULT_TTL = 1200.0
# 取失败后多久内不再重试（避免每个客户消息都开一次浏览器）
NEGATIVE_TTL = 300.0
# 抓签名用的页面：订单查询页会自己发 recentOrderList，正好带签名
ORDERS_PAGE = "https://mms.pinduoduo.com/orders/list?tab=1"
# 抄头时认这些接口（订单相关优先，抓不到就抓 mms 上任何一个带签名的请求）
_PREFERRED_PATHS = ("recentOrderList", "mergeShipping", "orderList", "mms")

_store: Dict[str, Dict[str, Any]] = {}
_negative: Dict[str, float] = {}
_locks: Dict[str, threading.Lock] = {}
_guard = threading.Lock()


def _key(username: Any) -> str:
    return str(username or "").strip()


def _lock_for(key: str) -> threading.Lock:
    with _guard:
        lock = _locks.get(key)
        if lock is None:
            lock = _locks[key] = threading.Lock()
        return lock


def store(username: Any, value: str) -> None:
    """记下一份签名（登录/刷新 cookies 时也会直接灌进来，省一次浏览器）"""
    key = _key(username)
    value = (value or "").strip()
    if not key or not value:
        return
    with _guard:
        _store[key] = {"value": value, "ts": time.time()}
        _negative.pop(key, None)


def current(username: Any) -> str:
    """当前缓存的签名（可能是旧的；没有则空串）"""
    with _guard:
        return str((_store.get(_key(username)) or {}).get("value") or "")


def age(username: Any) -> float:
    """缓存签名多久之前取的（没取过返回一个极大值）"""
    with _guard:
        ts = float((_store.get(_key(username)) or {}).get("ts") or 0.0)
    return (time.time() - ts) if ts else 1e9


def is_fresh(username: Any, ttl: float = DEFAULT_TTL) -> bool:
    return bool(current(username)) and age(username) <= ttl


def invalidate(username: Any) -> None:
    """平台回了 40002：把缓存作废，下次 ensure 会重新去抓"""
    key = _key(username)
    with _guard:
        _store.pop(key, None)
        _negative.pop(key, None)


def ensure(username: Any, proxy: Optional[str] = None,
           ttl: float = DEFAULT_TTL, force: bool = False) -> str:
    """确保有一份新鲜签名（阻塞；可能开一次浏览器，约 5~15 秒）

    Args:
        username: 账号手机号（与 user_data/<username> 目录同名）
        proxy:    该账号的代理（与登录用的一致，否则 IP 与签名不匹配）
        ttl:      新鲜度阈值
        force:    无视缓存强制重取（平台刚回过 40002 时用）

    Returns:
        可用的签名；实在拿不到时返回旧值（可能为空串），绝不抛异常
    """
    key = _key(username)
    if not key:
        return ""
    if not force and is_fresh(key, ttl):
        return current(key)

    lock = _lock_for(key)
    if not lock.acquire(blocking=False):
        # 别的线程已经在抓了：别排队等，用现有值先发请求
        return current(key)
    try:
        if not force and is_fresh(key, ttl):
            return current(key)
        failed_at = _negative.get(key)
        if failed_at and time.time() - failed_at < NEGATIVE_TTL:
            return current(key)

        value = capture(key, proxy)
        if value:
            store(key, value)
            logger.info(f"[anti-content] 已取到新签名 username={key} 长度={len(value)}")
            return value

        with _guard:
            _negative[key] = time.time()
        logger.warning(
            f"[anti-content] 取签名失败 username={key}（{int(NEGATIVE_TTL)}s 内不再重试，"
            f"先用旧值；可能是 user_data 登录态失效，需要重新登录该账号）")
        return current(key)
    finally:
        lock.release()


def ensure_background(username: Any, proxy: Optional[str] = None,
                      ttl: float = DEFAULT_TTL) -> None:
    """后台补签名：立刻返回，不阻塞调用方（请求线程/事件循环都不该等浏览器）

    请求线程里只做「有就用、没有就催一下」，真正开浏览器在守护线程里跑；
    这样订单查询的第一次失败不会把整个消息处理卡住 10 秒。
    """
    key = _key(username)
    if not key or is_fresh(key, ttl):
        return
    failed_at = _negative.get(key)
    if failed_at and time.time() - failed_at < NEGATIVE_TTL:
        return

    def _run() -> None:
        try:
            ensure(key, proxy, ttl=ttl)
        except Exception as e:  # pragma: no cover - 兜底，不让线程抛
            logger.debug(f"[anti-content] 后台补签名异常 username={key}: {e}")

    threading.Thread(target=_run, name=f"anti-content-{key}", daemon=True).start()


def capture(username: str, proxy: Optional[str] = None,
            timeout: float = 25.0) -> Optional[str]:
    """打开已登录的持久化浏览器，抄下页面请求里的 anti-content

    Returns:
        签名；浏览器不可用 / 被踢到登录页 / 超时返回 None
    """
    key = _key(username)
    if not key:
        return None
    try:
        # 导入即完成 PLAYWRIGHT_BROWSERS_PATH / 临时目录的准备，且复用它的代理解析
        from Channel.pinduoduo import pdd_login as _pdd_login
        from playwright.sync_api import sync_playwright
        from utils.path_utils import get_app_dir

        user_dir = get_app_dir() / "user_data" / key
        if not user_dir.exists():
            logger.warning(f"[anti-content] 账号 {key} 未登录过（无 user_data 目录）")
            return None

        box = {"value": "", "url": ""}
        done = threading.Event()

        def _on_request(request) -> None:
            try:
                url = request.url or ""
                if "pinduoduo.com" not in url:
                    return
                headers = request.headers or {}
                value = headers.get("anti-content") or headers.get("anti_content") or ""
                if not value:
                    return
                preferred = any(p in url for p in _PREFERRED_PATHS)
                # 订单接口的签名最匹配订单查询；其它请求的也先收下兜底
                if preferred or not box["value"]:
                    box["value"] = value
                if preferred:
                    done.set()
            except Exception:
                pass

        launch_kwargs: Dict[str, Any] = dict(
            headless=True,
            args=[
                "--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-notifications",
            ],
        )
        if proxy:
            launch_kwargs["proxy"] = _pdd_login._parse_proxy_for_playwright(proxy)

        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(str(user_dir), **launch_kwargs)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.on("request", _on_request)
                try:
                    page.goto(ORDERS_PAGE, wait_until="domcontentloaded",
                              timeout=int(timeout * 1000))
                except Exception as e:
                    logger.debug(f"[anti-content] 打开订单页异常 username={key}: {e}")
                box["url"] = page.url or ""
                done.wait(timeout)
            finally:
                try:
                    context.close()
                except Exception:
                    pass

        if not box["value"]:
            if "/login" in (box["url"] or ""):
                logger.warning(
                    f"[anti-content] 账号 {key} 的浏览器登录态已失效（被跳到登录页），"
                    f"需要重新登录")
            else:
                logger.warning(f"[anti-content] 订单页没发出带签名的请求 username={key}")
        return box["value"] or None
    except Exception as e:
        logger.warning(f"[anti-content] 抓签名异常 username={key}: {e}")
        return None


__all__ = [
    "DEFAULT_TTL", "NEGATIVE_TTL", "store", "current", "age", "is_fresh",
    "invalidate", "ensure", "ensure_background", "capture",
]
