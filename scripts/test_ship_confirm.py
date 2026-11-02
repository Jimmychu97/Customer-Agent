# -*- coding: utf-8 -*-
"""发货「客户确认 → 真发货 → 必反馈」回归自测（离线，不联网）

线上症状（logs/app.log 16:26~16:48）：
  客户已经把序列号/订单发全了，程序也请他确认了，但客户回「确认」之后
  既没去发货/查询，也没给客户任何回复 —— 客户只能反复问「查好了吗」。

本脚本把这条链逐环钉死：
  C1 只有序列号（没订单号）        -> 有回复，且不调发货接口
  C2 序列号+订单号齐了             -> 回「请确认」，不调发货接口
  C3 客户回「确认」                -> 调发货 1 次，且有回复
  C4 客户回「确认了」（非整句白名单）-> 调发货 1 次，且有回复
  C5 客户回「好的稍等」（不是确认） -> 不发货、有提醒、待确认仍在
  C6 C5 之后再回「确认」           -> 仍能发货 1 次
  C7 发货成功但验机没返回数据      -> 必有「没取到报告」的明确话术

运行：uv run python scripts/test_ship_confirm.py
"""
import asyncio
import json
import os
import sys
import tempfile

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

tempfile.tempdir = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "temp")
os.makedirs(tempfile.tempdir, exist_ok=True)

# 沙箱里 loguru 的 enqueue 需要命名管道，离线自测关掉异步写日志
import loguru

_o = loguru.logger.add
loguru.logger.add = lambda *a, **k: (k.__setitem__("enqueue", False), _o(*a, **k))[1]

import shutil  # noqa: E402

from core.di_container import container  # noqa: E402
from database.db_manager import DatabaseManager  # noqa: E402
from database.models import Order, ProductKnowledge, Shop  # noqa: E402
from bridge.context import ChannelType, Context, ContextType  # noqa: E402
from Message.handlers import voucher_handler as vh  # noqa: E402
import core.order_lookup as order_lookup  # noqa: E402

SHOP_ID = "SHOP1"
USER_ID = "ACC1"
CUST_UID = "CUST1"
SERIAL = "ABCDE12345"
ORDER_SN = "261028-423740065360537"
GOODS_SHIP = 1002
GOODS_MULTI = 1003   # 3 个规格：规格不明时绝不能发货（线上 1010294008341 就是这种）
GOODS_SINGLE = 1004  # 只有 1 个规格：允许按「唯一规格」兜底
GOODS_KB_ONLY = 1005  # 只在商品知识库里、SKU 没同步（线上 976314624020 就是这样）
META = {"shop_id": SHOP_ID, "user_id": USER_ID, "from_uid": CUST_UID}

EMPTY_CFG = {"server_id": None, "zhenxin_server_id": None, "device_type": None}

# 商品 -> 规格 -> 验机编码（测试替身；真实链路读 goods_commit）
VERIFY_CFG = {
    str(GOODS_SHIP): {"128G": {"server_id": "10105", "zhenxin_server_id": None,
                               "device_type": "android"}},
    str(GOODS_MULTI): {
        "GSX验机（全面信息）": {"server_id": "10112", "zhenxin_server_id": "1032",
                              "device_type": "apple"},
        "运营商+黑白名单": {"server_id": "10117", "zhenxin_server_id": "1012",
                            "device_type": "apple"},
        "监管机 二手必查": {"server_id": "10125", "zhenxin_server_id": "1034",
                            "device_type": "apple"},
    },
    str(GOODS_SINGLE): {"唯一套餐": {"server_id": "10999", "zhenxin_server_id": None,
                                     "device_type": "android"}},
}

_results = {"pass": 0, "fail": 0}


def check(name, ok, detail=""):
    if ok:
        _results["pass"] += 1
        print("  [PASS] %s" % name)
    else:
        _results["fail"] += 1
        print("  [FAIL] %s %s" % (name, str(detail)[:300]))


def make_tmp_db():
    tmpdir = tempfile.mkdtemp(prefix="ship_confirm_")
    db = DatabaseManager(db_path=os.path.join(tmpdir, "test.db"))
    container.register_singleton(DatabaseManager, instance=db)
    return db, tmpdir


def make_context(content, ctype=ContextType.TEXT):
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


