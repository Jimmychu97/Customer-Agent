# -*- coding: utf-8 -*-
"""双通道自测：卡券核销 与 发货 必须共存（离线，不联网、不查平台订单）

核心验证：**只靠「客户卡片里的商品ID」匹配系统商品配置**，就能决定走哪条路；
平台订单接口全程打桩为不可用，证明两条路都不依赖它。

覆盖：
  S1 绑定「仅发货」商品 → 客户只发序列号 → 请确认 → 发货 1 次，绝不要卡券
  S2 绑定「卡券核销」商品 → 客户只发序列号 → 索要卡券（原有行为）
  S3 绑定「二选一兼容」商品 → 只发序列号走发货；再发卡券走核销（两条路都通）
  S4 先发货 → 客户后补卡券 → 卡券照常核销，且不新建空单
  S5 先核销 → 客户后要发货 → 货照常发出
  S6 无绑定且平台不可用 → 判为未确定，不索要卡券

运行：uv run python scripts/test_dual_mode.py
"""
import asyncio
import os
import shutil
import sys
import tempfile

os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["LOG_FILE"] = "logs/test.log"          # 强制写测试日志，别污染 app.log
os.environ.setdefault("ORDER_BROWSER_FALLBACK", "0")
os.environ.setdefault("ORDER_HEARTBEAT", "0")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 自测的临时数据库统一放到 temp/ 下，免得在项目根堆一堆 ship_guard_xxx 目录
tempfile.tempdir = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "temp")
os.makedirs(tempfile.tempdir, exist_ok=True)

import loguru  # noqa: E402

_o = loguru.logger.add
loguru.logger.add = lambda *a, **k: (k.__setitem__("enqueue", False), _o(*a, **k))[1]

from database.db_manager import DatabaseManager                                   # noqa: E402
from database.models import GoodsCommit, Order, Shop                              # noqa: E402
from core.di_container import container                                           # noqa: E402
from core import order_lookup                                                     # noqa: E402
from bridge.context import Context, ContextType, ChannelType                       # noqa: E402
from Message.core.conversation_product import bind_product, clear_binding          # noqa: E402
from Message.handlers import voucher_handler as vh                                 # noqa: E402

SHOP, ACC, CUST = "S1", "A1", "C1"
GOODS_SHIP, GOODS_VOUCHER, GOODS_AUTO = 9200001, 9200002, 9200003
SPEC = "特惠激活锁"
SERIAL, COUPON, ORDER_SN = "VQ627F3QQH", "123456789012", "261101-111111111111"

_results = {"pass": 0, "fail": 0}


def check(name, cond, detail=""):
    if cond:
        _results["pass"] += 1
        print(f"  [PASS] {name}")
    else:
        _results["fail"] += 1
        print(f"  [FAIL] {name}  <- {detail}")


def seed(db):
    with db.session_scope() as s:
        shop = Shop(channel_id=1, shop_id=SHOP, shop_name="双通道店")
        s.add(shop)
        s.flush()
        # 只在 SKU 层配置服务模式（product_knowledge 故意不建，证明不靠商品级兜底）
        for goods_id, mode in ((GOODS_SHIP, 1), (GOODS_VOUCHER, 0), (GOODS_AUTO, 2)):
            s.add(GoodsCommit(shop_id=SHOP, goods_id=str(goods_id), sku_id=f"k{goods_id}",
                              spec_name=SPEC, zhenxin_server_id=1014, device_type="apple",
                              service_mode=mode))


class Harness:
    def __init__(self, db):
        self.db = db
        self.sent, self.ship_calls, self.verify_calls = [], [], []
        self.h = vh.VoucherDetectionHandler()
        self.h._send = self._send
        self.h._record_message = lambda *a, **k: None
        self.h._record_outgoing = lambda *a, **k: None
        self.h._ship_via_platform = self._ship
        self.h._do_verify = self._verify
        self.h._llm_analyze_gsx = self._noop
        self.h._llm_analyze_lingsu = self._noop

    async def _send(self, shop_id, user_id, from_uid, text):
        self.sent.append(text)
        return True

    def _ship(self, order_sn, shop_id, user_id):
        self.ship_calls.append(order_sn)
        return {"success": True, "result_code": 100}

    async def _verify(self, sn, coupon_no, shop_id, user_id, from_uid, order_id):
        self.verify_calls.append((sn, coupon_no))
        self.h._update_order_status(order_id, 1)        # 核销成功 → status=1
        return f"✅ 核销成功！卡券号：{coupon_no}", "success"

    async def _noop(self, *a, **k):
        return ""

    async def say(self, text):
        ctx = Context.create_pinduoduo_context(
            content=text, msg_id="m", from_uid=CUST, to_uid="cs", nickname="亲",
            user_msg_type=ContextType.TEXT, shop_id=SHOP, user_id=ACC,
            username="acc", channel_type=ChannelType.PINDUODUO)
        n = len(self.sent)
        await self.h.handle(ctx, {"from_uid": CUST, "shop_id": SHOP, "user_id": ACC})
        return self.sent[n:]

    def text(self):
        return "\n".join(self.sent)

    def orders(self):
        with self.db.session_scope() as s:
            return [(o.id, o.sn_imei, o.coupons, o.status, o.order_sn, o.shipped_at is not None)
                    for o in s.query(Order).order_by(Order.id)]


