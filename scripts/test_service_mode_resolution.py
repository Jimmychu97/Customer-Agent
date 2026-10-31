# -*- coding: utf-8 -*-
"""服务模式判定自测：按「客户这一单」决定要卡券还是发序列号（离线，不联网）

覆盖：
1. 判定优先级矩阵：本条消息商品 > 已认领本人订单 > 订单详情 > 全店唯一待发货单 >
   待发货商品一致 > 历史商品卡 > 本单落库；全无线索判为「未确定」
2. 弱依据：只在历史消息里见过商品卡，不足以断定「这单一定要卡券」
3. R1 回归：客户上一单是核销商品、这一单是发货商品 → 不得据此索要卡券
4. R2 回归：别的客户的核销单不影响本客户的判定
5. 分派：仅发货/未确定商品的回复一律不含「卡券」；核销商品仍照旧要卡券

运行：uv run python scripts/test_service_mode_resolution.py
"""
import asyncio
import os
import sys
import tempfile
import shutil

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("LOG_FILE", "logs/test.log")
os.environ.setdefault("ORDER_BROWSER_FALLBACK", "0")
os.environ.setdefault("ORDER_HEARTBEAT", "0")
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

from database.db_manager import DatabaseManager            # noqa: E402
from database.models import Order, ProductKnowledge, Shop  # noqa: E402
from core.di_container import container                     # noqa: E402
from core import order_lookup                               # noqa: E402
from core.service_mode import (                             # noqa: E402
    EVIDENCE_CLAIMED_ORDER, EVIDENCE_HISTORY, EVIDENCE_LOCAL_ORDER,
    EVIDENCE_MESSAGE_ORDER, EVIDENCE_NONE, EVIDENCE_ORDER_DETAIL,
    EVIDENCE_PENDING_COMMON, EVIDENCE_PENDING_UNIQUE, EVIDENCE_UNSET,
    resolve_service_mode_with_evidence,
)
from bridge.context import Context, ContextType, ChannelType  # noqa: E402
from Message.handlers import voucher_handler as vh          # noqa: E402

SHOP_ID, USER_ID, CUST_UID, OTHER_UID = "SHOP1", "ACC1", "CUST1", "CUST2"
GOODS_VOUCHER, GOODS_SHIP, GOODS_UNSET = 2001, 2002, 2003
SERIAL = "ABCDE12345"
COUPON = "123456789012"
ORDER_SN = "261028-423740065360537"
OLD_ORDER_SN = "261027-111111111111"

_results = {"pass": 0, "fail": 0}


def check(name, ok, detail=""):
    if ok:
        _results["pass"] += 1
        print(f"  [PASS] {name}")
    else:
        _results["fail"] += 1
        print(f"  [FAIL] {name} {detail}")


def make_tmp_db():
    tmpdir = tempfile.mkdtemp(prefix="svc_mode_")
    db = DatabaseManager(db_path=os.path.join(tmpdir, "test.db"))
    container.register_singleton(DatabaseManager, instance=db)
    return db, tmpdir


def set_mode(db, goods_id: int, mode, shop_key: str = SHOP_ID) -> None:
    """写商品级服务模式；mode=None 表示「商品存在但没配模式」"""
    with db.session_scope() as session:
        shop = session.query(Shop).filter(Shop.shop_id == shop_key).first()
        if shop is None:
            shop = Shop(channel_id=1, shop_id=shop_key, shop_name="测试店")
            session.add(shop)
            session.flush()
        row = session.query(ProductKnowledge).filter_by(shop_id=shop.id, goods_id=goods_id).first()
        if row is None:
            session.add(ProductKnowledge(shop_id=shop.id, goods_id=goods_id,
                                        goods_name="测试商品", service_mode=mode))
        else:
            row.service_mode = mode


def add_order(db, order_sn, service_mode, session_id: str = CUST_UID, sn: str = "") -> None:
    with db.session_scope() as session:
        session.add(Order(shop_id=SHOP_ID, session_id=session_id, sn_imei=sn, coupons="",
                          status=0, order_sn=order_sn, service_mode=service_mode))