def set_mode(db, mode, goods_id):
    """写入商品级服务模式（走真实数据库契约）"""
    with db.session_scope() as session:
        shop = session.query(Shop).filter(Shop.shop_id == SHOP_ID).first()
        if shop is None:
            shop = Shop(channel_id=1, shop_id=SHOP_ID, shop_name="测试店")
            session.add(shop)
            session.flush()
        row = session.query(ProductKnowledge).filter_by(
            shop_id=shop.id, goods_id=goods_id).first()
        if row is None:
            session.add(ProductKnowledge(shop_id=shop.id, goods_id=goods_id,
                                         goods_name="测试商品", service_mode=mode))
        else:
            row.service_mode = mode


def seed_goods_commit(db):
    """写入 SKU/规格行：`_product_spec_count` 与「唯一规格兜底」都读这张表"""
    from database.models import GoodsCommit

    with db.session_scope() as session:
        for goods, specs in VERIFY_CFG.items():
            for spec, cfg in specs.items():
                exists = session.query(GoodsCommit).filter_by(
                    shop_id=SHOP_ID, sku_id="%s_%s" % (goods, spec)).first()
                if exists:
                    continue
                session.add(GoodsCommit(
                    shop_id=SHOP_ID, goods_id=str(goods), sku_id="%s_%s" % (goods, spec),
                    spec_name=spec, service_mode=1,
                    server_id=int(cfg["server_id"]) if cfg.get("server_id") else None,
                    zhenxin_server_id=(int(cfg["zhenxin_server_id"])
                                       if cfg.get("zhenxin_server_id") else None),
                    device_type=cfg.get("device_type"),
                ))


class Harness:
    """只做记录、不联网的发货处理器"""

    def __init__(self, db):
        self.db = db
        self.sent = []
        self.ship_calls = []
        self.report_calls = []
        self.claim_status = "one"      # _resolve_customer_ship_order 的返回口径
        self.claim_goods = GOODS_SHIP
        self.claim_spec = "128G"
        self.report_data = {"字段": "值"}  # 验机接口返回的数据；None = 没数据
        self.handler = vh.VoucherDetectionHandler()

        async def fake_send(shop_id, user_id, from_uid, text):
            self.sent.append(text)
            return True

        self.handler._send = fake_send
        self.handler._record_message = lambda *a, **k: None
        self.handler._ship_via_platform = self._fake_ship
        # 规格精确匹配：规格不对/为空一律拿不到编码（与 database.goods_config 契约一致）
        self.handler._get_query_config = lambda gid, sp: dict(
            VERIFY_CFG.get(str(gid), {}).get(sp or "", EMPTY_CFG))

        async def fake_claim(shop_id, user_id, from_uid, sn):
            if self.claim_status == "one":
                return {"status": "one", "source": "test",
                        "order": {"order_sn": ORDER_SN, "goods_id": self.claim_goods,
                                  "spec": self.claim_spec, "goods_name": "测试商品"}}
            return {"status": "none", "orders": []}

        self.handler._resolve_customer_ship_order = fake_claim

    def _fake_ship(self, order_sn, shop_id, user_id):
        self.ship_calls.append(order_sn)
        return {"success": True, "error_msg": "", "result_code": 100}

    def install_goods_stub(self):
        """验机接口替身：返回 self.report_data（None 表示平台没数据）

        android 走 gsx_api.query_gsx(server_id)，apple 走 gsxunlocking_api.query_gsxunlocking(zhenxin)
        """
        import Channel.pinduoduo.utils.API.gsx_api as gsx_api
        import Channel.pinduoduo.utils.API.gsxunlocking_api as gsxu

        self._orig_gsx = getattr(gsx_api, "query_gsx", None)
        self._orig_gsxu = getattr(gsxu, "query_gsxunlocking", None)

        def fake_query_gsx(sn, server_id):
            self.report_calls.append({"sn": sn, "server_id": server_id, "zhenxin": None})
            return {"success": True, "data": self.report_data, "error_msg": ""}

        def fake_query_gsxunlocking(sn, zhenxin_sid, factory=None, format=None):
            self.report_calls.append({"sn": sn, "server_id": None, "zhenxin": zhenxin_sid})
            return {"success": True, "result": self.report_data, "error_msg": ""}

        gsx_api.query_gsx = fake_query_gsx
        gsxu.query_gsxunlocking = fake_query_gsxunlocking

    def restore_goods_stub(self):
        import Channel.pinduoduo.utils.API.gsx_api as gsx_api
        import Channel.pinduoduo.utils.API.gsxunlocking_api as gsxu

        if self._orig_gsx is not None:
            gsx_api.query_gsx = self._orig_gsx
        if self._orig_gsxu is not None:
            gsxu.query_gsxunlocking = self._orig_gsxu

    def last(self):
        return self.sent[-1] if self.sent else ""

    def reset(self):
        with self.db.session_scope() as session:
            session.query(Order).delete()
        self.sent.clear()
        self.ship_calls.clear()
        self.report_calls.clear()
        self.report_data = {"字段": "值"}
        self.claim_status = "one"
        self.claim_goods = GOODS_SHIP
        self.claim_spec = "128G"
        vh._ship_confirmed.clear()
        vh._serial_confirm_pending.clear()
        vh._serial_confirmed.clear()
        vh._report_sent.clear()

    async def send_text(self, content, spec=""):
        meta = dict(META, goods_id=self.claim_goods)
        if spec:
            meta["spec"] = spec
        return await self.handler.handle(make_context(content), meta)

    async def send_order_card(self, order_sn, goods_id=None, spec="128G"):
        goods_id = goods_id or self.claim_goods
        card = json.dumps({"order_id": order_sn, "goods_id": goods_id,
                           "goods_name": "测试商品", "spec": spec}, ensure_ascii=False)
        return await self.handler.handle(
            make_context(card, ContextType.ORDER_INFO), dict(META, goods_id=goods_id))


