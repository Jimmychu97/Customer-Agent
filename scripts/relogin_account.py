# -*- coding: utf-8 -*-
"""账号登录修复：先尝试用已保存的浏览器会话刷新 cookies，不行再用账号密码重新登录

用法：
    python scripts/relogin_account.py --account 13431560467
    python scripts/relogin_account.py --all      # 修所有账号
说明：
    - 如果拼多多要求短信/验证码，脚本会失败并提示「请到程序界面手动登录」；
    - 成功会把最新 cookies 写回数据库，随后 HTTP 订单接口与浏览器兜底都能用。
"""
import argparse
import asyncio
import os
import sys

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("LOG_FILE", "logs/test.log")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import loguru

_o = loguru.logger.add
loguru.logger.add = lambda *a, **k: (k.__setitem__("enqueue", False), _o(*a, **k))[1]


async def fix_account(acc: dict) -> bool:
    from Channel.pinduoduo.pdd_login import login_pdd, refresh_pdd_cookies

    username = acc["username"]
    user_id = acc["user_id"]
    shop_id = acc["shop_id"]
    password = acc.get("password") or ""
    print(f"\n== 账号 {username}（user_id={user_id} shop={shop_id}）==")

    print("① 用已保存的浏览器会话刷新 cookies…")
    try:
        refreshed = await refresh_pdd_cookies(username)
    except Exception as e:
        refreshed = False
        print("   刷新异常:", e)
    if refreshed:
        cookies = refreshed.get("cookies") if isinstance(refreshed, dict) else ""
        if cookies:
            _save(shop_id, user_id, cookies)
            print("   ✅ 会话仍有效，cookies 已刷新")
            return True
    print("   会话已失效，改为账号密码重新登录…")

    print("② 账号密码登录（无头浏览器）…")
    try:
        result = await login_pdd(username, password, headless=True)
    except Exception as e:
        print("   登录异常:", e)
        result = False
    if not result:
        print("   ❌ 自动登录失败（多半需要短信/验证码）")
        print("   👉 请到程序界面里手动点「登录」重新登录该账号，然后再跑 scripts/check_order_api.py")
        return False

    _save(result.get("shop_id") or shop_id, result.get("user_id") or user_id,
          result.get("cookies") or "")
    print("   ✅ 登录成功，cookies 已写回数据库")
    return True


def _save(shop_id: str, user_id: str, cookies: str) -> None:
    if not cookies:
        return
    from database.db_manager import DatabaseManager
    from core.di_container import container
    from config import config

    try:
        db = container.get(DatabaseManager)
    except Exception:
        db = DatabaseManager(db_path=config.get("database.path") or "temp/channel_shop.db")
        container.register_singleton(DatabaseManager, instance=db)
    db.update_account_cookies("pinduoduo", str(shop_id), str(user_id), cookies)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--account", help="登录名或 user_id")
    parser.add_argument("--all", action="store_true", help="修复所有账号")
    args = parser.parse_args()

    from database.db_manager import DatabaseManager
    from database.models import Account, Shop
    from core.di_container import container
    from config import config

    db_path = config.get("database.path") or "temp/channel_shop.db"
    container.register_singleton(DatabaseManager, instance=DatabaseManager(db_path=db_path))
    from database import db_manager

    accounts = []
    with db_manager.session_scope() as session:
        rows = session.query(Account).all()
        shops = {s.id: s.shop_id for s in session.query(Shop).all()}
        for r in rows:
            accounts.append({"username": r.username, "user_id": r.user_id,
                             "password": r.password or "",
                             "shop_id": shops.get(r.shop_id, "")})
    if args.account and not args.all:
        accounts = [a for a in accounts
                    if a["username"] == args.account or a["user_id"] == str(args.account)]
    if not accounts:
        print("没有找到账号，检查 --account 参数或数据库")
        return 1

    ok = 0
    for acc in accounts:
        if await fix_account(acc):
            ok += 1
    print(f"\n完成：{ok}/{len(accounts)} 个账号可用")
    if ok:
        print("接下来运行：python scripts/check_order_api.py  验证订单查询是否恢复")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
