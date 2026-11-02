# -*- coding: utf-8 -*-
"""规格 → 验机服务解析自测脚本（离线，不联网）

覆盖 2026-10-29 15:32 那单的真实缺陷（客户买「特惠激活锁」，程序按「全面验机报告」查了）：
1. 规格精确命中时用该规格的 zhenxin 服务号（特惠激活锁 = 1014，不是全面验机 1032）
2. 规格拿不到时**不顶替**成别的套餐（尤其不能默认成最全的全面验机）
3. 订单详情接口命中 40002 限流时先重试，重试成功后不再设长冷却
4. 重试仍被限流才短暂冷却，不会把整店的规格解析掐掉 10 分钟
5. 规格缺失发货成功时，只回「核对规格、稍后补报告」，绝不换个套餐去查

运行：uv run python scripts/test_spec_query.py
"""
import asyncio
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

# 沙箱/CI 里 loguru 的 enqueue 需要命名管道，离线自测直接关掉异步写日志
import loguru

_orig_add = loguru.logger.add


def _add_no_enqueue(*args, **kwargs):
    kwargs["enqueue"] = False
    return _orig_add(*args, **kwargs)


loguru.logger.add = _add_no_enqueue

from database.db_manager import DatabaseManager
from database.models import GoodsCommit
from core.di_container import container
from core import order_lookup
from Message.handlers import voucher_handler as vh

SHOP_ID, USER_ID, CUST_UID = "SHOP1", "ACC1", "CUST1"
SERIAL = "VQ627F3QQH"
ORDER_SN = "261029-401373929091626"
GOODS_ID = "328045268438"
SPEC_BOUGHT = "特惠激活锁"          # zhenxin 1014
SPEC_FULL = "全面验机报告+人工解析"  # zhenxin 1032（绝不能被自动顶替）

_results = {"pass": 0, "fail": 0}


def check(name: str, ok: bool, detail="") -> None:
    if ok:
        _results["pass"] += 1
        print("  [PASS] %s" % name)
    else:
        _results["fail"] += 1
        print("  [FAIL] %s %s" % (name, detail))


def make_db():
    tmpdir = tempfile.mkdtemp(prefix="spec_query_")
    db = DatabaseManager(db_path=os.path.join(tmpdir, "test.db"))
    container.register_singleton(DatabaseManager, instance=db)
    with db.session_scope() as session:
        session.add(GoodsCommit(shop_id="2", goods_id=GOODS_ID, sku_id="1075903187638",
                                spec_name=SPEC_FULL, server_id=10112,
                                zhenxin_server_id=1032, device_type="apple"))
        session.add(GoodsCommit(shop_id="2", goods_id=GOODS_ID, sku_id="1075903187640",
                                spec_name=SPEC_BOUGHT, server_id=10105,
                                zhenxin_server_id=1014, device_type="apple"))
    return db, tmpdir


class FakeOrderAPI:
    """替身订单接口：前 fail_times 次回 40002，之后成功"""

    calls = 0
    fail_times = 0
    spec = SPEC_BOUGHT

    def __init__(self, *args, **kwargs):
        pass

    def get_order_by_sn(self, order_sn):
        type(self).calls += 1
        if type(self).calls <= type(self).fail_times:
            return {"success": False, "error_msg": "40002: 操作太过频繁，请稍后再试！"}
        return {
            "success": True,
            "order": {
                "pending_shipment": True,
                "goods_id": GOODS_ID,
                "goods_name": "苹果gsx验机",
                "spec": type(self).spec,
            },
        }


