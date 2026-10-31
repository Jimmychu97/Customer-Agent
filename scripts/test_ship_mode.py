# -*- coding: utf-8 -*-
"""无物流发货分支 / 三态服务模式 自测脚本（离线，不联网）

覆盖场景：
1. mode=0（卡券核销）：序列号+卡券 -> 走核销，发货接缝 0 次调用
2. mode=1（仅发货）：序列号+订单号 -> 走发货，核销 0 次调用
3. mode=1：只给卡券 -> 不核销、不发请求，回「无需卡券」提示
4. mode=2（二选一兼容）：给卡券 -> 核销；只给订单号 -> 发货
5. 订单号不会被误识别为卡券号（_extract_coupon / _extract_order_sn 互斥）
6. 重复触发：同一订单已 status=1 且 order_sn 一致时，发货接缝最多调用 1 次
7. 接缝默认实现：不替换 _ship_via_platform 时回「尚未配置」且不抛未捕获异常
8. 订单卡先到、序列号后到：复用同一单，发货带的是订单卡里的订单号

运行：uv run python scripts/test_ship_mode.py
"""
import asyncio
import json
import os
import sys
import tempfile
import shutil
import types

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

# 沙箱里 loguru 的 enqueue 需要命名管道，离线自测关掉异步写日志
import loguru

_o = loguru.logger.add
loguru.logger.add = lambda *a, **k: (k.__setitem__("enqueue", False), _o(*a, **k))[1]

from database.db_manager import DatabaseManager
from database.models import Order, Shop, ProductKnowledge
from core.di_container import container
from bridge.context import Context, ContextType, ChannelType
from Message.handlers import voucher_handler as vh

SHOP_ID = "SHOP1"
USER_ID = "ACC1"
CUST_UID = "CUST1"
SERIAL = "ABCDE12345"
COUPON = "123456789012"
ORDER_SN = "261028-423740065360537"
GOODS_VOUCHER, GOODS_SHIP, GOODS_AUTO = 1001, 1002, 1003
META = {"shop_id": SHOP_ID, "user_id": USER_ID, "from_uid": CUST_UID}

# core.service_mode 契约：优先用真实模块；不存在时注入等价替身，保证本脚本可独立运行
_STUB_MODES = {}
try:
    from core.service_mode import (
        SERVICE_MODE_VOUCHER, SERVICE_MODE_SHIP, SERVICE_MODE_AUTO,  # noqa: F401
    )
    CONTRACT_REAL = True
except Exception:  # pragma: no cover - 仅在契约模块缺失时走到
    CONTRACT_REAL = False
    SERVICE_MODE_VOUCHER, SERVICE_MODE_SHIP, SERVICE_MODE_AUTO = 0, 1, 2

    def _stub_resolve(shop_platform_id, goods_id):
        try:
            return _STUB_MODES.get((str(shop_platform_id), int(goods_id)), SERVICE_MODE_VOUCHER)
        except Exception:
            return SERVICE_MODE_VOUCHER

    _mod = types.ModuleType("core.service_mode")
    _mod.SERVICE_MODE_VOUCHER = SERVICE_MODE_VOUCHER
    _mod.SERVICE_MODE_SHIP = SERVICE_MODE_SHIP
    _mod.SERVICE_MODE_AUTO = SERVICE_MODE_AUTO
    _mod.SERVICE_MODE_LABELS = {0: "卡券核销", 1: "仅发货", 2: "二选一兼容"}
    _mod.resolve_service_mode = _stub_resolve
    _mod.describe_service_mode = lambda m: _mod.SERVICE_MODE_LABELS.get(m, "未知")
    _mod.normalize_service_mode = lambda v: v if v in _mod.SERVICE_MODE_LABELS else SERVICE_MODE_VOUCHER
    sys.modules["core.service_mode"] = _mod

_results = {"pass": 0, "fail": 0, "skip": 0}
# 跨代理契约列：另一子代理在 database/models.py 新增；缺失时相关断言跳过而非误判失败
HAS_ORDER_SN_COL = True
HAS_SERVICE_MODE_COL = True


