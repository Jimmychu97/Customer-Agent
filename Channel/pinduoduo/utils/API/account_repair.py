"""账号登录修复：会话过期时用保存的账号密码重新登录，并把 cookies 写回数据库"""
import asyncio
from typing import Optional

from utils.logger_loguru import get_logger

logger = get_logger("AccountRepair")


def _load_account(shop_id: str, user_id: str) -> Optional[dict]:
    try:
        from database.db_manager import DatabaseManager
        from database.models import Account, Shop
        from core.di_container import container

        db = container.get(DatabaseManager)
    except Exception as e:
        logger.warning(f"[登录修复] 取数据库失败: {e}")
        return None
    try:
        with db.session_scope() as session:
            shop = session.query(Shop).filter(Shop.shop_id == str(shop_id)).first()
            if not shop:
                return None
            acc = (session.query(Account)
                   .filter(Account.shop_id == shop.id, Account.user_id == str(user_id))
                   .first())
            if not acc:
                return None
            return {"username": acc.username, "password": acc.password or "",
                    "user_id": acc.user_id, "shop_id": shop.shop_id}
    except Exception as e:
        logger.warning(f"[登录修复] 读账号失败: {e}")
        return None


def _save_cookies(shop_id: str, user_id: str, cookies: str) -> None:
    if not cookies:
        return
    try:
        from database.db_manager import DatabaseManager
        from core.di_container import container

        db = container.get(DatabaseManager)
        db.update_account_cookies("pinduoduo", str(shop_id), str(user_id), cookies)
        logger.info(f"[登录修复] 新 cookies 已写回数据库 shop={shop_id} user_id={user_id}")
    except Exception as e:
        logger.warning(f"[登录修复] 保存 cookies 失败: {e}")


async def relogin_account(shop_id: str, user_id: str, headless: bool = True) -> bool:
    """先刷新会话，不行再用账号密码登录；成功返回 True"""
    acc = _load_account(shop_id, user_id)
    if not acc:
        logger.warning(f"[登录修复] 找不到账号 shop={shop_id} user_id={user_id}")
        return False

    from Channel.pinduoduo.pdd_login import login_pdd, refresh_pdd_cookies

    username = acc["username"]
    try:
        refreshed = await refresh_pdd_cookies(username)
    except Exception as e:
        logger.warning(f"[登录修复] 刷新会话异常 {username}: {e}")
        refreshed = False
    if isinstance(refreshed, dict) and refreshed.get("cookies"):
        _save_cookies(shop_id, user_id, refreshed["cookies"])
        logger.info(f"[登录修复] 会话仍有效，cookies 已刷新 {username}")
        return True

    try:
        result = await login_pdd(username, acc["password"], headless=headless)
    except Exception as e:
        logger.warning(f"[登录修复] 账号密码登录异常 {username}: {e}")
        result = False
    if not result:
        logger.warning(
            f"[登录修复] 自动登录失败 {username}（多半需要短信/验证码），请到程序界面手动登录")
        return False
    _save_cookies(result.get("shop_id") or shop_id,
                  result.get("user_id") or user_id,
                  result.get("cookies") or "")
    logger.info(f"[登录修复] 账号密码重新登录成功 {username}")
    return True


def relogin_account_sync(shop_id: str, user_id: str, headless: bool = True) -> bool:
    """同步入口（供线程池调用）"""
    try:
        return asyncio.run(relogin_account(shop_id, user_id, headless=headless))
    except Exception as e:
        logger.warning(f"[登录修复] 同步登录失败: {e}")
        return False
