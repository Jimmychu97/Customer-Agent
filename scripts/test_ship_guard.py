# -*- coding: utf-8 -*-
"""发货链路「防呆」自测脚本（离线，不联网）

逐条验证客户消息重复推送、订单状态异常、参数缺失、接口限流时，
程序会不会乱打平台接口、乱发报告：

G1 缺序列号           -> 不调发货接口，回引导
G2 缺订单号           -> 不调发货接口，要订单号
G3 订单号已发货成功   -> 不重复发货（数据库判重）
G4 本单已发货成功     -> 不重复发货
G5 订单不是「待发货」 -> 拒绝发货
G6 并发重复推送       -> 只发 1 次
G7 规格未知（本次修复）-> 发货 1 次，验机 0 次，只回「核对规格」
G8 平台发货失败       -> 不查报告，订单落「失败」状态
G9 订单详情被限流     -> 不阻断发货（降级继续）

运行：uv run python scripts/test_ship_guard.py
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
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 自测的临时数据库统一放到 temp/ 下，免得在项目根堆一堆 ship_guard_xxx 目录
tempfile.tempdir = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "temp")
os.makedirs(tempfile.tempdir, exist_ok=True)

# 沙箱里 loguru 的 enqueue 需要命名管道，离线自测关掉异步写日志
import loguru

_orig_add = loguru.logger.add


def _add_no_enqueue(*args, **kwargs):
    kwargs["enqueue"] = False
    return _orig_add(*args, **kwargs)


loguru.logger.add = _add_no_enqueue

from database.db_manager import DatabaseManager
from database.models import GoodsCommit, Order, ProductKnowledge, Shop
from core.di_container import container
from core import order_lookup
from bridge.context import Context, ContextType, ChannelType
from Message.handlers import voucher_handler as vh
import Channel.pinduoduo.utils.API.gsxunlocking_api as gsx_api

SHOP_ID, USER_ID, CUST_UID = "SHOP1", "ACC1", "CUST1"
SERIAL = "VQ627F3QQH"
ORDER_SN = "261029-401373929091626"
GOODS_ID = "328045268438"
SPEC_BOUGHT = "特惠激活锁"

_results = {"pass": 0, "fail": 0}
REAL_PENDING_SUMMARY = None   # Harness 会用 stub 覆盖模块函数，这里留一份真的


def check(name, ok, detail=""):
    if ok:
        _results["pass"] += 1
        print("  [PASS] %s" % name)
    else:
        _results["fail"] += 1
        print("  [FAIL] %s %s" % (name, detail))


def make_context(content, ctype=ContextType.TEXT):
    return Context.create_pinduoduo_context(
        content=content, msg_id="msg", from_uid=CUST_UID, to_uid="cs", nickname="n",
        user_msg_type=ctype, shop_id=SHOP_ID, user_id=USER_ID,
        username="u", channel_type=ChannelType.PINDUODUO)


def make_db():
    tmpdir = tempfile.mkdtemp(prefix="ship_guard_")
    db = DatabaseManager(db_path=os.path.join(tmpdir, "test.db"))
    container.register_singleton(DatabaseManager, instance=db)
    with db.session_scope() as session:
        session.add(GoodsCommit(shop_id="2", goods_id=GOODS_ID, sku_id="1075903187640",
                                spec_name=SPEC_BOUGHT, server_id=10105,
                                zhenxin_server_id=1014, device_type="apple"))
        # 商品配成「仅发货」，handle() 才能判定 ship_branch
        shop = Shop(channel_id=1, shop_id=SHOP_ID, shop_name="测试店")
        session.add(shop)
        session.flush()
        session.add(ProductKnowledge(shop_id=shop.id, goods_id=GOODS_ID,
                                     goods_name="苹果gsx验机", service_mode=1))
    return db, tmpdir


def add_order(db, order_sn, status=0, sn=SERIAL, shipped=False):
    """shipped=True 表示「已发货完成」：新语义用 status=4 + shipped_at（status=1 只代表核销成功）"""
    from datetime import datetime as _dt

    with db.session_scope() as session:
        order = Order(shop_id=SHOP_ID, session_id=CUST_UID, sn_imei=sn, coupons="",
                      order_sn=order_sn,
                      status=4 if shipped else status,
                      shipped_at=_dt.now() if shipped else None)
        session.add(order)
        session.flush()
        return order.id


def order_status(db, order_id):
    with db.session_scope() as session:
        order = session.query(Order).filter_by(id=order_id).first()
        return order.status if order else None


class Harness:
    def __init__(self):
        self.ship_calls = []
        self.gsx_calls = []
        self.reports = []
        self.sent = []
        self.pending_summary = {"status": "empty", "orders": []}
        self.ship_result = {"success": True, "result_code": 100}
        self.detail = {"pending_shipment": True, "goods_id": GOODS_ID,
                       "goods_name": "苹果gsx验机", "spec": SPEC_BOUGHT}
        self.ship_delay = 0.0
        self.h = vh.VoucherDetectionHandler()

        def fake_ship(order_sn, shop_id, user_id):
            self.ship_calls.append(order_sn)
            if self.ship_delay:
                time.sleep(self.ship_delay)
            return dict(self.ship_result)

        async def fake_sender(serial, goods_id, spec, report_data, order_id,
                              shop_id, user_id, from_uid, voucher_success_text=""):
            self.reports.append(report_data)
            if report_data:
                # 与线上发送器一致：报告真正发出后打防重复标记
                self.h._mark_report_sent(shop_id, from_uid, serial)

        async def fake_send(shop_id, user_id, from_uid, text):
            self.sent.append(text)
            return True

        self.h._ship_via_platform = fake_ship
        self.h._query_gsxunlocking_and_send = fake_sender
        self.h._query_gsx_and_send = fake_sender
        self.h._send = fake_send
        self.h._record_message = lambda *a, **k: None
        self.h._pick_pending_reference = lambda *a, **k: None   # 别联网
        order_lookup.common_pending_goods = lambda *a, **k: None
        global REAL_PENDING_SUMMARY
        if REAL_PENDING_SUMMARY is None:
            REAL_PENDING_SUMMARY = order_lookup.pending_orders_summary
        order_lookup.pending_orders_summary = lambda *a, **k: dict(self.pending_summary)
        order_lookup.get_order_detail = lambda *a, **k: self.detail
        gsx_api.query_gsxunlocking = lambda sn, srv: (
            self.gsx_calls.append(srv), {"success": True, "result": "报告"})[1]

    def reset(self):
        self.ship_calls.clear()
        self.gsx_calls.clear()
        self.reports.clear()
        self.sent.clear()
        self.pending_summary = {"status": "empty", "orders": []}
        self.ship_result = {"success": True, "result_code": 100}
        vh._serial_confirm_pending.clear()
        vh._serial_confirmed.clear()
        vh._report_sent.clear()
        self.detail = {"pending_shipment": True, "goods_id": GOODS_ID,
                       "goods_name": "苹果gsx验机", "spec": SPEC_BOUGHT}
        self.ship_delay = 0.0

    def clear_calls(self):
        """只清调用记录，保留「已确认序列号」这类状态"""
        self.ship_calls.clear()
        self.gsx_calls.clear()
        self.reports.clear()
        self.sent.clear()

    async def ship(self, sn=SERIAL, order_sn=ORDER_SN, order_id=None, spec=""):
        return await self.h._do_ship_locked(
            sn, order_sn, SHOP_ID, USER_ID, CUST_UID, order_id,
            goods_id=GOODS_ID, spec=spec)

    async def send(self, content, image_sn=None, image=False):
        meta = {"shop_id": SHOP_ID, "user_id": USER_ID, "from_uid": CUST_UID,
                "goods_id": GOODS_ID}
        if image:
            meta["_image_sn"] = image_sn
            meta["_image_coupon"] = None
        return await self.h.handle(make_context(content), meta)

    def last(self) -> str:
        return self.sent[-1] if self.sent else ""


async def main():
    db, tmpdir = make_db()
    h = Harness()

    print("G1 缺序列号")
    h.reset()
    reply, status = await h.ship(sn="", order_sn=ORDER_SN)
    check("G1 不调发货接口", h.ship_calls == [], h.ship_calls)
    check("G1 回序列号引导", "序列号" in (reply or ""), reply)

    print("G2 缺订单号")
    h.reset()
    reply, status = await h.ship(order_sn="")
    check("G2 不调发货接口", h.ship_calls == [], h.ship_calls)
    check("G2 回「先发订单信息」", "订单" in (reply or ""), reply)
    h.reset()
    shipped_id = add_order(db, ORDER_SN, shipped=True)
    reply, status = await h.ship(order_sn="", order_id=shipped_id)
    check("G2 该单已发货时不再索要订单号", "已经发货完成" in (reply or ""), reply)
    check("G2 不主动发历史报告", h.reports == [], h.reports)
    check("G2 提示要报告再开口", "重发" in (reply or "") or "查报告" in (reply or ""), reply)
    check("G2 仍未调发货接口", h.ship_calls == [], h.ship_calls)

    print("G3 订单号已发货成功（数据库判重）")
    h.reset()
    with db.session_scope() as session:  # 清掉本单号的历史行，避免干扰
        session.query(Order).filter(Order.order_sn == ORDER_SN).update({"order_sn": None})
    add_order(db, ORDER_SN, shipped=True)
    h.detail = None  # 规格也查不到：没有任何可查的口径
    reply, status = await h.ship(order_sn=ORDER_SN)
    check("G3 不重复发货", h.ship_calls == [], h.ship_calls)
    check("G3 回「之前已经发货完成」", "已经发货完成" in (reply or ""), reply)
    check("G3 不主动发历史报告", h.reports == [], h.reports)
    check("G3 也没乱查验机接口", h.gsx_calls == [], h.gsx_calls)
    with db.session_scope() as session:
        session.query(Order).filter(Order.order_sn == ORDER_SN).update({"order_sn": None})

    print("G4 本单已发货成功（order_id 判重）")
    h.reset()
    h.detail = None
    done_id = add_order(db, "261028-000000000000777", shipped=True)
    orig_already2 = h.h._order_already_shipped
    h.h._order_already_shipped = lambda *a, **k: False   # 单独验证 order_id 这道判重
    try:
        reply, status = await h.ship(order_sn="261028-000000000000777", order_id=done_id)
    finally:
        h.h._order_already_shipped = orig_already2
    check("G4 不重复发货", h.ship_calls == [], h.ship_calls)
    check("G4 同样只提示、不发报告", "已经发货完成" in (reply or "") and h.reports == [],
          reply)

    print("G5 订单不是「待发货」")
    h.reset()
    h.detail = {"pending_shipment": False, "goods_id": GOODS_ID,
                "goods_name": "苹果gsx验机", "spec": SPEC_BOUGHT,
                "order_status_str": "已发货"}
    reply, status = await h.ship()
    check("G5 拒绝发货（0 次接口调用）", h.ship_calls == [], h.ship_calls)
    check("G5 文案说明不是待发货", "不是「待发货」" in (reply or ""), reply)
    check("G5 状态为失败", status == vh._SHIP_FAILED, status)

    print("G6 并发重复推送同一单")
    h.reset()
    h.detail = {"pending_shipment": True, "goods_id": GOODS_ID,
                "goods_name": "苹果gsx验机", "spec": SPEC_BOUGHT}
    h.ship_delay = 0.2
    orig_already = h.h._order_already_shipped   # 模拟两条消息都在写库前涌入
    orig_by_id = h.h._already_shipped
    orig_update = h.h._update_order_status
    h.h._order_already_shipped = lambda *a, **k: False
    h.h._already_shipped = lambda *a, **k: False
    h.h._update_order_status = lambda *a, **k: None
    try:
        tasks = [h.h._do_ship(SERIAL, ORDER_SN, SHOP_ID, USER_ID, CUST_UID, None,
                              goods_id=GOODS_ID, spec=SPEC_BOUGHT) for _ in range(3)]
        results = await asyncio.gather(*tasks)
    finally:
        h.h._order_already_shipped = orig_already
        h.h._already_shipped = orig_by_id
        h.h._update_order_status = orig_update
    check("G6 只发 1 次货", h.ship_calls == [ORDER_SN], h.ship_calls)
    inflight = sum(1 for r, _ in results if r == vh._SHIP_IN_FLIGHT_TEXT)
    check("G6 其余请求被在途占位拦下", inflight == 2, (inflight, results))

    print("G7 规格未知：不换套餐查（本次修复）")
    h.reset()
    h.detail = None  # 平台限流，规格拿不到
    reply, status = await h.ship(spec="")
    check("G7 发货照常 1 次", h.ship_calls == [ORDER_SN], h.ship_calls)
    check("G7 验机 0 次（不顶替全面验机）", h.gsx_calls == [], h.gsx_calls)
    check("G7 改要订单卡片/订单号核对规格", "订单" in (reply or ""), reply)

    print("G8 平台发货失败")
    h.reset()
    h.ship_result = {"success": False, "error_msg": "订单状态异常"}
    fail_id = add_order(db, ORDER_SN, status=0)
    reply, status = await h.ship(order_id=fail_id)
    check("G8 文案回发货失败", "发货失败" in (reply or ""), reply)
    check("G8 不查报告", h.gsx_calls == [] and h.reports == [], (h.gsx_calls, h.reports))
    check("G8 订单落失败状态(2)", order_status(db, fail_id) == 2, order_status(db, fail_id))

    print("G9 订单详情被限流：发货照做，但序列号要客户确认后才查")
    h.reset()
    h.detail = None
    reply, status = await h.ship(spec=SPEC_BOUGHT, order_id=None)
    check("G9 仍完成发货", h.ship_calls == [ORDER_SN], h.ship_calls)
    check("G9 未确认前不查验机", h.gsx_calls == [], h.gsx_calls)
    check("G9 要求客户核对序列号", "确认" in (h.last() or "") and SERIAL in (h.last() or ""),
          h.last())
    await h.send("确认")
    check("G9 确认后用 1014 查", h.gsx_calls == ["1014"], h.gsx_calls)
    check("G9 报告已交给报告链", h.reports == ["报告"], h.reports)

    print("G10 已发货但没出报告：客户再发序列号先不问，等他要了才查")
    h.reset()
    h.detail = {"pending_shipment": True, "goods_id": GOODS_ID,
                "goods_name": "苹果gsx验机", "spec": SPEC_BOUGHT}
    no_report_id = add_order(db, ORDER_SN, shipped=True)  # 上次已发货但规格没核对上，库里没有报告
    reply, status = await h.ship(order_sn=ORDER_SN, order_id=no_report_id, spec="")
    check("G10 不重复发货", h.ship_calls == [], h.ship_calls)
    check("G10 不主动补查", h.gsx_calls == [], h.gsx_calls)
    check("G10 提示「查报告」", "查报告" in (reply or ""), reply)
    await h.send("查报告")
    check("G10 客户要了才用 1014 查", h.gsx_calls == ["1014"], h.gsx_calls)
    check("G10 报告已补发", h.reports == ["报告"], h.reports)

    print("G11 已发货且本地已有报告：不主动重发，客户说「重发」才发")
    h.reset()
    with db.session_scope() as session:
        row = session.query(Order).filter_by(id=no_report_id).first()
        row.gsx_result = "历史报告内容"
    reply, status = await h.ship(order_sn=ORDER_SN, order_id=no_report_id, spec=SPEC_BOUGHT)
    check("G11 不重复发货", h.ship_calls == [], h.ship_calls)
    check("G11 不主动重发历史报告", h.reports == [], h.reports)
    check("G11 提示「重发」", "重发" in (reply or ""), reply)
    check("G11 提示里带复购引导", "再拍一单" in (reply or ""), reply)
    await h.send("重发")
    check("G11 要了才重发", h.reports == ["历史报告内容"], h.reports)
    check("G11 重发不查接口", h.gsx_calls == [], h.gsx_calls)

    print("G12 已发货单：有报告只提示「重发」，没报告只提示「查报告」（都不主动发/不查）")
    h.reset()
    reply = await h.h._reply_for_shipped_order(
        SERIAL, {"order_id": no_report_id, "gsx_result": "历史报告内容", "sku_id": ""},
        SHOP_ID, USER_ID, CUST_UID, ORDER_SN, GOODS_ID, SPEC_BOUGHT)
    check("G12a 有报告也只提示不发", h.reports == [] and "重发" in (reply or ""),
          (h.reports, reply))
    check("G12a 不打验机接口", h.gsx_calls == [], h.gsx_calls)

    h.reset()
    h.detail = None
    reply = await h.h._reply_for_shipped_order(
        SERIAL, {"order_id": no_report_id, "gsx_result": "", "sku_id": ""},
        SHOP_ID, USER_ID, CUST_UID, ORDER_SN, GOODS_ID, "")
    check("G12b 没报告提示「查报告」", "查报告" in (reply or ""), reply)
    check("G12b 也带复购引导", "再拍一单" in (reply or ""), reply)
    check("G12b 也不打验机接口", h.gsx_calls == [], h.gsx_calls)

    print("G13 截图序列号 + 后台有唯一待发货单（客户已有该单记录）：先请客户确认「序列号+订单」，确认后才发货")
    with db.session_scope() as session:
        session.query(Order).delete()
    h.reset()
    add_order(db, ORDER_SN, status=0, sn="")   # 订单卡片已推进会话（归属证据）
    h.pending_summary = {"status": "unique", "orders": [{
        "order_sn": ORDER_SN, "goods_id": GOODS_ID, "spec": SPEC_BOUGHT,
        "goods_name": "苹果gsx验机"}]}
    await h.send("（关于本机截图）", image_sn=SERIAL, image=True)
    check("G13 未确认前不发货", h.ship_calls == [], h.ship_calls)
    check("G13 未确认前不查验机", h.gsx_calls == [], h.gsx_calls)
    check("G13 请客户核对序列号和订单",
          "确认" in (h.last() or "") and SERIAL in (h.last() or "")
          and ORDER_SN in (h.last() or ""), h.last())
    await h.send("确认")
    check("G13 确认后才发货 1 次", h.ship_calls == [ORDER_SN], h.ship_calls)
    check("G13 确认后按该单规格用 1014 查", h.gsx_calls == ["1014"], h.gsx_calls)

    print("G14 截图序列号 + 后台没有待发货单：不查不发，告知没有单并要订单卡片")
    with db.session_scope() as session:
        session.query(Order).delete()
    h.reset()
    h.pending_summary = {"status": "empty", "orders": []}
    await h.send("（关于本机截图）", image_sn=SERIAL, image=True)
    check("G14 不发货", h.ship_calls == [], h.ship_calls)
    check("G14 不查验机接口", h.gsx_calls == [], h.gsx_calls)
    check("G14 告知后台没有待发货单", "没有待发货" in (h.last() or ""), h.last())
    check("G14 要订单卡片/订单号", "订单" in (h.last() or ""), h.last())
    check("G14 不出现「确认后马上」这种话术", "确认后马上" not in (h.last() or ""), h.last())

    print("G15 截图序列号 + 后台被限流查不到：不查不发、不瞎下结论")
    h.reset()
    h.pending_summary = {"status": "unavailable", "orders": []}
    await h.send("（关于本机截图）", image_sn=SERIAL, image=True)
    check("G15 不发货", h.ship_calls == [], h.ship_calls)
    check("G15 不查验机接口", h.gsx_calls == [], h.gsx_calls)
    check("G15 不谎称「没有待发货单」", "没有待发货" not in (h.last() or ""), h.last())
    check("G15 要订单卡片/订单号", "订单" in (h.last() or ""), h.last())

    print("G16 文字序列号但规格核对不上：改要订单，不再让客户确认")
    with db.session_scope() as session:
        session.query(Order).delete()
    h.reset()
    h.pending_summary = {"status": "empty", "orders": []}
    await h.send("序列号 %s" % SERIAL)
    check("G16 不查（规格没对上）", h.gsx_calls == [], h.gsx_calls)
    check("G16 告知后台待发货单情况", "已查后台" in (h.last() or ""), h.last())
    check("G16 要订单卡片/订单号", "订单" in (h.last() or ""), h.last())
    check("G16 不出现「确认后马上」这种话术", "确认后马上" not in (h.last() or ""), h.last())

    print("G17 客户自己打字「序列号 + 订单号」：发货照做，查前也要确认")
    with db.session_scope() as session:
        session.query(Order).delete()
    h.reset()
    await h.send("序列号 %s 订单号 %s" % (SERIAL, ORDER_SN))
    check("G17 文字序列号照常发货", h.ship_calls == [ORDER_SN], h.ship_calls)
    check("G17 未确认前不查", h.gsx_calls == [], h.gsx_calls)
    check("G17 请客户确认序列号", "确认" in (h.last() or ""), h.last())
    await h.send("确认")
    check("G17 确认后用客户买的 1014 查", h.gsx_calls == ["1014"], h.gsx_calls)

    print("G18 重复发同一序列号（历史单）：只提示，不查不发")
    h.clear_calls()
    reply, _st = await h.h._do_ship(SERIAL, ORDER_SN, SHOP_ID, USER_ID, CUST_UID, None,
                                    goods_id=GOODS_ID, spec=SPEC_BOUGHT)
    check("G18 不重复查接口", h.gsx_calls == [], h.gsx_calls)
    check("G18 不重复发报告", h.reports == [], h.reports)
    check("G18 回提示", "已经发货完成" in (reply or ""), reply)

    print("G19 同一序列号重复推消息：不主动发历史报告，只提示")
    h.reset()
    with db.session_scope() as session:
        session.query(Order).delete()
    rep_id = add_order(db, ORDER_SN, shipped=True)   # 已发货完成 = status 4 + shipped_at
    with db.session_scope() as session:
        session.query(Order).filter_by(id=rep_id).first().gsx_result = "报告内容"
    reply, _st = await h.ship(order_sn=ORDER_SN, order_id=rep_id, spec=SPEC_BOUGHT)
    check("G19 第一次不主动发报告", h.reports == [], h.reports)
    check("G19 第一次只提示「重发」", "重发" in (reply or ""), reply)
    h.clear_calls()
    reply, _st = await h.ship(order_sn=ORDER_SN, order_id=rep_id, spec=SPEC_BOUGHT)
    check("G19 第二次仍不主动发报告", h.reports == [], h.reports)
    check("G19 第二次仍只提示「重发」", "重发" in (reply or ""), reply)

    print("G20 客户回「重发」：才把报告发出去")
    h.clear_calls()
    await h.send("重发")
    check("G20 重发成功", h.reports == ["报告内容"], h.reports)

    print("G21 仅查询模式下同一序列号重复发：不重复查接口")
    h.reset()
    with db.session_scope() as session:
        session.query(Order).delete()
    h.h._mark_serial_confirmed(SHOP_ID, CUST_UID, SERIAL)
    await h.h._query_report_only(SERIAL, GOODS_ID, SPEC_BOUGHT, SHOP_ID, USER_ID, CUST_UID, None)
    check("G21 第一次查接口", h.gsx_calls == ["1014"], h.gsx_calls)
    h.clear_calls()
    await h.h._query_report_only(SERIAL, GOODS_ID, SPEC_BOUGHT, SHOP_ID, USER_ID, CUST_UID, None)
    check("G21 第二次不查接口", h.gsx_calls == [], h.gsx_calls)
    check("G21 第二次回「刚发过」", "刚发过" in (h.last() or ""), h.last())

    print("G22 报告发完的固定话术要带「请确认收货」")
    check("G22 话术含「亲，请确认收货」", "亲，请确认收货" in vh._FIXED_REVIEW_TEXT,
          vh._FIXED_REVIEW_TEXT)
    check("G22 保留原好评话术", "五行好平" in vh._FIXED_REVIEW_TEXT, vh._FIXED_REVIEW_TEXT)

    print("G23 待发货判定走订单列表（合并发货页为空时不能误判成没有单）")
    import Channel.pinduoduo.utils.API.order_api as oa

    class FakeAPI:
        def __init__(self, *a, **k):
            pass

        def list_orders(self, **k):
            return {"success": True, "total": 2, "error_msg": "", "orders": [
                {"order_sn": "261030-000000000000001", "pending_shipment": True,
                 "goods_id": GOODS_ID, "spec": SPEC_BOUGHT},
                {"order_sn": "261030-000000000000002", "pending_shipment": False},
            ]}

        def list_pending_shipment_orders(self, **k):
            return {"success": True, "total": 0, "orders": [], "error_msg": ""}

    class FailAPI:
        def __init__(self, *a, **k):
            pass

        def list_orders(self, **k):
            return {"success": False, "total": 0, "orders": [], "error_msg": "会话已过期"}

        def list_pending_shipment_orders(self, **k):
            return {"success": False, "total": 0, "orders": [], "error_msg": "会话已过期"}

    orig_api = oa.MmsOrderAPI
    try:
        oa.MmsOrderAPI = FakeAPI
        order_lookup._cache.clear()
        order_lookup._cooldown_until.clear()
        order_lookup._last_call_ts.clear()
        summary = REAL_PENDING_SUMMARY(SHOP_ID, USER_ID)
        check("G23 从订单列表识别出待发货单",
              summary["status"] == "unique"
              and summary["orders"][0]["order_sn"] == "261030-000000000000001", summary)

        oa.MmsOrderAPI = FailAPI
        order_lookup._cache.clear()
        order_lookup._cooldown_until.clear()
        order_lookup._last_call_ts.clear()
        summary = REAL_PENDING_SUMMARY(SHOP_ID, USER_ID)
        check("G23 两个接口都失败时判为查不到（不谎称没有单）",
              summary["status"] == "unavailable", summary)
    finally:
        oa.MmsOrderAPI = orig_api

    print("G24 认领订单的优先级：本地 > 平台；本地全是已发货老单 → 请他发新单号")
    h.reset()
    with db.session_scope() as session:
        session.query(Order).delete()
    add_order(db, ORDER_SN, status=0)
    h.detail = {"pending_shipment": True, "goods_id": GOODS_ID,
                "goods_name": "苹果gsx验机", "spec": SPEC_BOUGHT}
    # 平台唯一一笔（带商品/规格）→ 直接用平台的，本地行只提供 order_id 绑定
    h.pending_summary = {"status": "unique", "orders": [
        {"order_sn": ORDER_SN, "goods_id": GOODS_ID, "spec": SPEC_BOUGHT,
         "goods_name": "苹果gsx验机"}]}
    r = await h.h._resolve_customer_ship_order(SHOP_ID, USER_ID, CUST_UID, SERIAL)
    check("G24 平台唯一时用平台那笔（并绑本地行）",
          r["status"] == "one" and r["source"] == "platform"
          and r["order"]["order_sn"] == ORDER_SN and r["order"].get("order_id"), r)

    # 平台多笔（都是这个客户的） → 按【最新一单】让客户确认（确认信息里写明单号）
    extra_sn = "261030-000000000000031"
    add_order(db, extra_sn, status=0)
    add_order(db, "261030-000000000000041", status=0, sn="")
    add_order(db, "261030-000000000000042", status=0, sn="")
    h.pending_summary = {"status": "mixed", "orders": [
        {"order_sn": "261030-000000000000041", "goods_id": GOODS_ID, "spec": SPEC_BOUGHT,
         "goods_name": "苹果gsx验机", "order_time": 1000},
        {"order_sn": "261030-000000000000042", "goods_id": GOODS_ID, "spec": SPEC_BOUGHT,
         "goods_name": "苹果gsx验机", "order_time": 2000},
    ]}
    r = await h.h._resolve_customer_ship_order(SHOP_ID, USER_ID, CUST_UID, SERIAL)
    check("G24 平台多笔 → 按最新一单让客户确认",
          r["status"] == "one" and r.get("newest") is True
          and r["order"]["order_sn"] == "261030-000000000000042", r)

    # 平台多笔，但客户自己只有一笔刚下的单 → 用他自己那笔（多用户不串单）
    with db.session_scope() as s:
        s.query(Order).filter(Order.order_sn.in_([
            extra_sn, "261030-000000000000041", "261030-000000000000042"])).delete(
            synchronize_session=False)
    r = await h.h._resolve_customer_ship_order(SHOP_ID, USER_ID, CUST_UID, SERIAL)
    check("G24 平台多笔但客户自己只有一笔 → 用他这笔",
          r["status"] == "one" and r["source"] == "local"
          and r["order"]["order_sn"] == ORDER_SN, r)

    # 把本地那笔单挪到 3 小时前（真正的旧单，超出续接窗口）
    from datetime import datetime as _dt0, timedelta as _td0
    with db.session_scope() as session:
        row = session.query(Order).filter(Order.order_sn == ORDER_SN).first()
        if row:
            row.created_at = _dt0.now() - _td0(hours=3)
    h.pending_summary = {"status": "empty", "orders": []}
    r = await h.h._resolve_customer_ship_order(SHOP_ID, USER_ID, CUST_UID, SERIAL)
    check("G24 平台没单且本地只有旧单 → stale", r["status"] == "stale", r)

    # 平台查不到（限流）时：只认能被平台核实的本地单
    h.pending_summary = {"status": "unavailable", "orders": []}
    h.detail = {"pending_shipment": True, "goods_id": GOODS_ID,
                "goods_name": "苹果gsx验机", "spec": SPEC_BOUGHT}
    r = await h.h._resolve_customer_ship_order(SHOP_ID, USER_ID, CUST_UID, SERIAL)
    check("G24 限流时用能核实的本地单",
          r["status"] == "one" and r["source"] == "local", r)

    h.detail = None   # 详情也查不到 → 旧单不能拿来顶
    from datetime import datetime as _dt, timedelta as _td
    with db.session_scope() as session:
        row = session.query(Order).filter(Order.order_sn == ORDER_SN).first()
        if row:
            row.created_at = _dt.now() - _td(hours=3)
    r = await h.h._resolve_customer_ship_order(SHOP_ID, USER_ID, CUST_UID, SERIAL)
    check("G24 限流+详情查不到+旧单 → 不自动用旧单（unavailable）",
          r["status"] == "unavailable", r)

    shutil.rmtree(tmpdir, ignore_errors=True)
    print("\n合计: %d 通过 / %d 失败" % (_results["pass"], _results["fail"]))
    return 0 if _results["fail"] == 0 else 1


sys.exit(asyncio.run(main()))