def check(name: str, ok: bool, detail: str = "", skip: bool = False) -> None:
    if skip:
        _results["skip"] += 1
        print("  [SKIP] %s %s" % (name, detail))
        return
    if ok:
        _results["pass"] += 1
        print("  [PASS] %s" % name)
    else:
        _results["fail"] += 1
        print("  [FAIL] %s %s" % (name, detail))


def make_tmp_db():
    tmpdir = tempfile.mkdtemp(prefix="ship_mode_")
    db = DatabaseManager(db_path=os.path.join(tmpdir, "test.db"))
    container.register_singleton(DatabaseManager, instance=db)
    return db, tmpdir


def make_context(content, ctype: ContextType = ContextType.TEXT) -> Context:
    return Context.create_pinduoduo_context(
        content=content,
        msg_id="msg",
        from_uid=CUST_UID,
        to_uid="cs",
        nickname="n",
        user_msg_type=ctype,
        shop_id=SHOP_ID,
        user_id=USER_ID,
        username="u",
        channel_type=ChannelType.PINDUODUO,
    )


def set_mode(db, mode: int, goods_id: int) -> None:
    """写入商品级服务模式（真实契约走数据库，替身契约同时写内存表）"""
    _STUB_MODES[(SHOP_ID, goods_id)] = mode
    try:
        with db.session_scope() as session:
            shop = session.query(Shop).filter(Shop.shop_id == SHOP_ID).first()
            if shop is None:
                shop = Shop(channel_id=1, shop_id=SHOP_ID, shop_name="测试店")
                session.add(shop)
                session.flush()
            row = session.query(ProductKnowledge).filter_by(shop_id=shop.id, goods_id=goods_id).first()
            if row is None:
                session.add(ProductKnowledge(shop_id=shop.id, goods_id=goods_id,
                                             goods_name="测试商品", service_mode=mode))
            else:
                row.service_mode = mode
    except Exception as exc:
        print("  [WARN] 写入商品服务模式失败（当前用例将依赖替身）: %s" % exc)


class Harness:
    """构造一个只做记录、不联网的处理器"""

    def __init__(self, db):
        self.db = db
        self.sent = []
        self.verify_calls = []
        self.ship_calls = []
        self.ship_result = {"success": True, "error_msg": ""}
        self.handler = vh.VoucherDetectionHandler()

        async def fake_send(shop_id, user_id, from_uid, text):
            self.sent.append(text)
            return True

        async def fake_verify(sn, coupon, shop_id, user_id, from_uid, order_id):
            self.verify_calls.append({"sn": sn, "coupon": coupon, "order_id": order_id})
            self.handler._update_order_status(order_id, 1)
            return "核销成功！", "success"

        self.handler._send = fake_send
        self.handler._record_message = lambda *a, **k: None
        self.handler._do_verify = fake_verify

    def install_ship_stub(self) -> None:
        def fake_ship(order_sn, shop_id, user_id):
            self.ship_calls.append(order_sn)
            return dict(self.ship_result)

        self.handler._ship_via_platform = fake_ship

    def reset(self) -> None:
        with self.db.session_scope() as session:
            session.query(Order).delete()
        self.sent.clear()
        self.verify_calls.clear()
        self.ship_calls.clear()
        self.ship_result = {"success": True, "error_msg": ""}
        vh._ship_confirmed.clear()
        vh._serial_confirm_pending.clear()
        vh._serial_confirmed.clear()

    def orders(self):
        with self.db.session_scope() as session:
            return [
                (o.id, o.sn_imei or "", o.coupons or "", o.status,
                 getattr(o, "order_sn", None) or "")
                for o in session.query(Order).order_by(Order.id).all()
            ]

    def _auto_confirm_ship(self):
        """这些场景测的是发货/模式分派本身：先把「客户已确认序列号+订单」标记好，
        发货前确认的交互单独由 scenario_10 覆盖"""
        if getattr(self, "auto_confirm_ship", True):
            self.handler._mark_ship_confirmed(SHOP_ID, CUST_UID, SERIAL, ORDER_SN)

    async def send_text(self, content, goods_id=GOODS_SHIP, mode=None):
        if mode is not None:
            set_mode(self.db, mode, goods_id)
        self._auto_confirm_ship()
        meta = dict(META, goods_id=goods_id)
        return await self.handler.handle(make_context(content), meta)

    async def send_order_card(self, order_sn, goods_id=GOODS_SHIP, mode=None):
        if mode is not None:
            set_mode(self.db, mode, goods_id)
        self._auto_confirm_ship()
        card = json.dumps({"order_id": order_sn, "goods_id": goods_id,
                           "goods_name": "测试商品", "spec": "128G"}, ensure_ascii=False)
        return await self.handler.handle(
            make_context(card, ContextType.ORDER_INFO), dict(META, goods_id=goods_id)
        )

    def last(self) -> str:
        return self.sent[-1] if self.sent else ""


