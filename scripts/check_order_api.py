# -*- coding: utf-8 -*-
"""订单查询通道体检脚本（定位"明明有单却查不到发货订单"）

依次验证三条通道，并给出人话结论：
1. HTTP 订单列表 `mms/recentOrderList`（需要有效的 anti-content 签名）
2. 合并发货页 `mars/shop/mergeShipping/newOrderList`
3. 浏览器兜底（已登录的 Playwright 会话，抓页面自己发的请求）

用法：python scripts/check_order_api.py [--shop 652909334] [--account 110997513]
"""
import argparse
import os
import sys
import time

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("LOG_FILE", "logs/test.log")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shop", default=None, help="平台店铺 ID，默认取数据库第一个店铺")
    parser.add_argument("--account", default=None, help="账号 user_id，默认取该店铺第一个账号")
    args = parser.parse_args()

    from database.db_manager import DatabaseManager
    from database.models import Account, Shop
    from core.di_container import container
    from config import config

    db_path = (config.get("database.path") or "temp/channel_shop.db")
    container.register_singleton(DatabaseManager, instance=DatabaseManager(db_path=db_path))

    from database import db_manager

    shop_id, account_id = args.shop, args.account
    with db_manager.session_scope() as session:
        acc = None
        if account_id:
            acc = session.query(Account).filter(Account.user_id == str(account_id)).first()
        if acc is None:
            acc = session.query(Account).first()
        username = ""
        if acc is not None:
            account_id = acc.user_id
            username = acc.username or ""
            shop_row = session.query(Shop).filter(Shop.id == acc.shop_id).first()
            if shop_row is not None:
                shop_id = shop_id or shop_row.shop_id
        if not shop_id:
            shop = session.query(Shop).first()
            shop_id = shop.shop_id if shop else None

    print("店铺 shop_id=%s 账号 user_id=%s 登录名=%s" % (shop_id, account_id, username))
    if not shop_id or not account_id:
        print("❌ 数据库里没有店铺/账号记录")
        return 1

    print("\n① HTTP 订单列表（recentOrderList）")
    from Channel.pinduoduo.utils.API.order_api import MmsOrderAPI

    try:
        res = MmsOrderAPI(shop_id, account_id).list_orders(page_num=1, page_size=20)
    except Exception as e:
        res = {"success": False, "error_msg": str(e), "orders": []}
    http_ok = bool(res.get("success"))
    print("   success=%s total=%s error=%s" % (
        http_ok, res.get("total"), (res.get("error_msg") or "")[:60]))
    if http_ok:
        pending = [o for o in res.get("orders") or [] if o.get("pending_shipment")]
        print("   待发货 %d 笔；样例：%s" % (
            len(pending),
            [o.get("order_sn") for o in pending[:3]]))
        if pending:
            o = pending[0]
            print("   商品/规格：%s / %s（查询项目看 spec）" % (o.get("goods_name"), o.get("spec")))

    print("\n② 合并发货页（mergeShipping/newOrderList）")
    try:
        merged = MmsOrderAPI(shop_id, account_id).list_pending_shipment_orders()
        print("   success=%s 待发货=%d error=%s" % (
            merged.get("success"), len(merged.get("orders") or []),
            (merged.get("error_msg") or "")[:60]))
    except Exception as e:
        print("   异常：%s" % e)

    print("\n③ 浏览器兜底（用已登录浏览器抓页面自己的请求）")
    from Channel.pinduoduo.utils.API import order_browser

    t0 = time.time()
    orders = order_browser.fetch_pending_orders_via_browser(account_id, timeout=20)
    print("   耗时 %.1fs，待发货 %d 笔，LAST_FAILURE=%r" % (
        time.time() - t0, len(orders), order_browser.LAST_FAILURE))
    for o in orders[:5]:
        print("   - %s / %s / %s" % (o.get("order_sn"), o.get("goods_name"), o.get("spec")))

    print("\n结论：")
    if http_ok:
        print("  ✅ HTTP 通道正常，订单查询可用")
    elif orders:
        print("  ⚠️ HTTP 被风控，但浏览器兜底可用（程序会自动用它）")
    elif order_browser.LAST_FAILURE == "login_expired":
        print("  ❌ 登录态已过期：请先在程序里重新登录账号 %s，否则三条通道都查不到订单" % username)
    else:
        print("  ❌ 三条通道都失败：多半也是登录态/风控问题，请重新登录后再跑一次本脚本")
    return 0


if __name__ == "__main__":
    sys.exit(main())