def make_context(content: str) -> Context:
    return Context.create_pinduoduo_context(
        content=content, msg_id="msg", from_uid=CUST_UID, to_uid="cs", nickname="n",
        user_msg_type=ContextType.TEXT, shop_id=SHOP_ID, user_id=USER_ID,
        username="u", channel_type=ChannelType.PINDUODUO,
    )


META = {"from_uid": CUST_UID, "shop_id": SHOP_ID, "user_id": USER_ID}


# ==============================================================================
# 1. 判定优先级矩阵
# ==============================================================================

def scenario_matrix(db):
    print("A 判定优先级矩阵")

    # 1) 本条消息自带商品 → 最高优先（即便已认领单是另一种模式）
    r = resolve_service_mode_with_evidence(
        SHOP_ID, message_goods_id=GOODS_SHIP, message_spec="特惠激活锁",
        claimed_order={"goods_id": GOODS_VOUCHER, "spec": ""},
        history_goods_id=GOODS_VOUCHER,
    )
    check("A1 本条消息商品优先", r.mode == 1 and r.evidence == EVIDENCE_MESSAGE_ORDER, r)
    check("A1 规格一并带出", r.spec == "特惠激活锁", r.spec)

    # 2) 已认领本人的订单
    r = resolve_service_mode_with_evidence(
        SHOP_ID, claimed_order={"goods_id": GOODS_VOUCHER, "order_sn": ORDER_SN})
    check("A2 已认领订单次之", r.mode == 0 and r.evidence == EVIDENCE_CLAIMED_ORDER, r)
    check("A2 订单号带出", r.order_sn == ORDER_SN, r.order_sn)

    # 3) 订单详情
    r = resolve_service_mode_with_evidence(
        SHOP_ID, order_sn=ORDER_SN, order_detail_goods_id=GOODS_SHIP,
        order_detail_spec="全面验机报告")
    check("A3 订单详情可用", r.mode == 1 and r.evidence == EVIDENCE_ORDER_DETAIL, r)

    # 4) 全店唯一待发货单
    r = resolve_service_mode_with_evidence(
        SHOP_ID, picked_order={"goods_id": GOODS_SHIP, "spec": "s", "order_sn": ORDER_SN})
    check("A4 唯一待发货单可用", r.mode == 1 and r.evidence == EVIDENCE_PENDING_UNIQUE, r)

    # 5) 待发货商品一致
    r = resolve_service_mode_with_evidence(
        SHOP_ID, common_goods={"goods_id": GOODS_VOUCHER, "spec": "s"})
    check("A5 待发货商品一致可用", r.mode == 0 and r.evidence == EVIDENCE_PENDING_COMMON, r)

    # 6) 历史商品卡 → 弱依据
    r = resolve_service_mode_with_evidence(SHOP_ID, history_goods_id=GOODS_VOUCHER)
    check("A6 历史商品卡是弱依据", r.mode == 0 and r.evidence == EVIDENCE_HISTORY and r.is_weak, r)
    r = resolve_service_mode_with_evidence(SHOP_ID, history_goods_id=GOODS_SHIP)
    check("A6 弱依据能支撑不索要卡券", r.mode == 1 and r.is_weak, r)

    # 7) 本单落库模式（由调用方判定过订单号对得上）
    r = resolve_service_mode_with_evidence(SHOP_ID, order_sn=ORDER_SN, local_order_mode=1)
    check("A7 本单落库兜底", r.mode == 1 and r.evidence == EVIDENCE_LOCAL_ORDER, r)

    # 8) 全无线索 → 未确定（不是卡券核销）
    r = resolve_service_mode_with_evidence(SHOP_ID, order_sn=ORDER_SN)
    check("A8 全无线索判为未确定", r.mode is None and r.evidence == EVIDENCE_NONE, r)
    check("A8 未确定不等于核销", not r.is_known and not r.is_weak, r)

    # 9) 「未配置」= 3（新语义）：新建商品没配模式时落 3，判定为未确定 → 走二选一，
    #    绝不索要卡券；存量显式 0 仍然是卡券核销（不动存量）。
    set_mode(db, GOODS_UNSET, None)
    with db.session_scope() as session:
        shop = session.query(Shop).filter(Shop.shop_id == SHOP_ID).first()
        row = session.query(ProductKnowledge).filter_by(
            shop_id=shop.id, goods_id=GOODS_UNSET).first()
        stored = row.service_mode if row else "缺行"
    check("A9 未配置落成 3", stored == 3, stored)
    r = resolve_service_mode_with_evidence(SHOP_ID, message_goods_id=GOODS_UNSET)
    check("A9 未配置判为未确定（不是核销）", r.mode is None and r.evidence == EVIDENCE_NONE, r)
    r = resolve_service_mode_with_evidence(
        SHOP_ID, message_goods_id=GOODS_UNSET,
        claimed_order={"goods_id": GOODS_SHIP, "spec": ""})
    check("A9 未配置商品跳过该线索继续找", r.mode == 1 and r.evidence == EVIDENCE_CLAIMED_ORDER, r)
    r = resolve_service_mode_with_evidence(SHOP_ID, message_goods_id=GOODS_VOUCHER)
    check("A9 存量显式 0 仍是核销", r.mode == 0, r)