async def scenario_1(h: Harness):
    print("S1 mode=0：序列号+卡券 -> 走核销，不触发发货")
    h.reset()
    h.install_ship_stub()
    await h.send_text("序列号 %s 卡券 %s" % (SERIAL, COUPON), GOODS_VOUCHER, SERVICE_MODE_VOUCHER)
    check("S1: 核销被调用 1 次", len(h.verify_calls) == 1, h.verify_calls)
    check("S1: 发货接缝 0 次调用", len(h.ship_calls) == 0, h.ship_calls)
    rows = h.orders()
    check("S1: 建 1 条单且已核销", len(rows) == 1 and rows[0][3] == 1, rows)


async def scenario_2(h: Harness):
    print("S2 mode=1：序列号+订单号 -> 走发货，不触发核销")
    h.reset()
    h.install_ship_stub()
    await h.send_text("序列号 %s 订单号 %s" % (SERIAL, ORDER_SN), GOODS_SHIP, SERVICE_MODE_SHIP)
    check("S2: 核销 0 次调用", len(h.verify_calls) == 0, h.verify_calls)
    check("S2: 发货接缝 1 次调用", h.ship_calls == [ORDER_SN], h.ship_calls)
    rows = h.orders()
    check("S2: 1 条单、已发货、订单号落库",
          len(rows) == 1 and rows[0][3] == 1 and rows[0][4] == ORDER_SN, rows,
          skip=not HAS_ORDER_SN_COL)
    check("S2: 回复含「发货成功」", "发货成功" in h.last(), h.last())


async def scenario_3(h: Harness):
    print("S3 mode=1：只给卡券 -> 不核销、不发请求，回提示语")
    h.reset()
    h.install_ship_stub()
    await h.send_text("卡券 %s" % COUPON, GOODS_SHIP, SERVICE_MODE_SHIP)
    check("S3: 核销 0 次调用", len(h.verify_calls) == 0, h.verify_calls)
    check("S3: 发货接缝 0 次调用", len(h.ship_calls) == 0, h.ship_calls)
    check("S3: 回复说明本商品不需要卡券", "不需要卡券" in h.last(), h.last())


async def scenario_4(h: Harness):
    print("S4 mode=2：给卡券走核销 / 只给订单号走发货")
    h.reset()
    h.install_ship_stub()
    await h.send_text("序列号 %s 卡券 %s" % (SERIAL, COUPON), GOODS_AUTO, SERVICE_MODE_AUTO)
    check("S4a: 有卡券 -> 核销 1 次", len(h.verify_calls) == 1, h.verify_calls)
    check("S4a: 有卡券 -> 发货 0 次", len(h.ship_calls) == 0, h.ship_calls)

    h.reset()
    h.install_ship_stub()
    await h.send_text("序列号 %s 订单号 %s" % (SERIAL, ORDER_SN), GOODS_AUTO, SERVICE_MODE_AUTO)
    check("S4b: 无卡券 -> 发货 1 次", h.ship_calls == [ORDER_SN], h.ship_calls)
    check("S4b: 无卡券 -> 核销 0 次", len(h.verify_calls) == 0, h.verify_calls)


