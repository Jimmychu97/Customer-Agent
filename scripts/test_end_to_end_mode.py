# -*- coding: utf-8 -*-
"""完整跑线：卡券商品走卡券，发货商品走发货（离线，不联网）

用和线上同构的数据（同一店铺：一个「仅发货」商品 + 一个「卡券核销」商品），
从「客户下单后的订单卡」开始，一直跑到出报告/发货完成，检查两条链路互不串：
    仅发货商品   -> 要序列号，绝口不提卡券；发货后请客户确认序列号，确认才查
    卡券核销商品 -> 要卡券号核销，绝不让客户去发订单号/等发货
    商品未知     -> 引导发【订单卡片/订单号】（订单卡里带商品ID，自动判定），不许提「卡券核销」

运行：uv run python scripts/test_end_to_end_mode.py
"""
import asyncio
import json
import os
import sys
import tempfile
import shutil
import time

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("LOG_FILE", "logs/test.log")  # 自测日志别写进 app.log
os.environ.setdefault("ORDER_BROWSER_FALLBACK", "0")  # 自测不启动浏览器
os.environ.setdefault("ORDER_HEARTBEAT", "0")  # 自测不跑会话心跳
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 自测的临时数据库统一放到 temp/ 下，免得在项目根堆一堆 ship_guard_xxx 目录
tempfile.tempdir = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "temp")
os.makedirs(tempfile.tempdir, exist_ok=True)

import loguru

_o = loguru.logger.add
loguru.logger.add = lambda *a, **k: (k.__setitem__("enqueue", False), _o(*a, **k))[1]

from database.db_manager import DatabaseManager
from database.models import GoodsCommit, MessageRecord, Order, ProductKnowledge, Shop
from core.di_container import container
from core import order_lookup
from bridge.context import Context, ContextType, ChannelType
from Message.handlers import voucher_handler as vh
import Channel.pinduoduo.utils.API.gsxunlocking_api as gsx_api

SHOP_ID, USER_ID, CUST_UID = "652909334", "ACC1", "3930379866"
SHIP_GOODS = "328045268438"     # 商品管理里勾了「发货功能」
VOUCHER_GOODS = "978279333324"  # 配成「卡券核销」的商品
SERIAL = "MPVFCJXF3F"
COUPON = "012345678901"
ORDER_SN = "261029-401373929091626"
SPEC = "特惠激活锁"

_results = {"pass": 0, "fail": 0}


def check(name, ok, detail=""):
    if ok:
        _results["pass"] += 1
        print("  [PASS] %s" % name)
    else:
        _results["fail"] += 1
        print("  [FAIL] %s | %r" % (name, detail))


def make_context(content, ctype=ContextType.TEXT):
    return Context.create_pinduoduo_context(
        content=content, msg_id="m", from_uid=CUST_UID, to_uid="cs", nickname="n",
        user_msg_type=ctype, shop_id=SHOP_ID, user_id=USER_ID, username="u",
        channel_type=ChannelType.PINDUODUO)


def make_db():
    tmpdir = tempfile.mkdtemp(prefix="e2e_mode_")
    db = DatabaseManager(db_path=os.path.join(tmpdir, "t.db"))
    container.register_singleton(DatabaseManager, instance=db)
    with db.session_scope() as s:
        shop = Shop(channel_id=1, shop_id=SHOP_ID, shop_name="果粉速查")
        s.add(shop)
        s.flush()
        s.add(ProductKnowledge(shop_id=shop.id, goods_id=SHIP_GOODS,
                               goods_name="苹果gsx验机查询序列号", service_mode=1))
        s.add(ProductKnowledge(shop_id=shop.id, goods_id=VOUCHER_GOODS,
                               goods_name="苹果手机GSX整机深度报告", service_mode=0))
        s.add(GoodsCommit(shop_id=str(shop.id), goods_id=SHIP_GOODS, sku_id="k1",
                          spec_name=SPEC, server_id=10105, zhenxin_server_id=1014,
                          device_type="apple"))
        s.add(GoodsCommit(shop_id=str(shop.id), goods_id=VOUCHER_GOODS, sku_id="k2",
                          spec_name="全面验机报告+人工解析", server_id=10112,
                          zhenxin_server_id=1032, device_type="apple"))
    return db, tmpdir