# ==============================================================================
# 2. 回归：不能让「别的单/别人」的模式污染本单
# ==============================================================================

def scenario_regressions(db, handler):
    print("B 回归：本单与别人互不干扰")

    # R1：同客户上一单是核销商品（已落库），本单是发货商品
    add_order(db, OLD_ORDER_SN, 0, sn="OLD12345")
    got = handler._local_order_mode(SHOP_ID, CUST_UID, order_sn=ORDER_SN)
    check("B1 别的订单号不得当成本单依据", got is None, got)
    got = handler._local_order_mode(SHOP_ID, CUST_UID, order_sn=OLD_ORDER_SN)
    check("B1 本单订单号能读回落库模式", got == 0, got)
    got = handler._local_order_mode(SHOP_ID, CUST_UID)
    check("B1 没订单号时返回 None（不猜最近一单）", got is None, got)

    r = handler._resolve_order_service_mode(
        SHOP_ID, CUST_UID, message_goods_id=GOODS_SHIP, order_sn=ORDER_SN)
    check("B1 本单商品是发货 → 判发货", r.mode == 1, r)

    # R2：别的客户有核销单，不影响本客户
    add_order(db, "261026-222222222222", 0, session_id=OTHER_UID, sn="OTHER1")
    r = handler._resolve_order_service_mode(SHOP_ID, CUST_UID, order_sn="261025-999999999999")
    check("B2 别的客户的核销单不污染", r.mode is None, r)

    # R2b：本客户、无商品线索、只有历史商品卡 → 弱依据，可据此不索要卡券
    r = handler._resolve_order_service_mode(
        SHOP_ID, CUST_UID, history_goods_id=GOODS_SHIP)
    check("B2b 历史商品卡只给弱依据", r.mode == 1 and r.is_weak, r)


# ==============================================================================
# 3. 分派：对客话术里该不该出现「卡券」
# ==============================================================================

class Harness:
    def __init__(self):
        self.sent = []
        self.ship_calls = []
        self.picked = None          # 待发货唯一候选（用来喂商品线索）
        self.handler = vh.VoucherDetectionHandler()
        self.handler._send = self._fake_send
        self.handler._record_message = lambda *a, **k: None
        self.handler._record_outgoing = lambda *a, **k: None
        self.handler._ship_via_platform = self._fake_ship
        self.handler._resolve_customer_ship_order = self._no_ship_order
        self.handler._pick_pending_reference = lambda *a, **k: self.picked
        self.handler._get_query_config = lambda gid, sp: {
            "server_id": None, "zhenxin_server_id": None, "device_type": None}

    async def _fake_send(self, shop_id, user_id, from_uid, text):
        self.sent.append(text)
        return True

    def _fake_ship(self, order_sn, shop_id, user_id):
        self.ship_calls.append(order_sn)
        return {"success": True}

    async def _no_ship_order(self, shop_id, user_id, from_uid, sn="", force=False):
        return {"status": "none", "order": None, "orders": [], "source": "platform"}

    def last(self):
        return self.sent[-1] if self.sent else ""

    async def send(self, text):
        self.sent.clear()
        await self.handler.handle(make_context(text), dict(META))
        return self.last()


def clear_orders(db):
    with db.session_scope() as session:
        session.query(Order).delete()