async def scenario_5():
    print("S5 订单号不会被误识别为卡券号")
    check("S5: _extract_coupon(订单号) 为 None", vh._extract_coupon(ORDER_SN) is None, vh._extract_coupon(ORDER_SN))
    check("S5: _extract_order_sn(订单号) 命中", vh._extract_order_sn(ORDER_SN) == ORDER_SN, vh._extract_order_sn(ORDER_SN))
    check("S5: 卡券号不被识别为订单号", vh._extract_order_sn(COUPON) is None, vh._extract_order_sn(COUPON))
    mixed = "卡券 %s 订单号 %s" % (COUPON, ORDER_SN)
    check("S5: 混合文本两个都能各自提取",
          vh._extract_coupon(mixed) == COUPON and vh._extract_order_sn(mixed) == ORDER_SN,
          (vh._extract_coupon(mixed), vh._extract_order_sn(mixed)))


async def scenario_6(h: Harness):
    print("S6 重复触发：已发货的同一订单不再调用发货接口")
    h.reset()
    h.install_ship_stub()
    await h.send_text("序列号 %s 订单号 %s" % (SERIAL, ORDER_SN), GOODS_SHIP, SERVICE_MODE_SHIP)
    await h.send_text("序列号 %s 订单号 %s" % (SERIAL, ORDER_SN), GOODS_SHIP, SERVICE_MODE_SHIP)
    check("S6: 第二次不重复发货", len(h.ship_calls) <= 1, h.ship_calls)
    check("S6: 第二次提示「之前已经发货完成」", "已经发货完成" in h.last(), h.last())
    check("S6: 不主动重发历史报告", all("报告内容" not in t for t in h.sent), h.sent[-2:])
    check("S6: 仍 1 条单", len(h.orders()) == 1, h.orders())


async def scenario_7(h: Harness):
    print("S7 接缝兜底：接口实现缺失（NotImplementedError）时回「尚未配置」且不抛异常")
    h.reset()
    h.install_ship_stub()

    def raise_ni(order_sn, shop_id, user_id):
        raise NotImplementedError("simulated: 接口未实现")

    h.handler._ship_via_platform = raise_ni
    reply, status, crashed = "", "", ""
    try:
        reply, status = await h.handler._do_ship(SERIAL, ORDER_SN, SHOP_ID, USER_ID, CUST_UID, None)
    except Exception as exc:  # 不该发生
        crashed = repr(exc)
    check("S7: 未抛出未捕获异常", not crashed, crashed)
    check("S7: 返回「尚未配置」类文案", "尚未配置" in (reply or ""), reply)
    check("S7: 状态为 error", status == vh._SHIP_ERROR, status)