async def scenario_confirm_chain(h, db):
    print("C1~C4 仅发货商品：序列号 -> 订单 -> 请确认 -> 确认后必须真发货")
    h.reset()
    set_mode(db, 1, GOODS_SHIP)

    h.claim_status = "none"
    await h.send_text(SERIAL)
    check("C1 只有序列号：有回复", bool(h.last()), h.sent[-1:])
    check("C1 只有序列号：不调发货接口", h.ship_calls == [], h.ship_calls)

    h.claim_status = "one"
    await h.send_order_card(ORDER_SN)

    h.sent.clear()
    await h.send_text(SERIAL)
    asked = "确认" in "".join(h.sent)
    check("C2 序列号+订单：回「请确认」", asked, h.sent)
    check("C2 序列号+订单：确认前不发货", h.ship_calls == [], h.ship_calls)

    h.sent.clear()
    await h.send_text("确认")
    check("C3 客户回「确认」：发货 1 次", h.ship_calls == [ORDER_SN], h.ship_calls)
    check("C3 客户回「确认」：给客户回复了", bool(h.sent), h.sent)

    h.reset()
    set_mode(db, 1, GOODS_SHIP)
    await h.send_order_card(ORDER_SN)
    await h.send_text(SERIAL)
    h.sent.clear()
    await h.send_text("确认了")
    check("C4 客户回「确认了」：发货 1 次", h.ship_calls == [ORDER_SN], h.ship_calls)
    check("C4 客户回「确认了」：给客户回复了", bool(h.sent), h.sent)


async def scenario_pending_survives(h, db):
    print("C5~C6 客户先回一句不是确认的话：待确认不能被静默作废")
    h.reset()
    set_mode(db, 1, GOODS_SHIP)
    await h.send_order_card(ORDER_SN)
    await h.send_text(SERIAL)

    h.sent.clear()
    await h.send_text("好的稍等")
    check("C5 非确认短句：不发货", h.ship_calls == [], h.ship_calls)
    check("C5 非确认短句：有提醒回复", bool(h.sent), h.sent)
    pending = h.handler._peek_confirm_pending(SHOP_ID, CUST_UID)
    check("C5 非确认短句：待确认仍在", pending is not None, pending)

    h.sent.clear()
    await h.send_text("确认")
    check("C6 之后回「确认」：仍能发货 1 次", h.ship_calls == [ORDER_SN], h.ship_calls)
    check("C6 之后回「确认」：有回复", bool(h.sent), h.sent)