async def scenario_dispatch(db):
    print("C 分派：仅发货/未确定不提卡券，核销商品照旧")
    h = Harness()

    # C1 仅发货商品（待发货单可核实）+ 客户只发序列号 → 回复不含「卡券」
    clear_orders(db)
    h.picked = {"goods_id": GOODS_SHIP, "spec": "特惠激活锁", "order_sn": ORDER_SN}
    reply = await h.send(f"序列号 {SERIAL}")
    check("C1 仅发货商品不提卡券", "卡券" not in reply, reply)
    check("C1 有实际回复（不静默）", bool(reply), reply)
    check("C1 走向发货引导（要订单号/卡片）", ("订单" in reply), reply)
    check("C1 未擅自发货", h.ship_calls == [], h.ship_calls)

    # C2 商品/订单全都核不到 + 客户只发序列号 → 未确定，仍不提「卡券」
    clear_orders(db)
    h.picked = None
    reply = await h.send(f"序列号 {SERIAL}")
    check("C2 未确定不提卡券", "卡券" not in reply, reply)
    check("C2 有实际回复（不静默）", bool(reply), reply)

    # C3 核销商品 + 客户只发序列号 → 仍然索要卡券（既有行为不变）
    clear_orders(db)
    h.picked = {"goods_id": GOODS_VOUCHER, "spec": "特惠激活锁", "order_sn": ORDER_SN}
    reply = await h.send(f"序列号 {SERIAL}")
    check("C3 核销商品仍索要卡券", "卡券" in reply, reply)
    check("C3 告知去查看卡券", "查看卡券" in reply, reply)

    # C4 仅发货商品 + 客户发卡券 → 不走核销，也不让客户去查看卡券
    clear_orders(db)
    h.picked = {"goods_id": GOODS_SHIP, "spec": "特惠激活锁", "order_sn": ORDER_SN}
    reply = await h.send(f"卡券 {COUPON}")
    check("C4 仅发货商品给了卡券也不核销", "正在核销" not in reply, reply)
    check("C4 不提让客户去查看卡券", "查看卡券" not in reply, reply)


async def scenario_orders_db(db):
    print("D AI 侧口径：读库里「客户这一单」的结论")
    from core.service_mode import resolve_service_mode_from_orders_db

    clear_orders(db)
    r = resolve_service_mode_from_orders_db(SHOP_ID, CUST_UID)
    check("D1 无单 → 未确定", r.mode is None and r.evidence == EVIDENCE_NONE, r)

    add_order(db, ORDER_SN, 2, sn="")
    r = resolve_service_mode_from_orders_db(SHOP_ID, CUST_UID)
    check("D2 唯一未完成单 → 采用它的模式", r.mode == 2 and r.evidence == EVIDENCE_LOCAL_ORDER, r)
    check("D2 订单号带出", r.order_sn == ORDER_SN, r.order_sn)

    add_order(db, "261024-333333333333", 0, sn="X1")
    r = resolve_service_mode_from_orders_db(SHOP_ID, CUST_UID)
    check("D3 多笔不同模式 → 未确定（不猜）", r.mode is None, r)

    clear_orders(db)
    add_order(db, "261023-444444444444", 3, sn="X2")
    r = resolve_service_mode_from_orders_db(SHOP_ID, CUST_UID)
    check("D4 落库为「未配置」→ 未确定", r.mode is None and r.evidence == EVIDENCE_UNSET, r)


async def main():
    db, tmpdir = make_tmp_db()
    try:
        set_mode(db, GOODS_VOUCHER, 0)
        set_mode(db, GOODS_SHIP, 1)
        # 不联网：订单查询一律返回"查不到"
        order_lookup.pending_orders_summary = lambda *a, **k: {"status": "unavailable", "orders": []}
        order_lookup.common_pending_goods = lambda *a, **k: None
        import core.service_mode as sm
        sm.infer_goods_id_from_session = lambda *a, **k: None

        scenario_matrix(db)
        scenario_regressions(db, vh.VoucherDetectionHandler())
        await scenario_dispatch(db)
        await scenario_orders_db(db)
    finally:
        try:
            db.engine.dispose()
        except Exception:
            pass
        shutil.rmtree(tmpdir, ignore_errors=True)

    total = _results["pass"] + _results["fail"]
    print(f"\n合计 {_results['pass']}/{total} 通过")
    return 0 if _results["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