async def scenario_9(h: Harness):
    print("S9 发货流程里的验机：序列号需客户确认后才查")

    def confirm_serial():
        # 序列号/IMEI 现在必须客户确认后才查验机；确认交互由 scenario_10 / E2E 覆盖
        h.handler._mark_serial_confirmed(SHOP_ID, CUST_UID, SERIAL)

    # S9a 验机失败 -> 不发货、清空序列号
    h.reset()
    confirm_serial()
    h.install_ship_stub()
    h.handler._get_query_config = lambda gid, sp: {
        "server_id": None, "zhenxin_server_id": "1031", "device_type": "apple"}

    import Channel.pinduoduo.utils.API.gsxunlocking_api as gsxu
    orig_gsxu = gsxu.query_gsxunlocking
    gsxu.query_gsxunlocking = lambda imei, srv: {"success": False, "error_msg": "序列号不存在"}
    try:
        reply, status = await h.handler._do_ship(
            SERIAL, ORDER_SN, SHOP_ID, USER_ID, CUST_UID, None, goods_id=GOODS_SHIP)
    finally:
        gsxu.query_gsxunlocking = orig_gsxu
    check("S9a: 验机失败 -> 0 次发货", len(h.ship_calls) == 0, h.ship_calls)
    check("S9a: 回复要求重发序列号", "序列号" in (reply or "") and "有误" in (reply or ""), reply)

    # S9b 验机成功 -> 发货 1 次，report_data 非 None
    h.reset()
    confirm_serial()
    h.install_ship_stub()
    h.handler._get_query_config = lambda gid, sp: {
        "server_id": None, "zhenxin_server_id": "1031", "device_type": "apple"}
    captured = {}
    orig_sender = h.handler._query_gsxunlocking_and_send

    async def capture_sender(serial, goods_id, spec, report_data, order_id,
                             shop_id, user_id, from_uid, voucher_success_text=""):
        captured["report_data"] = report_data
        captured["text"] = voucher_success_text

    h.handler._query_gsxunlocking_and_send = capture_sender
    gsxu.query_gsxunlocking = lambda imei, srv: {"success": True, "result": {"model": "iPhone15,2"}}
    try:
        await h.handler._do_ship(
            SERIAL, ORDER_SN, SHOP_ID, USER_ID, CUST_UID, None, goods_id=GOODS_SHIP)
    finally:
        gsxu.query_gsxunlocking = orig_gsxu
    h.handler._query_gsxunlocking_and_send = orig_sender
    check("S9b: 验机成功 -> 发货 1 次", h.ship_calls == [ORDER_SN], h.ship_calls)
    check("S9b: report_data 非 None", captured.get("report_data") is not None, captured)

    # S9c 无验机配置 -> 照常发货，report_data 为 None，不抛异常
    h.reset()
    h.install_ship_stub()
    h.handler._get_query_config = lambda gid, sp: {
        "server_id": None, "zhenxin_server_id": None, "device_type": None}
    captured.clear()
    orig_sender2 = h.handler._query_gsx_and_send

    async def capture_sender2(serial, goods_id, spec, report_data, order_id,
                              shop_id, user_id, from_uid, voucher_success_text=""):
        captured["report_data"] = report_data

    h.handler._query_gsx_and_send = capture_sender2
    crashed = ""
    try:
        await h.handler._do_ship(
            SERIAL, ORDER_SN, SHOP_ID, USER_ID, CUST_UID, None, goods_id=GOODS_SHIP)
    except Exception as exc:
        crashed = repr(exc)
    finally:
        h.handler._query_gsx_and_send = orig_sender2
    check("S9c: 无验机配置仍发货", h.ship_calls == [ORDER_SN], h.ship_calls)
    check("S9c: report_data 为 None", captured.get("report_data") is None, captured)
    check("S9c: 未抛异常", not crashed, crashed)

    # S9d 验机接口异常 -> 不发货，且必须给客户回话（不能静默丢弃）
    h.reset()
    confirm_serial()
    h.install_ship_stub()
    h.handler._get_query_config = lambda gid, sp: {
        "server_id": None, "zhenxin_server_id": "1031", "device_type": "apple"}

    def boom(imei, srv):
        raise RuntimeError("验机接口超时")

    gsxu.query_gsxunlocking = boom
    try:
        reply, status = await h.handler._do_ship(
            SERIAL, ORDER_SN, SHOP_ID, USER_ID, CUST_UID, None, goods_id=GOODS_SHIP)
    finally:
        gsxu.query_gsxunlocking = orig_gsxu
    check("S9d: 验机异常 -> 0 次发货", len(h.ship_calls) == 0, h.ship_calls)
    check("S9d: 回复告知稍后再试", "稍后" in (reply or ""), reply)
    check("S9d: 状态为 error", status == vh._SHIP_ERROR, status)


async def scenario_8(h: Harness):
    print("S8 订单卡先到、序列号后到：复用同一单并带上订单号")
    h.reset()
    h.install_ship_stub()
    set_mode(h.db, SERVICE_MODE_SHIP, GOODS_SHIP)
    card = make_context(json.dumps({"order_id": ORDER_SN, "goods_id": GOODS_SHIP,
                                    "goods_name": "测试商品"}, ensure_ascii=False), ContextType.ORDER_INFO)
    check("S8: can_handle 接管发货商品的订单卡",
          h.handler.can_handle(card) is True, h.handler.can_handle(card))
    await h.send_order_card(ORDER_SN, GOODS_SHIP, SERVICE_MODE_SHIP)
    rows = h.orders()
    check("S8: 订单卡建 1 条单并写入订单号",
          len(rows) == 1 and rows[0][4] == ORDER_SN, rows, skip=not HAS_ORDER_SN_COL)
    check("S8: 引导客户提供序列号", "序列号" in h.last(), h.last())
    await h.send_text("序列号 %s" % SERIAL, GOODS_SHIP, SERVICE_MODE_SHIP)
    rows = h.orders()
    check("S8: 补序列号后仍 1 条单", len(rows) == 1, rows)
    check("S8: 发货带的是订单卡里的订单号", h.ship_calls == [ORDER_SN], h.ship_calls)
    check("S8: 序列号写入同一单", rows[0][1] == SERIAL, rows)