class Harness:
    def __init__(self):
        self.sent = []
        self.sent_by_uid = {}
        self.ship_calls = []
        self.verify_calls = []
        self.gsx_calls = []
        self.reports = []
        self.orders = []
        self.detail = {"pending_shipment": True, "goods_id": SHIP_GOODS,
                       "goods_name": "苹果gsx验机查询序列号", "spec": SPEC}
        self.detail_map = {}
        self.pending_summary = {"status": "empty", "orders": []}
        self.summary_fn = None      # 需要按调用次数变化时覆盖这个
        self.h = vh.VoucherDetectionHandler()

        async def fake_send(shop_id, user_id, from_uid, text):
            self.sent.append(text)
            self.sent_by_uid.setdefault(from_uid, []).append(text)
            return True

        async def fake_report_sender(serial, goods_id, spec, report_data, order_id,
                                     shop_id, user_id, from_uid, voucher_success_text=""):
            self.reports.append(report_data)
            if voucher_success_text:
                self.sent.append(voucher_success_text)

        def fake_ship(order_sn, shop_id, user_id):
            self.ship_calls.append(order_sn)
            return {"success": True, "result_code": 100}

        def fake_verify(sn, coupon, shop_id, user_id, from_uid, order_id):
            self.verify_calls.append({"sn": sn, "coupon": coupon})
            self.h._update_order_status(order_id, 1)
            # 与线上 _query_and_verify 返回一致：(结果文案, (goods_id, spec, report_data, device_type))
            return "核销成功！", (VOUCHER_GOODS, "全面验机报告+人工解析", "报告内容", "apple")

        self.h._send = fake_send
        self.h._record_message = lambda *a, **k: None
        self.h._pick_pending_reference = lambda *a, **k: None
        self.h._ship_via_platform = fake_ship
        self.h._query_and_verify = fake_verify
        self.h._query_gsxunlocking_and_send = fake_report_sender
        self.h._query_gsx_and_send = fake_report_sender
        order_lookup.common_pending_goods = lambda *a, **k: None
        order_lookup.pending_orders_summary = lambda *a, **k: (
            self.summary_fn(*a, **k) if self.summary_fn else dict(self.pending_summary))
        order_lookup.get_order_detail = lambda shop, user, order_sn, **k: self.detail_map.get(
            order_sn, self.detail)
        order_lookup.is_cooling_down = lambda *a, **k: False
        gsx_api.query_gsxunlocking = lambda sn, srv: (
            self.gsx_calls.append(srv), {"success": True, "result": "报告内容"})[1]

    def reset(self):
        self.sent.clear()
        self.sent_by_uid.clear()
        self.ship_calls.clear()
        self.verify_calls.clear()
        self.gsx_calls.clear()
        self.reports.clear()
        self.pending_summary = {"status": "empty", "orders": []}
        self.summary_fn = None
        self.detail_map = {}
        vh._serial_confirm_pending.clear()
        vh._serial_confirmed.clear()
        vh._ship_confirmed.clear()

    def last(self):
        return self.sent[-1] if self.sent else ""
    def all_text(self):
        return "\n".join(self.sent)

    async def send(self, content, ctype=ContextType.TEXT, goods_id=None, image_sn=None):
        meta = {"shop_id": SHOP_ID, "user_id": USER_ID, "from_uid": CUST_UID}
        if goods_id:
            meta["goods_id"] = goods_id
        if image_sn:
            meta["_image_sn"] = image_sn
            meta["_image_coupon"] = None
        return await self.h.handle(make_context(content, ctype), meta)


def clear_orders(db):
    with db.session_scope() as s:
        s.query(Order).delete()


def add_order(db, order_sn, uid=None, sn="", status=0, **extra):
    """造一笔「这个客户聊过」的订单（等价于平台把订单卡片推进了会话）"""
    with db.session_scope() as s:
        row = Order(shop_id=SHOP_ID, session_id=uid or CUST_UID, sn_imei=sn, coupons="",
                    order_sn=order_sn, status=status, service_mode=1, **extra)
        s.add(row)
        s.flush()
        return row.id


def count_orders(db):
    with db.session_scope() as s:
        return s.query(Order).count()