async def scenario_report_missing(h, db):
    print("C7 发货成功但验机没数据：不许静默，必须给客户明确结论")
    h.reset()
    set_mode(db, 1, GOODS_SHIP)
    h.report_data = None
    await h.send_order_card(ORDER_SN)
    await h.send_text(SERIAL)

    h.sent.clear()
    await h.send_text("确认")
    joined = "".join(h.sent)
    check("C7 发货成功", h.ship_calls == [ORDER_SN], h.ship_calls)
    check("C7 发过「发货成功」", "发货成功" in joined, h.sent)
    check("C7 明确告知没取到报告",
          any(k in joined for k in ("没取到验机报告", "没出验机报告", "报告暂时没出", "没查")),
          h.sent)


async def scenario_spec_unknown(h, db):
    print("C8~C10 验机商品拿不到规格（线上 17:28 的真实场景）：先别发货，把套餐问清楚")
    # C8 多规格 + 规格未知 → 不发货、要订单卡片
    h.reset()
    set_mode(db, 1, GOODS_MULTI)
    h.claim_goods, h.claim_spec = GOODS_MULTI, ""
    h.detail = None                      # 订单详情被风控拿不到（线上就是这样）
    h.sent.clear()
    await h.send_text(SERIAL)
    joined = "".join(h.sent)
    check("C8 多规格无规格：不发货", h.ship_calls == [], h.ship_calls)
    check("C8 多规格无规格：要求发订单卡片/套餐名",
          ("订单卡片" in joined or "套餐" in joined), h.sent)
    check("C8 多规格无规格：已告知没发货",
          ("没发给您发货" in joined or "我先没" in joined or "尚未发货" in joined), h.sent)

    # C9 单规格 + 规格未知 → 按唯一规格兜底，确认后发货并出报告
    h.reset()
    set_mode(db, 1, GOODS_SINGLE)
    h.claim_goods, h.claim_spec = GOODS_SINGLE, ""
    h.detail = None
    h.sent.clear()
    await h.send_text(SERIAL)
    check("C9 单规格无规格：按唯一规格兜底后请客户确认",
          "确认" in "".join(h.sent), h.sent)
    check("C9 单规格无规格：确认前不发货", h.ship_calls == [], h.ship_calls)
    h.sent.clear()
    await h.send_text("确认")
    check("C9 单规格无规格：确认后发货 1 次", h.ship_calls == [ORDER_SN], h.ship_calls)
    check("C9 单规格无规格：报告用兜底规格的编码查询",
          any(c.get("server_id") == "10999" for c in h.report_calls), h.report_calls)

    # C10 客户补了套餐（订单卡片带 spec）→ 确认后发货并出报告
    h.reset()
    set_mode(db, 1, GOODS_MULTI)
    h.claim_goods, h.claim_spec = GOODS_MULTI, ""
    h.detail = None
    await h.send_text(SERIAL)
    h.sent.clear()
    await h.send_text(SERIAL, spec="运营商+黑白名单")
    check("C10 补了套餐：请客户确认", "确认" in "".join(h.sent), h.sent)
    h.sent.clear()
    await h.send_text("确认")
    check("C10 补了套餐：发货 1 次", h.ship_calls == [ORDER_SN], h.ship_calls)
    check("C10 补了套餐：报告用该套餐的编码查询（apple→真心 1012）",
          any(str(c.get("zhenxin")) == "1012" for c in h.report_calls), h.report_calls)


async def main():
    db, tmpdir = make_tmp_db()
    seed_goods_commit(db)
    h = Harness(db)
    h.install_goods_stub()
    orig_detail = order_lookup.get_order_detail
    orig_summary = order_lookup.pending_orders_summary
    h.detail = {"goods_id": GOODS_SHIP, "spec": "128G", "goods_name": "测试商品",
                "pending_shipment": True, "order_status_str": "待发货"}
    order_lookup.get_order_detail = lambda *a, **k: h.detail
    order_lookup.pending_orders_summary = lambda *a, **k: {
        "status": "unique", "orders": [{"order_sn": ORDER_SN, "goods_id": GOODS_SHIP,
                                        "spec": "128G", "goods_name": "测试商品"}],
    }
    try:
        await scenario_confirm_chain(h, db)
        await scenario_pending_survives(h, db)
        await scenario_report_missing(h, db)
        await scenario_spec_unknown(h, db)
    finally:
        order_lookup.get_order_detail = orig_detail
        order_lookup.pending_orders_summary = orig_summary
        h.restore_goods_stub()
        shutil.rmtree(tmpdir, ignore_errors=True)

    print("\n结果: %d 通过, %d 失败" % (_results["pass"], _results["fail"]))
    return 0 if _results["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