async def scenario_10(h: Harness):
    print("S10 发货前必须客户确认「序列号+订单」，确认后才发货")
    h.reset()
    h.install_ship_stub()
    # 验机现在在发货之前查：本场景只测确认闸门，验机与报告发送都打桩，别联网
    h.handler._get_query_config = lambda gid, sp: {
        "server_id": None, "zhenxin_server_id": "1031", "device_type": "apple"}

    import Channel.pinduoduo.utils.API.gsxunlocking_api as gsxu
    orig_gsxu = gsxu.query_gsxunlocking
    gsxu.query_gsxunlocking = lambda imei, srv: {"success": True, "result": {"model": "iPhone15,2"}}

    async def noop_sender(serial, goods_id, spec, report_data, order_id,
                          shop_id, user_id, from_uid, voucher_success_text=""):
        return None

    orig_sender = h.handler._query_gsxunlocking_and_send
    h.handler._query_gsxunlocking_and_send = noop_sender
    h.auto_confirm_ship = False
    try:
        await h.send_text("序列号 %s 订单号 %s" % (SERIAL, ORDER_SN), GOODS_SHIP, SERVICE_MODE_SHIP)
        check("S10 未确认前不调发货接口", h.ship_calls == [], h.ship_calls)
        check("S10 先发确认信息", "请核对本次查询信息" in h.last(), h.last())
        check("S10 确认信息带序列号和订单号",
              SERIAL in h.last() and ORDER_SN in h.last(), h.last())
        check("S10 提示回复「确认」", "确认" in h.last(), h.last())
        await h.send_text("确认", GOODS_SHIP, SERVICE_MODE_SHIP)
        check("S10 确认后才发货 1 次", h.ship_calls == [ORDER_SN], h.ship_calls)
    finally:
        gsxu.query_gsxunlocking = orig_gsxu
        h.handler._query_gsxunlocking_and_send = orig_sender
        h.auto_confirm_ship = True


async def main():
    global HAS_ORDER_SN_COL, HAS_SERVICE_MODE_COL
    db, tmpdir = make_tmp_db()
    print("契约模块 core.service_mode: %s" % ("真实模块" if CONTRACT_REAL else "注入替身"))
    with db.session_scope() as session:
        cols = [c.name for c in Order.__table__.columns]
    HAS_ORDER_SN_COL = "order_sn" in cols
    HAS_SERVICE_MODE_COL = "service_mode" in cols
    print("orders 表新列：order_sn=%s service_mode=%s"
          % (HAS_ORDER_SN_COL, HAS_SERVICE_MODE_COL))
    if not (HAS_ORDER_SN_COL and HAS_SERVICE_MODE_COL):
        print("  [SKIP] 契约列缺失，依赖订单号/service_mode 写库的断言将跳过")
    try:
        h = Harness(db)
        await scenario_1(h)
        await scenario_2(h)
        await scenario_3(h)
        await scenario_4(h)
        await scenario_5()
        await scenario_6(h)
        await scenario_7(h)
        await scenario_8(h)
        await scenario_9(h)
        await scenario_10(h)
    finally:
        try:
            db.engine.dispose()
        except Exception:
            pass
        shutil.rmtree(tmpdir, ignore_errors=True)

    print("")
    total = _results["pass"] + _results["fail"]
    print("总计 %d/%d 通过%s" % (
        _results["pass"], total,
        ("（跳过 %d 项，契约列缺失）" % _results["skip"]) if _results["skip"] else ""))
    return 0 if _results["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