async def main():
    db, tmpdir = make_db()
    h = Harness()
    session_id = "pinduoduo%s" % CUST_UID

    print("S1 发货商品：客户下单后平台推订单卡")
    h.reset()
    clear_orders(db)
    card = json.dumps({"order_id": ORDER_SN, "goods_id": SHIP_GOODS,
                       "goods_name": "苹果gsx验机查询序列号", "spec": SPEC}, ensure_ascii=False)
    check("S1 核销处理器接管订单卡（否则会被 AI 索要卡券）",
          h.h.can_handle(make_context(card, ContextType.ORDER_INFO)) is True)
    meta = {"shop_id": SHOP_ID, "user_id": USER_ID, "from_uid": CUST_UID, "goods_id": SHIP_GOODS}
    await h.h.handle(make_context(card, ContextType.ORDER_INFO), meta)
    check("S1 记录了订单号", ORDER_SN in h.all_text(), h.all_text())
    check("S1 要序列号", "序列号" in h.all_text(), h.all_text())
    check("S1 没有提卡券", "卡券" not in h.all_text(), h.all_text())
    print("  ── 客户收到 ──\n" + h.last())

    h.reset()
    clear_orders(db)

    print("S2 发货商品：客户发序列号+订单号 → 发货 → 确认序列号 → 出报告")
    h.reset()
    await h.send("序列号 %s 订单号 %s" % (SERIAL, ORDER_SN), goods_id=SHIP_GOODS)
    check("S2 确认前不发货", h.ship_calls == [], h.ship_calls)
    check("S2 未确认前不查", h.gsx_calls == [], h.gsx_calls)
    check("S2 请客户确认序列号", "确认" in h.last() and SERIAL in h.last(), h.last())
    check("S2 确认信息带「查询项目：特惠激活锁」",
          "查询项目：特惠激活锁" in h.last(), h.last())
    check("S2 全程没提卡券", "卡券" not in h.all_text(), h.all_text())
    print("  ── 客户收到 ──\n" + h.last())
    await h.send("确认")
    check("S2 确认后按特惠激活锁 1014 查", h.gsx_calls == ["1014"], h.gsx_calls)
    check("S2 报告已发出", h.reports == ["报告内容"], h.reports)
    check("S2 确认后仍没提卡券", "卡券" not in h.all_text(), h.all_text())

    h.reset()
    clear_orders(db)

    print("S3 卡券商品：客户下单后订单卡应交给 AI（不走发货）")
    h.reset()
    vcard = json.dumps({"order_id": ORDER_SN, "goods_id": VOUCHER_GOODS,
                        "goods_name": "苹果手机GSX整机深度报告", "spec": "全面验机报告+人工解析"},
                       ensure_ascii=False)
    check("S3 核销处理器不接管卡券商品的订单卡",
          h.h.can_handle(make_context(vcard, ContextType.ORDER_INFO)) is False)

    h.reset()
    clear_orders(db)

    print("S4 卡券商品：客户发序列号+卡券 → 核销 → 出报告（不让客户发订单号）")
    h.reset()
    await h.send("序列号 %s 卡券 %s" % (SERIAL, COUPON), goods_id=VOUCHER_GOODS)
    check("S4 核销 1 次", len(h.verify_calls) == 1, h.verify_calls)
    check("S4 没有触发发货", h.ship_calls == [], h.ship_calls)
    check("S4 没有让客户再发订单号", "订单号发我" not in h.all_text(), h.all_text())
    check("S4 报告已出", h.reports == ["报告内容"], h.reports)

    h.reset()
    clear_orders(db)

    print("S5 商品未知（客户只发序列号、没订单卡）：引导发订单卡片，不许提「卡券核销」")
    h.reset()
    await h.send("序列号 %s" % SERIAL)
    check("S5 没提「卡券核销」", "卡券核销" not in h.all_text(), h.all_text())
    check("S5 没让客户选服务方式", "哪种服务" not in h.all_text(), h.all_text())
    check("S5 让客户发订单卡片/订单号", "订单" in h.all_text(), h.all_text())
    print("  ── 客户收到 ──\n" + h.last())

    print("S6 AI 的「待补提示」也要按商品模式说话")
    from Message.handlers.ai_handler import AIReplyHandler

    ai = AIReplyHandler(bot=None)
    with db.session_scope() as s:
        s.query(Order).delete()
        s.add(Order(shop_id=SHOP_ID, session_id=CUST_UID, sn_imei=SERIAL, coupons="",
                    order_sn=None, status=0, service_mode=1))
    hint_ship = ai._get_pending_voucher_hint(SHOP_ID, CUST_UID)
    check("S6 仅发货单的提示不提卡券", "卡券" not in hint_ship, hint_ship)
    with db.session_scope() as s:
        s.query(Order).delete()
        s.add(Order(shop_id=SHOP_ID, session_id=CUST_UID, sn_imei=SERIAL, coupons="",
                    order_sn=None, status=0, service_mode=None))
    hint_unknown = ai._get_pending_voucher_hint(SHOP_ID, CUST_UID)
    check("S6 模式未确定时也不提卡券", "卡券" not in hint_unknown, hint_unknown)

    print("S7 客户只发订单号（平台能查到）：记录订单号 + 要序列号")
    h.reset()
    clear_orders(db)
    check("S7 订单号文本也由核销处理器接管（不放给 AI）",
          h.h.can_handle(make_context("订单号 %s" % ORDER_SN)) is True)
    await h.send("订单号 %s" % ORDER_SN, goods_id=SHIP_GOODS)
    check("S7 记下了订单号", ORDER_SN in h.all_text(), h.all_text())
    check("S7 要序列号", "序列号" in h.all_text(), h.all_text())
    check("S7 没提卡券", "卡券" not in h.all_text(), h.all_text())
    check("S7 落了 1 条单", count_orders(db) == 1, count_orders(db))

    print("S8 客户发半截/错误订单号：明确让他核对，不建错单、不糊弄")
    h.reset()
    clear_orders(db)
    h.detail = None            # 平台查不到这个订单号
    bad_sn = "261030-013809805640"
    await h.send("订单号 %s" % bad_sn, goods_id=SHIP_GOODS)
    check("S8 明确告知平台没查到", "没查到" in h.all_text(), h.all_text())
    check("S8 让客户重发完整订单号/订单卡片",
          ("完整订单号" in h.all_text()) and ("订单卡片" in h.all_text()), h.all_text())
    check("S8 不建错单", count_orders(db) == 0, count_orders(db))
    check("S8 不说「已记录」", "已记录" not in h.all_text(), h.all_text())

    print("S9 客户改发正确订单号：正常记录并要序列号")
    h.reset()
    h.detail = {"pending_shipment": True, "goods_id": SHIP_GOODS,
                "goods_name": "苹果gsx验机查询序列号", "spec": SPEC}
    await h.send("订单号 %s" % ORDER_SN, goods_id=SHIP_GOODS)
    check("S9 恢复正常记录", ORDER_SN in h.all_text() and "序列号" in h.all_text(), h.all_text())
    check("S9 落了 1 条单", count_orders(db) == 1, count_orders(db))

    print("S10 平台只有 1 笔待发货单：直接用那笔发货，不用客户发订单号")
    h.reset()
    clear_orders(db)
    with db.session_scope() as s:
        s.add(Order(shop_id=SHOP_ID, session_id=CUST_UID, sn_imei="", coupons="",
                    order_sn=ORDER_SN, status=0, service_mode=1))
    h.pending_summary = {"status": "unique", "orders": [{
        "order_sn": ORDER_SN, "goods_id": SHIP_GOODS, "spec": SPEC,
        "goods_name": "苹果gsx验机查询序列号"}]}
    await h.send("序列号 %s" % SERIAL, goods_id=SHIP_GOODS)
    check("S10 确认前不发货", h.ship_calls == [], h.ship_calls)
    check("S10 请客户确认序列号", "确认" in (h.last() or ""), h.last())
    check("S10 按订单查出「查询项目：特惠激活锁」",
          "查询项目：特惠激活锁" in (h.last() or ""), h.last())
    await h.send("确认")
    check("S10 确认后按 1014 查", h.gsx_calls == ["1014"], h.gsx_calls)
    check("S10 报告已反馈", h.reports == ["报告内容"], h.reports)

    print("S11 他名下有多笔待发货单：按【最新一单】让客户确认，确认后发最新那笔")
    h.reset()
    clear_orders(db)
    second_sn = "261030-013809805640197"
    with db.session_scope() as s:
        s.add(Order(shop_id=SHOP_ID, session_id=CUST_UID, sn_imei="", coupons="",
                    order_sn=ORDER_SN, status=0, service_mode=1))
        s.add(Order(shop_id=SHOP_ID, session_id=CUST_UID, sn_imei="", coupons="",
                    order_sn=second_sn, status=0, service_mode=1))
    h.pending_summary = {"status": "mixed", "orders": [
        {"order_sn": ORDER_SN, "goods_id": SHIP_GOODS, "spec": SPEC,
         "goods_name": "苹果gsx验机", "order_time": 1000},
        {"order_sn": second_sn, "goods_id": SHIP_GOODS, "spec": SPEC,
         "goods_name": "苹果gsx验机", "order_time": 2000},   # 更新的一笔
    ]}
    await h.send("序列号 %s" % SERIAL, goods_id=SHIP_GOODS)
    check("S11 多笔时不乱发货", h.ship_calls == [], h.ship_calls)
    check("S11 按最新一单给确认信息",
          second_sn in (h.last() or "") and "最新一单" in (h.last() or ""), h.last())
    check("S11 结尾提示旧报告回「重发」", "重发" in (h.last() or ""), h.last())
    await h.send("确认")
    check("S11 确认后发最新那笔", h.ship_calls == [second_sn], h.ship_calls)
    check("S11 报告已反馈", h.reports == ["报告内容"], h.reports)

    print("S12 他名下没有待发货单：仍按订单卡片/订单号引导，不瞎发货")
    h.reset()
    clear_orders(db)
    h.pending_summary = {"status": "empty", "orders": []}
    await h.send("序列号 %s" % SERIAL, goods_id=SHIP_GOODS)
    check("S12 不发货", h.ship_calls == [], h.ship_calls)
    check("S12 引导发订单卡片/订单号", "订单" in h.all_text(), h.all_text())

    print("S13 本地只剩旧单（平台已发货）：不再念旧单号，请他发新订单号")
    h.reset()
    clear_orders(db)
    h.detail = None
    h.detail_map = {ORDER_SN: {"pending_shipment": False, "order_status_str": "已发货"}}
    with db.session_scope() as s:
        row = Order(shop_id=SHOP_ID, session_id=CUST_UID, sn_imei="", coupons="",
                    order_sn=ORDER_SN, status=0, service_mode=1)
        s.add(row)
        s.flush()
        old_id = row.id
        # 真正的「旧单」：3 小时前下单（超出 120 分钟续接窗口）
        from datetime import datetime as _dt, timedelta as _td
        row.created_at = _dt.now() - _td(hours=3)
    await h.send("序列号 %s" % SERIAL, goods_id=SHIP_GOODS)
    check("S13 不按旧单发货", h.ship_calls == [], h.ship_calls)
    check("S13 不再念旧单号", ORDER_SN not in h.all_text(), h.all_text())
    check("S13 引导发订单卡片", "订单卡片" in h.all_text(), h.all_text())

    print("S14 本地还挂着旧单、平台只有新单：用平台那笔新单，不念旧单号")
    h.reset()
    clear_orders(db)
    old_sn, new_sn = "261029-000000000000001", "261030-013809805640197"
    h.detail = None
    h.detail_map = {old_sn: {"pending_shipment": False}}
    h.pending_summary = {"status": "unique", "orders": [{
        "order_sn": new_sn, "goods_id": SHIP_GOODS, "spec": SPEC,
        "goods_name": "苹果gsx验机查询序列号"}]}
    with db.session_scope() as s:
        s.add(Order(shop_id=SHOP_ID, session_id=CUST_UID, sn_imei="", coupons="",
                    order_sn=old_sn, status=0, service_mode=1))
        s.add(Order(shop_id=SHOP_ID, session_id=CUST_UID, sn_imei="", coupons="",
                    order_sn=new_sn, status=0, service_mode=1))
    await h.send("序列号 %s" % SERIAL, goods_id=SHIP_GOODS)
    check("S14 确认前不发货", h.ship_calls == [], h.ship_calls)
    check("S14 没有念旧单号", old_sn not in h.all_text(), h.all_text())
    await h.send("确认")
    check("S14 确认后用新单发货", h.ship_calls == [new_sn], h.ship_calls)

    print("S16 文本序列号 + 平台只有 1 笔待发货（客户已有该单记录）：自动认领，不用客户发订单号")
    h.reset()
    clear_orders(db)
    h.detail = {"pending_shipment": True, "goods_id": SHIP_GOODS,
                "goods_name": "苹果gsx验机查询序列号", "spec": SPEC}
    add_order(db, ORDER_SN)      # 订单卡片已推进会话（归属证据）
    h.pending_summary = {"status": "unique", "orders": [{
        "order_sn": ORDER_SN, "goods_id": SHIP_GOODS, "spec": SPEC,
        "goods_name": "苹果gsx验机查询序列号"}]}
    await h.send("序列号 %s" % SERIAL, goods_id=SHIP_GOODS)
    check("S16 确认前不发货", h.ship_calls == [], h.ship_calls)
    check("S16 确认前不查", h.gsx_calls == [], h.gsx_calls)
    check("S16 直接给确认信息（含订单号）", ORDER_SN in (h.last() or ""), h.last())
    check("S16 确认信息含查询项目", "查询项目：特惠激活锁" in (h.last() or ""), h.last())
    check("S16 没有再要客户发订单", "把【订单卡片】" not in (h.last() or ""), h.last())
    await h.send("确认")
    check("S16 确认后发货 1 次", h.ship_calls == [ORDER_SN], h.ship_calls)
    check("S16 确认后按 1014 查", h.gsx_calls == ["1014"], h.gsx_calls)
    check("S16 报告已反馈", h.reports == ["报告内容"], h.reports)

    print("S17 平台有多笔待发货：默认按【最新一单】确认；客户指定别的单时以他为准")
    h.reset()
    clear_orders(db)
    sn_a, sn_b = "261030-000000000000011", "261030-000000000000012"
    h.detail = {"pending_shipment": True, "goods_id": SHIP_GOODS,
                "goods_name": "苹果gsx验机查询序列号", "spec": SPEC}
    h.pending_summary = {"status": "mixed", "orders": [
        {"order_sn": sn_a, "goods_id": SHIP_GOODS, "spec": SPEC,
         "goods_name": "苹果gsx验机", "order_time": 1000},
        {"order_sn": sn_b, "goods_id": SHIP_GOODS, "spec": SPEC,
         "goods_name": "苹果gsx验机", "order_time": 2000},
    ]}
    add_order(db, sn_a)
    add_order(db, sn_b)
    await h.send("序列号 %s" % SERIAL, goods_id=SHIP_GOODS)
    check("S17 多笔时不发货", h.ship_calls == [], h.ship_calls)
    check("S17 默认按最新一单确认",
          sn_b in (h.last() or "") and "最新一单" in (h.last() or ""), h.last())
    check("S17 结尾提示旧报告回「重发」", "重发" in (h.last() or ""), h.last())
    h.reset()
    await h.send("序列号 %s 订单号 %s" % (SERIAL, sn_a), goods_id=SHIP_GOODS)
    check("S17 指定后先确认不发货", h.ship_calls == [], h.ship_calls)
    check("S17 确认信息带指定订单号", sn_a in (h.last() or ""), h.last())
    await h.send("确认")
    check("S17 确认后用指定那笔发货", h.ship_calls == [sn_a], h.ship_calls)
    check("S17 确认后出报告", h.reports == ["报告内容"], h.reports)

    print("S18 平台没有/查不到待发货：不查不发，如实说明")
    h.reset()
    clear_orders(db)
    h.pending_summary = {"status": "empty", "orders": []}
    await h.send("序列号 %s" % SERIAL, goods_id=SHIP_GOODS)
    check("S18 没有单时不发货", h.ship_calls == [], h.ship_calls)
    check("S18 没有单时不查", h.gsx_calls == [], h.gsx_calls)
    check("S18 说明本店没有待发货单", "没有待发货" in h.all_text(), h.all_text())
    check("S18 引导发订单卡片", "订单卡片" in h.all_text(), h.all_text())

    h.reset()
    h.pending_summary = {"status": "unavailable", "orders": []}
    await h.send("序列号 %s" % SERIAL, goods_id=SHIP_GOODS)
    check("S18 限流时不谎称没有单", "没有待发货" not in h.all_text(), h.all_text())
    check("S18 限流时说明暂时没查到", "没查到" in h.all_text(), h.all_text())

    print("S19 历史消息停在卡券商品，但本次订单是发货商品：必须走发货，不许提卡券")
    h.reset()
    clear_orders(db)
    with db.session_scope() as s:
        s.add(MessageRecord(channel="pinduoduo", shop_id=SHOP_ID, from_uid=CUST_UID,
                            direction="in", msg_type="text",
                            content='{"goods_id": %s, "goods_name": "整机深度报告"}'
                                    % VOUCHER_GOODS))
        s.add(Order(shop_id=SHOP_ID, session_id=CUST_UID, sn_imei="", coupons="",
                    order_sn=ORDER_SN, status=0, service_mode=1))
    h.detail = {"pending_shipment": True, "goods_id": SHIP_GOODS,
                "goods_name": "苹果gsx验机查询序列号", "spec": SPEC}
    h.pending_summary = {"status": "unique", "orders": [{
        "order_sn": ORDER_SN, "goods_id": SHIP_GOODS, "spec": SPEC,
        "goods_name": "苹果gsx验机查询序列号"}]}
    await h.send("序列号 %s" % SERIAL)          # 不带 goods_id，模拟真实文本消息
    check("S19 不要卡券", "卡券" not in h.all_text(), h.all_text())
    check("S19 直接给确认信息", "请核对本次查询信息" in h.all_text(), h.all_text())
    check("S19 查询项目取自订单", "查询项目：特惠激活锁" in h.all_text(), h.all_text())
    check("S19 确认前不发货", h.ship_calls == [], h.ship_calls)
    await h.send("确认")
    check("S19 确认后按发货商品那单发货", h.ship_calls == [ORDER_SN], h.ship_calls)
    check("S19 用 1014 查（不是卡券商品的 1032）", h.gsx_calls == ["1014"], h.gsx_calls)
    check("S19 报告已反馈", h.reports == ["报告内容"], h.reports)

    print("S20 订单详情被限流（40002）时，查询项目仍要从订单列表拿得到")
    h.reset()
    clear_orders(db)
    with db.session_scope() as s:
        s.add(Order(shop_id=SHOP_ID, session_id=CUST_UID, sn_imei="", coupons="",
                    order_sn=ORDER_SN, status=0, service_mode=1))
    h.detail = None            # 订单详情接口被限流
    h.pending_summary = {"status": "unique", "orders": [{
        "order_sn": ORDER_SN, "goods_id": SHIP_GOODS, "spec": SPEC,
        "goods_name": "苹果gsx验机查询序列号"}]}
    await h.send("序列号 %s" % SERIAL, goods_id=SHIP_GOODS)
    check("S20 查询项目不空（来自订单列表）",
          "查询项目：特惠激活锁" in (h.last() or ""), h.last())
    check("S20 不显示「没识别到查询项目」",
          "没识别到查询项目" not in (h.last() or ""), h.last())
    await h.send("确认")
    check("S20 确认后按 1014 查", h.gsx_calls == ["1014"], h.gsx_calls)
    check("S20 报告已反馈", h.reports == ["报告内容"], h.reports)

    print("S21 客户问「我的订单/待发货」：后台查单 → 告诉他商品 → 要序列号 → 确认后发货查询")
    h.reset()
    clear_orders(db)
    h.pending_summary = {"status": "unique", "orders": [{
        "order_sn": ORDER_SN, "goods_id": SHIP_GOODS, "spec": SPEC,
        "goods_name": "苹果gsx验机查询序列号"}]}
    add_order(db, ORDER_SN)      # 订单卡片已推进会话（归属证据）
    check("S21 订单问句由本处理器接管（不给 AI 瞎答）",
          h.h.can_handle(make_context("我的订单呢")) is True)
    await h.send("我的订单呢")
    check("S21 告诉他订单号", ORDER_SN in h.all_text(), h.all_text())
    check("S21 告诉他商品", "商品：苹果gsx验机查询序列号" in h.all_text(), h.all_text())
    check("S21 告诉他查询项目", "查询项目：特惠激活锁" in h.all_text(), h.all_text())
    check("S21 让他发序列号", "序列号" in h.all_text(), h.all_text())
    check("S21 没提卡券", "卡券" not in h.all_text(), h.all_text())

    h.reset()
    h.pending_summary = {"status": "unique", "orders": [{
        "order_sn": ORDER_SN, "goods_id": SHIP_GOODS, "spec": SPEC,
        "goods_name": "苹果gsx验机查询序列号"}]}
    await h.send("序列号 %s" % SERIAL, goods_id=SHIP_GOODS)
    check("S21 发序列号后先确认", "请核对本次查询信息" in (h.last() or ""), h.last())
    check("S21 确认前不发货", h.ship_calls == [], h.ship_calls)
    await h.send("确认")
    check("S21 确认后发货 1 次", h.ship_calls == [ORDER_SN], h.ship_calls)
    check("S21 确认后用 1014 查", h.gsx_calls == ["1014"], h.gsx_calls)
    check("S21 报告已反馈", h.reports == ["报告内容"], h.reports)

    print("S22 客户问订单、后台确实没有：如实说并要订单卡片")
    h.reset()
    clear_orders(db)
    h.pending_summary = {"status": "empty", "orders": []}
    await h.send("待发货啊")
    check("S22 说明没查到待发货单", "没查到" in h.all_text(), h.all_text())
    check("S22 引导发订单卡片", "订单卡片" in h.all_text(), h.all_text())
    check("S22 不发货", h.ship_calls == [], h.ship_calls)

    print("S23 10 个客户并发发序列号：各查各的单，不串单、不互相卡")
    h.reset()
    clear_orders(db)
    accounts = ["uid_%02d" % i for i in range(10)]
    with db.session_scope() as s:
        for i, uid in enumerate(accounts):
            s.add(Order(shop_id=SHOP_ID, session_id=uid, sn_imei="", coupons="",
                        order_sn="261031-0000000000000%02d" % i, status=0, service_mode=1))
    h.pending_summary = {"status": "mixed", "orders": [
        {"order_sn": "261031-0000000000000%02d" % i, "goods_id": SHIP_GOODS,
         "spec": SPEC, "goods_name": "苹果gsx验机"} for i in range(10)]}
    h.detail = None   # 详情接口限流也不影响（规格来自待发货列表）

    async def _one(i, uid):
        ctx = Context.create_pinduoduo_context(
            content="序列号 KHFDFDWWH%01d" % i, msg_id="m%d" % i, from_uid=uid, to_uid="cs",
            nickname="n", user_msg_type=ContextType.TEXT, shop_id=SHOP_ID, user_id=USER_ID,
            username="u", channel_type=ChannelType.PINDUODUO)
        await h.h.handle(ctx, {"shop_id": SHOP_ID, "user_id": USER_ID, "from_uid": uid,
                               "goods_id": SHIP_GOODS})

    started = time.time()
    await asyncio.gather(*[_one(i, uid) for i, uid in enumerate(accounts)])
    elapsed = time.time() - started

    ok_cnt = 0
    for i, uid in enumerate(accounts):
        expect = "261031-0000000000000%02d" % i
        text = "\n".join(h.sent_by_uid.get(uid, []))
        cross = [o for o in ("261031-0000000000000%02d" % j for j in range(10))
                 if o != expect and o in text]
        if expect in text and "查询项目：特惠激活锁" in text and not cross:
            ok_cnt += 1
    check("S23 10 个客户都拿到自己的单（不串单、带查询项目）", ok_cnt == 10, ok_cnt)
    check("S23 并发总耗时 < 3s（单飞+线程池）", elapsed < 3.0, round(elapsed, 2))

    print("S24 先发订单号、再发序列号（平台列表为空/详情被限流）：要给确认信息并发货查询")
    h.reset()
    clear_orders(db)
    h.pending_summary = {"status": "empty", "orders": []}
    h.detail = None                      # 平台详情被限流
    order_lookup.is_cooling_down = lambda *a, **k: True   # 模拟 40002 冷却期（先记录订单号）
    await h.send("订单号 %s" % ORDER_SN, goods_id=SHIP_GOODS)
    order_lookup.is_cooling_down = lambda *a, **k: False
    check("S24 订单号先记录", "已记录" in h.all_text() or ORDER_SN in h.all_text(), h.all_text())

    h.reset()
    await h.send("序列号 %s" % SERIAL, goods_id=SHIP_GOODS)
    check("S24 序列号后给确认信息（不是「之前的订单已发货完成」）",
          "请核对本次查询信息" in h.all_text()
          and "之前的订单已经发货完成" not in h.all_text(), h.all_text())
    check("S24 确认信息带订单号", ORDER_SN in h.all_text(), h.all_text())
    check("S24 确认前不发货", h.ship_calls == [], h.ship_calls)

    # 客户确认时平台详情恢复（能核对到查询项目）
    h.detail = {"pending_shipment": True, "goods_id": SHIP_GOODS,
                "goods_name": "苹果gsx验机查询序列号", "spec": SPEC}
    await h.send("确认")
    check("S24 确认后发货 1 次", h.ship_calls == [ORDER_SN], h.ship_calls)
    check("S24 确认后按 1014 查", h.gsx_calls == ["1014"], h.gsx_calls)
    check("S24 报告已反馈", h.reports == ["报告内容"], h.reports)

    print("S25 发序列号必须先去后台实查（不吃缓存、不受冷却限制）")
    h.reset()
    clear_orders(db)
    calls = []

    def _fn(shop, user, force=False):
        calls.append(bool(force))
        if not force:            # 非强制（吃缓存/限流）→ 拿到空快照
            return {"status": "empty", "orders": []}
        return {"status": "unique", "orders": [{
            "order_sn": ORDER_SN, "goods_id": SHIP_GOODS, "spec": SPEC,
            "goods_name": "苹果gsx验机查询序列号"}]}

    h.summary_fn = _fn
    add_order(db, ORDER_SN)      # 归属证据（订单卡片已推进会话）
    await h.send("序列号 %s" % SERIAL, goods_id=SHIP_GOODS)
    check("S25 客户发序列号时强制去查（force=True）", calls and calls[0] is True, calls)
    check("S25 查到就直接给确认信息",
          "请核对本次查询信息" in h.all_text() and ORDER_SN in h.all_text(), h.all_text())

    print("S26 客户待发货订单要能直接告诉 AI（不让客户发订单卡片）")
    h.reset()
    clear_orders(db)
    h.pending_summary = {"status": "unique", "orders": [{
        "order_sn": ORDER_SN, "goods_id": SHIP_GOODS, "spec": SPEC,
        "goods_name": "苹果gsx验机查询序列号"}]}
    add_order(db, ORDER_SN)      # 只讲客户自己的单：先有归属证据
    hint = order_lookup.pending_order_hint(SHOP_ID, USER_ID, CUST_UID)
    check("S26 提示词里带订单号和商品", ORDER_SN in hint and "苹果gsx验机" in hint, hint)

    # 自测环境没有知识库服务：注册个占位，避免 Agent 包导入时抛错
    try:
        from database.knowledge_service import KnowledgeService

        container.register_singleton(KnowledgeService, instance=object())
    except Exception:
        pass
    from Agent.CustomerAgent.custom.message_builder import MessageBuilder

    MessageBuilder._inject_product_list = lambda self, deps: None   # 自测不查商品库
    msgs = MessageBuilder([]).build_messages(
        "我的订单呢", [], {"shop_id": SHOP_ID, "user_id": USER_ID,
                          "recipient_uid": CUST_UID, "pending_order_hint": hint})
    system_text = "\n".join(m["content"] for m in msgs if m.get("role") == "system")
    check("S26 AI 系统提示注入待发货订单", ORDER_SN in system_text, system_text[-300:])
    check("S26 明确禁止再让客户发订单卡片",
          "严禁再让他发订单卡片" in system_text, system_text[-400:])

    h.pending_summary = {"status": "empty", "orders": []}
    h.detail = None
    check("S26 没有待发货单时提示为空", order_lookup.pending_order_hint(
        SHOP_ID, USER_ID, "no_such_uid") == "")

    print("S27 同一序列号有历史已发货单，但客户又下了新单：必须确认新单，不要念旧单")
    h.reset()
    clear_orders(db)
    old_order = "261030-067612208771626"
    new_order = "261031-629040760451626"
    with db.session_scope() as s:
        from datetime import datetime as _now

        # 已发货完成 = status 4 + shipped_at（status 1 只代表核销成功）
        s.add(Order(shop_id=SHOP_ID, session_id=CUST_UID, sn_imei=SERIAL, coupons="",
                    order_sn=old_order, status=4, service_mode=1,
                    shipped_at=_now.now(),
                    sku_id="1014", gsx_result="旧报告内容"))
    h.pending_summary = {"status": "unique", "orders": [{
        "order_sn": new_order, "goods_id": SHIP_GOODS, "spec": SPEC,
        "goods_name": "苹果gsx验机查询序列号", "order_time": 2000}]}
    add_order(db, new_order)     # 新单的订单卡片也推进了会话
    await h.send("序列号 %s" % SERIAL, goods_id=SHIP_GOODS)
    check("S27 给的是新单确认信息", new_order in (h.last() or ""), h.last())
    check("S27 不念旧单号", old_order not in (h.last() or ""), h.last())
    check("S27 结尾提示旧报告回「重发」", "重发" in (h.last() or ""), h.last())
    await h.send("确认")
    check("S27 确认后发新单", h.ship_calls == [new_order], h.ship_calls)
    check("S27 确认后按 1014 查", h.gsx_calls == ["1014"], h.gsx_calls)

    print("S28 没有新单、只有历史已发货单：提示「要报告回复重发」，不刷历史报告")
    h.reset()
    clear_orders(db)
    with db.session_scope() as s:
        from datetime import datetime as _now

        # 已发货完成 = status 4 + shipped_at（status 1 只代表核销成功）
        s.add(Order(shop_id=SHOP_ID, session_id=CUST_UID, sn_imei=SERIAL, coupons="",
                    order_sn=old_order, status=4, service_mode=1,
                    shipped_at=_now.now(),
                    sku_id="1014", gsx_result="旧报告内容"))
    h.pending_summary = {"status": "empty", "orders": []}
    await h.send("序列号 %s" % SERIAL, goods_id=SHIP_GOODS)
    check("S28 不重复发旧报告", h.reports == [], h.reports)
    check("S28 提示回复「重发」", "重发" in h.all_text(), h.all_text())
    check("S28 不发货", h.ship_calls == [], h.ship_calls)

    print("S29 新客户(无任何订单)咨询：店里有别人的待发货单，也绝不能把单号发给客户")
    h.reset()
    clear_orders(db)
    other_sn = "261031-999888777666555"
    h.pending_summary = {"status": "unique", "orders": [{
        "order_sn": other_sn, "goods_id": SHIP_GOODS, "spec": SPEC,
        "goods_name": "苹果gsx验机查询序列号", "order_time": 3000}]}
    h.detail = {"pending_shipment": True, "goods_id": SHIP_GOODS,
                "goods_name": "苹果gsx验机查询序列号", "spec": SPEC}
    # ① 他只发序列号
    await h.send("序列号 %s" % SERIAL, goods_id=SHIP_GOODS)
    check("S29 不泄露别人的订单号", other_sn not in h.all_text(), h.all_text())
    check("S29 让他发自己的订单卡片", "订单卡片" in h.all_text(), h.all_text())
    check("S29 不发货", h.ship_calls == [], h.ship_calls)
    # ② 他直接问「我的订单」
    h.reset()
    await h.send("我的订单呢")
    check("S29 问订单也不泄露别人的单号", other_sn not in h.all_text(), h.all_text())
    check("S29 引导发订单卡片", "订单卡片" in h.all_text(), h.all_text())
    # ③ AI 提示词里也不能出现别人的单号
    hint = order_lookup.pending_order_hint(SHOP_ID, USER_ID, CUST_UID)
    check("S29 AI 提示词不含别人的单号", other_sn not in (hint or ""), hint)

    print("S30 客户刚下单（聊天里有「邀请下单成功…实收」系统消息）：自动认领最新待发货单并让他确认")
    h.reset()
    clear_orders(db)
    bought_sn = "261031-555444333222111"
    with db.session_scope() as s:
        s.add(MessageRecord(channel="pinduoduo", shop_id=SHOP_ID, account_id=USER_ID,
                            from_uid=CUST_UID, direction="out", msg_type="mall_cs",
                            content="邀请下单成功！消费者已发起拼单，实收6.75元 "))
    h.pending_summary = {"status": "unique", "orders": [{
        "order_sn": bought_sn, "goods_id": SHIP_GOODS, "spec": SPEC,
        "goods_name": "苹果gsx验机查询序列号", "order_time": 5000}]}
    await h.send("序列号 %s" % SERIAL, goods_id=SHIP_GOODS)
    check("S30 自动认领到那笔单并给确认信息", bought_sn in (h.last() or ""), h.last())
    check("S30 说明依据「刚拍下一单」", "刚拍下一单" in (h.last() or ""), h.last())
    check("S30 确认前不发货", h.ship_calls == [], h.ship_calls)
    await h.send("确认")
    check("S30 确认后发货", h.ship_calls == [bought_sn], h.ship_calls)
    check("S30 确认后按 1014 查", h.gsx_calls == ["1014"], h.gsx_calls)

    shutil.rmtree(tmpdir, ignore_errors=True)
    print("\n合计: %d 通过 / %d 失败" % (_results["pass"], _results["fail"]))
    return 0 if _results["fail"] == 0 else 1


sys.exit(asyncio.run(main()))
