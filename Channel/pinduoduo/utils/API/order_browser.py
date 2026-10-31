"""用已登录的浏览器会话查商家后台待发货订单（HTTP 接口被风控时的兜底）

为什么需要它：
拼多多 `mms/recentOrderList` 必须带**每次请求现算的 anti-content 签名**，登录时存下来的
那一个早已失效，HTTP 直连会一直返回 `40002 操作太过频繁`，于是订单永远查不到。
这里换成「打开已登录的浏览器 → 让页面自己去调接口 → 抓页面收到的响应」，
签名由浏览器页面自己生成，必然有效。
"""
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils.logger_loguru import get_logger

logger = get_logger("OrderBrowser")

TARGET_HOST = "mms.pinduoduo.com"
ORDER_LIST_PAGE = "https://mms.pinduoduo.com/orders/list?tab=0"  # tab=0：待发货
ORDER_LIST_API = "recentOrderList"
# 与 Channel/pinduoduo/pdd_login.py 的启动参数保持一致
LAUNCH_ARGS = ["--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage",
               "--disable-blink-features=AutomationControlled",
               "--disable-notifications", "--disable-web-security",
               "--disable-features=VizDisplayCompositor"]
SHIPPING_STATUS_UNSHIPPED = 0
PAY_STATUS_PAID = 2
LAST_FAILURE = ""   # ""=正常；"login_expired"=登录态过期；其它=异常信息


def _prepare_env() -> None:
    """必须在 import playwright 之前设置好浏览器目录与临时目录（与登录脚本一致）"""
    try:
        from utils.path_utils import get_app_dir

        app_dir = get_app_dir()
        browsers = app_dir / ".browsers"
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = (
            str(browsers) if browsers.exists()
            else os.path.join(os.getenv("LOCALAPPDATA", ""), "ms-playwright"))
        # 系统临时目录不可写时（受限环境），把 TMP/TEMP 指到应用自己的 temp 下
        tmp_dir = app_dir / "temp" / "playwright"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        os.environ["TMP"] = str(tmp_dir)
        os.environ["TEMP"] = str(tmp_dir)
    except Exception as e:
        logger.debug(f"[订单查询] 准备浏览器目录失败: {e}")


def _user_dir(account: str) -> Path:
    """把「平台 user_id / 登录名」映射到登录时用的 user_data 目录

    数据库里 accounts.user_id 是平台的 userId（如 110997513），
    而登录目录用的是 accounts.username（如 13431560467），必须做这层映射，
    否则会误报"未登录"。
    """
    from utils.path_utils import get_app_dir

    base = get_app_dir() / "user_data"
    names = [str(account)]
    try:
        from database import db_manager
        from database.models import Account

        with db_manager.session_scope() as session:
            row = (session.query(Account).filter(Account.user_id == str(account)).first()
                   or session.query(Account).filter(Account.username == str(account)).first())
            if row and row.username:
                names.insert(0, str(row.username))
    except Exception as e:
        logger.debug(f"[订单查询] 解析账号登录目录失败 account={account}: {e}")

    for name in names:
        candidate = base / name
        if candidate.exists():
            return candidate

    # 只有一个登录目录时直接用它（单账号店铺）
    try:
        dirs = [d for d in base.iterdir() if d.is_dir()]
        if len(dirs) == 1:
            logger.info(f"[订单查询] 浏览器兜底：账号 {account} 无目录，改用唯一登录目录 {dirs[0].name}")
            return dirs[0]
    except Exception:
        pass
    return base / names[0]


def _normalize(item: Dict[str, Any]) -> Dict[str, Any]:
    shipping_status = item.get("shipping_status")
    pay_status = item.get("pay_status")
    return {
        "order_sn": item.get("order_sn"),
        "order_status": item.get("order_status"),
        "order_status_str": item.get("order_status_str"),
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
        "pending_shipment": (
            shipping_status == SHIPPING_STATUS_UNSHIPPED and pay_status == PAY_STATUS_PAID
        ),
    }


async def _fetch_async(account: str, timeout: float) -> List[Dict[str, Any]]:
    global LAST_FAILURE
    LAST_FAILURE = ""
    _prepare_env()
    from playwright.async_api import async_playwright

    user_data_dir = _user_dir(account)
    if not user_data_dir.exists():
        logger.warning(f"[订单查询] 浏览器兜底：账号 {account} 未登录（无用户目录）")
        return []

    captured: List[Dict[str, Any]] = []
    done = asyncio.Event()

    async def main() -> List[Dict[str, Any]]:
        async with async_playwright() as p:
            context = await p.chromium.launch_persistent_context(
                str(user_data_dir), headless=True, args=LAUNCH_ARGS,
            )
            try:
                page = context.pages[0] if context.pages else await context.new_page()

                async def on_response(response):
                    if ORDER_LIST_API not in response.url:
                        return
                    try:
                        body = await response.json()
                    except Exception:
                        return
                    items = ((body.get("result") or {}).get("pageItems")) or []
                    if items:
                        captured.append({"items": items})
                        done.set()

                page.on("response", on_response)
                await page.goto(ORDER_LIST_PAGE, wait_until="domcontentloaded",
                                timeout=int(timeout * 1000))
                current_url = getattr(page, "url", "") or ""
                if "login" in current_url or "mobile_login" in current_url:
                    LAST_FAILURE = "login_expired"
                    logger.warning(
                        "[订单查询] 浏览器兜底：登录态已过期（被跳转到登录页），"
                        "请在程序里重新登录该账号")
                    return []
                try:
                    await asyncio.wait_for(done.wait(), timeout=timeout)
                except asyncio.TimeoutError:
                    logger.info(
                        f"[订单查询] 浏览器兜底：{int(timeout)}s 内没抓到订单列表响应"
                        f"（当前页面 {current_url[:60]}）")
            finally:
                await context.close()
            return []

    try:
        await main()
    except Exception as e:
        logger.warning(f"[订单查询] 浏览器兜底失败: {e}")
        return []

    orders: List[Dict[str, Any]] = []
    for group in captured:
        for item in group["items"]:
            if isinstance(item, dict):
                orders.append(_normalize(item))
    pending = [o for o in orders if o.get("pending_shipment")]
    logger.info(
        f"[订单查询] 浏览器兜底：抓到订单 {len(orders)} 笔，其中待发货 {len(pending)} 笔")
    return pending


def fetch_pending_orders_via_browser(
    account: str, timeout: float = 20.0
) -> List[Dict[str, Any]]:
    """同步入口（供线程池调用）：返回该店铺当前的待发货订单，失败返回空列表"""
    if not account:
        return []
    try:
        return asyncio.run(_fetch_async(str(account), timeout))
    except RuntimeError as e:
        # 已经在事件循环里跑（不该发生，调用方都在线程池）
        logger.warning(f"[订单查询] 浏览器兜底无法在事件循环内执行: {e}")
        return []
    except Exception as e:
        logger.warning(f"[订单查询] 浏览器兜底异常: {e}")
        return []