def reset(db, goods_id=None):
    with db.session_scope() as s:
        s.query(Order).delete()
    clear_binding(SHOP, CUST)
    if goods_id:
        bind_product(SHOP, CUST, goods_id, spec_name=SPEC, order_sn=ORDER_SN)
    vh._report_sent.clear()
    vh._serial_confirm_pending.clear()
    vh._serial_confirmed.clear()
    vh._ship_confirmed.clear()


async def main():
    tmp = tempfile.mkdtemp(prefix="dual_mode_")
    db = DatabaseManager(db_path=os.path.join(tmp, "t.db"))
    container.register_singleton(DatabaseManager, instance=db)
    seed(db)

    # 平台订单接口全程不可用：证明两条路都不依赖它
    order_lookup.pending_orders_summary = lambda *a, **k: {"status": "unavailable", "orders": []}
    order_lookup.pick_unique_pending_ship_order = lambda *a, **k: None
    order_lookup.common_pending_goods = lambda *a, **k: None
    order_lookup.get_order_detail = lambda *a, **k: None
    import Channel.pinduoduo.utils.API.gsxunlocking_api as gx
    gx.query_gsxunlocking = lambda sn, srv: {"success": True, "result": {"型号": "iPhone 15"}}

    h = Harness(db)
    try:
        print("S1 绑定「仅发货」商品 → 只发序列号")
        reset(db, GOODS_SHIP)
        await h.say(f"序列号 {SERIAL}")
        text = h.text()
        check("S1 按卡片商品判为发货（请客户确认）", "请核对本次查询信息" in text, text[:80])
        check("S1 不要卡券", "查看卡券" not in text and "请提供卡券" not in text, text[:80])
        await h.say("确认")
        check("S1 确认后发货 1 次", h.ship_calls == [ORDER_SN], h.ship_calls)
        check("S1 未索要卡券", "查看卡券" not in h.text(), h.text()[:80])

        print("S2 绑定「卡券核销」商品 → 只发序列号")
        reset(db, GOODS_VOUCHER)
        h.sent.clear()
        await h.say(f"序列号 {SERIAL}")
        text = h.text()
        check("S2 索要卡券（原有行为）", "卡券" in text and "查看卡券" in text, text[:100])

        print("S3 绑定「二选一」商品 → 序列号走发货，卡券走核销")
        reset(db, GOODS_AUTO)
        h.sent.clear(); h.ship_calls.clear(); h.verify_calls.clear()
        await h.say(f"序列号 {SERIAL}")
        check("S3 二选一下先按发货确认", "请核对本次查询信息" in h.text(), h.text()[:80])
        await h.say("确认")
        check("S3 发货 1 次", h.ship_calls == [ORDER_SN], h.ship_calls)
        await h.say(f"卡券 {COUPON}")
        check("S3 再给卡券 → 核销 1 次", len(h.verify_calls) == 1, h.verify_calls)

        print("S4 先发货 → 后补卡券（不新建空单）")
        reset(db, GOODS_AUTO)
        h.sent.clear(); h.ship_calls.clear(); h.verify_calls.clear()
        await h.say(f"序列号 {SERIAL}")
        await h.say("确认")
        await h.say(f"卡券 {COUPON}")
        rows = h.orders()
        check("S4 核销被调用 1 次", len(h.verify_calls) == 1, h.verify_calls)
        check("S4 始终只有一张单", len(rows) == 1, rows)
        check("S4 同一单上既有序列号又有卡券",
              rows and rows[0][1] == SERIAL and rows[0][2] == COUPON, rows)

        print("S5 先核销 → 后要发货")
        reset(db, GOODS_AUTO)
        h.sent.clear(); h.ship_calls.clear(); h.verify_calls.clear()
        await h.say(f"序列号 {SERIAL} 卡券 {COUPON}")
        check("S5 核销 1 次", len(h.verify_calls) == 1, h.verify_calls)
        h.sent.clear()
        await h.say(f"序列号 {SERIAL} 订单号 {ORDER_SN}")
        await h.say("确认")
        check("S5 之后仍能发货 1 次", h.ship_calls == [ORDER_SN], h.ship_calls)
        rows = h.orders()
        check("S5 发货时间已记录", rows and rows[0][5] is True, rows)

        print("S6 无绑定 + 平台不可用 → 未确定")
        reset(db)          # 不绑定任何商品
        h.sent.clear(); h.ship_calls.clear()
        await h.say(f"序列号 {SERIAL}")
        text = h.text()
        check("S6 不索要卡券", "查看卡券" not in text and "请提供卡券" not in text, text[:100])
        check("S6 引导发订单卡片/订单号", "订单卡片" in text or "订单号" in text, text[:100])
        check("S6 不擅自发货", h.ship_calls == [], h.ship_calls)
    finally:
        try:
            db.engine.dispose()
        except Exception:
            pass
        shutil.rmtree(tmp, ignore_errors=True)

    total = _results["pass"] + _results["fail"]
    print(f"\n双通道自测：{_results['pass']}/{total} 通过")
    return 0 if _results["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