async def main() -> int:
    db, tmpdir = make_db()
    handler = vh.VoucherDetectionHandler()

    print("S1 规格解析：精确命中 / 未知不顶替")
    cfg = handler._resolve_query_config(GOODS_ID, SPEC_BOUGHT)
    check("S1 特惠激活锁 -> zhenxin 1014",
          cfg.get("zhenxin_server_id") == "1014", cfg)
    check("S1 特惠激活锁 -> server_id 10105",
          cfg.get("server_id") == "10105", cfg)

    for spec in ("", "不存在规格"):
        cfg = handler._resolve_query_config(GOODS_ID, spec)
        check("S1 spec=%r 不顶替成全面验机" % spec,
              not cfg.get("server_id") and not cfg.get("zhenxin_server_id"), cfg)

    cfg = handler._resolve_query_config("999", "")  # 商品未知
    check("S1 商品未知也不顶替",
          not cfg.get("server_id") and not cfg.get("zhenxin_server_id"), cfg)

    import Channel.pinduoduo.utils.API.order_api as order_api

    order_api.MmsOrderAPI = FakeOrderAPI
    order_lookup._DETAIL_RETRY_DELAYS = (0.01, 0.01)  # 自测不等真实退避

    print("S2 订单详情限流后重试")
    FakeOrderAPI.calls = 0
    FakeOrderAPI.fail_times = 2
    FakeOrderAPI.spec = SPEC_BOUGHT
    order_lookup._detail_cache.clear()
    order_lookup._cooldown_until.clear()
    detail = order_lookup.get_order_detail(SHOP_ID, USER_ID, ORDER_SN, retries=2)
    check("S2 重试后拿到订单规格 特惠激活锁",
          bool(detail) and detail.get("spec") == SPEC_BOUGHT, detail)
    check("S2 首次+2 次重试共 3 次调用", FakeOrderAPI.calls == 3, FakeOrderAPI.calls)
    check("S2 成功不进入冷却", not order_lookup.is_cooling_down())

    print("S3 重试仍被限流：短冷却")
    FakeOrderAPI.calls = 0
    FakeOrderAPI.fail_times = 99
    order_lookup._detail_cache.clear()
    order_lookup._cooldown_until.clear()
    detail = order_lookup.get_order_detail(SHOP_ID, USER_ID, "261029-000000000000001", retries=2)
    check("S3 限流重试耗尽返回 None", detail is None, detail)
    left = order_lookup._cooldown_until.get(SHOP_ID, 0.0) - time.time()
    check("S3 冷却时长 <= 60s（不是 600s）", 0 < left <= 61.0, round(left, 1))

    print("S4 规格缺失时发货：只告知核对，不换套餐查")
    import Channel.pinduoduo.utils.API.gsxunlocking_api as gsx_api

    queried = {}
    sent = {}

    def fake_gsx(sn, srv):
        queried["srv"] = srv
        return {"success": True, "result": "型号信息: IPHONE 13 MIDNIGHT 256GB"}

    async def fake_sender(serial, goods_id, spec, report_data, order_id,
                          shop_id, user_id, from_uid, voucher_success_text=""):
        sent["report"] = report_data

    gsx_api.query_gsxunlocking = fake_gsx
    handler._query_gsxunlocking_and_send = fake_sender
    handler._ship_via_platform = lambda order_sn, shop_id, user_id: {"success": True,
                                                                    "result_code": 100}
    handler._order_already_shipped = lambda *a, **k: False
    handler._already_shipped = lambda *a, **k: False
    handler._update_order_status = lambda *a, **k: None

    order_lookup.get_order_detail = lambda *a, **k: None  # 平台限流，规格拿不到
    order_lookup._cooldown_until.clear()
    reply, status = await handler._do_ship_locked(
        SERIAL, ORDER_SN, SHOP_ID, USER_ID, CUST_UID, None,
        goods_id=GOODS_ID, spec="")
    check("S4 没换套餐去查（没有发生验机请求）", "srv" not in queried, queried)
    check("S4 改要订单核对（不再说'确认后补发'）",
          "订单" in (reply or "") and "确认后马上" not in (reply or ""), reply)
    check("S4 状态仍是发货成功", status == vh._SHIP_SUCCESS, status)

    print("S5 规格命中时用客户买的那个服务号")
    queried.clear()
    sent.clear()
    order_lookup.get_order_detail = lambda *a, **k: {
        "pending_shipment": True, "goods_id": GOODS_ID,
        "goods_name": "苹果gsx验机", "spec": SPEC_BOUGHT}
    handler._mark_serial_confirmed(SHOP_ID, CUST_UID, SERIAL)  # 客户已确认过序列号
    reply, status = await handler._do_ship_locked(
        SERIAL, ORDER_SN, SHOP_ID, USER_ID, CUST_UID, None,
        goods_id=GOODS_ID, spec="")
    check("S5 查询用的是特惠激活锁 1014", queried.get("srv") == "1014", queried)
    check("S5 报告数据已交给报告链", sent.get("report") is not None, sent)
    check("S5 返回 None（报告链已发完）", reply is None and status == vh._SHIP_SUCCESS, reply)

    shutil.rmtree(tmpdir, ignore_errors=True)
    print("\n合计: %d 通过 / %d 失败" % (_results["pass"], _results["fail"]))
    return 0 if _results["fail"] == 0 else 1


sys.exit(asyncio.run(main()))
