# -*- coding: utf-8 -*-
"""订单查询并发/限流自测（离线，不联网）

覆盖：
C1 并发合并：10 个线程同时查同一店铺 → 只打 1 次平台接口
C2 多店铺隔离：A 店被 40002 → 只有 A 冷却，B 店正常出结果
C3 指数退避：连续限流 30 → 60 → 120 → … 上限 300
C4 详情负缓存：同一订单查失败后 60s 内不重复打接口
C5 强制刷新：快照为空时 force_refresh 能拉到新单，且 30s 内只刷新一次
C6 不阻塞事件循环：订单/发货这类平台 IO 走线程池，10 个慢调用互不排队

运行：uv run python scripts/test_order_lookup_concurrency.py
"""
import asyncio
import os
import sys
import threading
import time

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("LOG_FILE", "logs/test.log")  # 自测日志别写进 app.log
os.environ.setdefault("ORDER_BROWSER_FALLBACK", "0")  # 自测不启动浏览器
os.environ.setdefault("ORDER_HEARTBEAT", "0")  # 自测不跑会话心跳
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import loguru

_o = loguru.logger.add
loguru.logger.add = lambda *a, **k: (k.__setitem__("enqueue", False), _o(*a, **k))[1]

import Channel.pinduoduo.utils.API.order_api as order_api
from core import order_lookup as ol
from Message.handlers import voucher_handler as vh

_results = {"pass": 0, "fail": 0}


def check(name, ok, detail=""):
    if ok:
        _results["pass"] += 1
        print("  [PASS] %s" % name)
    else:
        _results["fail"] += 1
        print("  [FAIL] %s | %r" % (name, detail))


class FakeAPI:
    """按 shop 返回预设结果，并记录调用次数"""

    calls = {}
    results = {}      # shop -> dict(success/orders/error_msg)
    delay = 0.0
    lock = threading.Lock()

    def __init__(self, shop_id, user_id):
        self.shop_id = str(shop_id)

    @classmethod
    def reset(cls, results=None, delay=0.0):
        cls.calls = {}
        cls.results = results or {}
        cls.delay = delay

    def list_orders(self, **kwargs):
        with FakeAPI.lock:
            FakeAPI.calls[self.shop_id] = FakeAPI.calls.get(self.shop_id, 0) + 1
        if FakeAPI.delay:
            time.sleep(FakeAPI.delay)
        return FakeAPI.results.get(self.shop_id, {"success": True, "orders": [], "error_msg": ""})

    def list_pending_shipment_orders(self, **kwargs):
        res = FakeAPI.results.get(self.shop_id, {})
        if res.get("success") is False:
            return {"success": False, "total": 0, "orders": [], "error_msg": res.get("error_msg", "")}
        return {"success": True, "total": 0, "orders": [], "error_msg": ""}

    def get_order_by_sn(self, order_sn):
        with FakeAPI.lock:
            FakeAPI.calls[self.shop_id] = FakeAPI.calls.get(self.shop_id, 0) + 1
        res = FakeAPI.results.get(self.shop_id, {})
        if res.get("success") is False:
            return {"success": False, "order": None, "error_msg": res.get("error_msg", "")}
        order = {"order_sn": order_sn, "pending_shipment": True,
                 "goods_id": "1", "spec": "特惠激活锁", "goods_name": "测试商品"}
        return {"success": True, "order": order, "error_msg": ""}


def pending(shop, orders):
    return {"status": "ok", "orders": orders}


def main_sync():
    orig = order_api.MmsOrderAPI
    order_api.MmsOrderAPI = FakeAPI
    try:
        print("C1 并发合并：10 线程同店铺只打 1 次接口")
        ol.clear_cache()
        one_order = [{"order_sn": "261031-000000000000001", "goods_id": "1",
                      "spec": "特惠激活锁", "goods_name": "测试商品", "pending_shipment": True}]
        FakeAPI.reset({"SHOP1": {"success": True, "orders": one_order, "error_msg": ""}},
                      delay=0.2)
        results = []
        barrier = threading.Barrier(10)

        def worker():
            barrier.wait()
            results.append(ol.pending_orders_summary("SHOP1", "ACC1"))

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        check("C1 只打 1 次接口", FakeAPI.calls.get("SHOP1") == 1, FakeAPI.calls)
        check("C1 10 个结果都拿到单",
              all(r.get("status") == "unique" for r in results) and len(results) == 10,
              results[:2])

        print("C2 多店铺隔离：A 店被限流不影响 B 店")
        ol.clear_cache()
        FakeAPI.reset({
            "SHOP_A": {"success": False, "error_msg": "40002 操作太过频繁，请稍后再试！"},
            "SHOP_B": {"success": True, "orders": one_order, "error_msg": ""},
        })
        a = ol.pending_orders_summary("SHOP_A", "ACC1")
        b = ol.pending_orders_summary("SHOP_B", "ACC1")
        check("C2 A 店判为查不到", a.get("status") == "unavailable", a)
        check("C2 B 店正常出结果", b.get("status") == "unique", b)
        check("C2 只有 A 店进入冷却",
              ol.is_cooling_down("SHOP_A") and not ol.is_cooling_down("SHOP_B"),
              (ol.is_cooling_down("SHOP_A"), ol.is_cooling_down("SHOP_B")))

        print("C3 指数退避：30 → 60 → 120 → … 上限 300")
        check("C3 退避序列", [ol._backoff_seconds(n) for n in (1, 2, 3, 4, 9)]
              == [30.0, 60.0, 120.0, 240.0, 300.0],
              [ol._backoff_seconds(n) for n in (1, 2, 3, 4, 9)])
        ol.clear_cache()
        FakeAPI.reset({"SHOP_C": {"success": False,
                                  "error_msg": "40002 操作太过频繁，请稍后再试！"}})
        ol.pending_orders_summary("SHOP_C", "ACC1")
        first = ol._cooldown_until.get("SHOP_C", 0) - time.time()
        with ol._lock:
            ol._cache.pop("SHOP_C", None)
            ol._cooldown_until.pop("SHOP_C", None)
            ol._last_call_ts.pop("SHOP_C", None)
            ol._http_blocked_until.pop("SHOP_C", None)
        ol.pending_orders_summary("SHOP_C", "ACC1")
        second = ol._cooldown_until.get("SHOP_C", 0) - time.time()
        check("C3 第 1 次约 30s、第 2 次约 60s",
              25 <= first <= 31 and 55 <= second <= 61, (first, second))

        print("C4 详情负缓存：失败后 60s 内不重复打接口")
        ol.clear_cache()
        FakeAPI.reset({"SHOP_D": {"success": False, "error_msg": "40002 操作太过频繁"}})
        ol.get_order_detail("SHOP_D", "ACC1", "261031-000000000000099")
        with ol._lock:
            ol._cooldown_until.pop("SHOP_D", None)  # 排除冷却影响，单看负缓存
        before = FakeAPI.calls.get("SHOP_D", 0)
        ol.get_order_detail("SHOP_D", "ACC1", "261031-000000000000099")
        check("C4 第二次不再请求接口", FakeAPI.calls.get("SHOP_D") == before, FakeAPI.calls)

        print("C5 强制刷新：快照为空时能拉到新单，且 30s 内只刷新一次")
        ol.clear_cache()
        FakeAPI.reset({"SHOP_E": {"success": True, "orders": [], "error_msg": ""}})
        first_summary = ol.pending_orders_summary("SHOP_E", "ACC1")
        FakeAPI.results["SHOP_E"] = {"success": True, "orders": one_order, "error_msg": ""}
        forced = ol.pending_orders_summary("SHOP_E", "ACC1", force_refresh=True)
        calls_after_force = FakeAPI.calls.get("SHOP_E")
        again = ol.pending_orders_summary("SHOP_E", "ACC1", force_refresh=True)
        check("C5 首次为空", first_summary.get("status") == "empty", first_summary)
        check("C5 强制刷新拿到新单", forced.get("status") == "unique", forced)
        check("C5 30s 内第二次强制刷新不再打接口",
              FakeAPI.calls.get("SHOP_E") == calls_after_force, FakeAPI.calls)
        check("C5 第二次仍是上次结果", again.get("status") == "unique", again)

        print("C7 HTTP 订单接口被 40002 风控时，浏览器兜底能拿到待发货单")
        ol.clear_cache()
        os.environ["ORDER_BROWSER_FALLBACK"] = "1"
        FakeAPI.reset({"SHOP_F": {"success": False,
                                  "error_msg": "40002 操作太过频繁，请稍后再试！"}})
        browser_calls = []

        def _fake_browser(account):
            browser_calls.append(account)
            return [{"order_sn": "261031-629040760451626", "goods_id": "1",
                     "spec": "特惠激活锁", "goods_name": "测试商品", "pending_shipment": True}]

        orig_browser = ol._fetch_orders_via_browser
        ol._fetch_orders_via_browser = _fake_browser
        try:
            summary = ol.pending_orders_summary("SHOP_F", "ACC1")
            check("C7 浏览器兜底拿到那笔单",
                  summary.get("status") == "unique"
                  and summary["orders"][0]["order_sn"] == "261031-629040760451626", summary)
            second = ol.pending_orders_summary("SHOP_F", "ACC1")
            check("C7 结果进缓存，不再重复启动浏览器",
                  len(browser_calls) == 1 and second.get("status") == "unique", browser_calls)

            print("C8 浏览器兜底也失败时判为查不到（不谎称没有单）")
            ol.clear_cache()
            ol._fetch_orders_via_browser = lambda account: []
            summary = ol.pending_orders_summary("SHOP_F", "ACC1")
            check("C8 判为 unavailable", summary.get("status") == "unavailable", summary)
        finally:
            ol._fetch_orders_via_browser = orig_browser
            os.environ["ORDER_BROWSER_FALLBACK"] = "0"

        print("C9 HTTP 被风控后熔断：不再硬打 HTTP，直接走浏览器")
        ol.clear_cache()
        os.environ["ORDER_BROWSER_FALLBACK"] = "1"
        FakeAPI.reset({"SHOP_G": {"success": False,
                                  "error_msg": "40002 操作太过频繁，请稍后再试！"}})
        browser2 = []

        def _fake_browser2(account):
            browser2.append(account)
            return [{"order_sn": "261031-629040760451626", "goods_id": "1",
                     "spec": "特惠激活锁", "goods_name": "测试商品", "pending_shipment": True}]

        orig_browser2 = ol._fetch_orders_via_browser
        ol._fetch_orders_via_browser = _fake_browser2
        try:
            first = ol.pending_orders_summary("SHOP_G", "ACC1")
            http_calls_after_first = FakeAPI.calls.get("SHOP_G", 0)
            with ol._lock:      # 只清缓存和浏览器间隔，保留熔断状态
                ol._cache.pop("SHOP_G", None)
                ol._cache_ttl.pop("SHOP_G", None)
                ol._browser_ts.pop("SHOP_G", None)
            second = ol.pending_orders_summary("SHOP_G", "ACC1", force_refresh=True)
            check("C9 首次拿到单", first.get("status") == "unique", first)
            check("C9 熔断后不再打 HTTP",
                  FakeAPI.calls.get("SHOP_G") == http_calls_after_first, FakeAPI.calls)
            check("C9 熔断期间直接走浏览器", second.get("status") == "unique", second)
            check("C9 浏览器结果缓存更久",
                  ol._cache_ttl.get("SHOP_G") == ol._BROWSER_CACHE_TTL, ol._cache_ttl)
        finally:
            ol._fetch_orders_via_browser = orig_browser2
            os.environ["ORDER_BROWSER_FALLBACK"] = "0"
    finally:
        order_api.MmsOrderAPI = orig


async def main_async():
    print("C6 平台 IO 走线程池：10 个慢调用不互相排队，也不卡事件循环")
    ticks = {"n": 0}

    async def ticker():
        while True:
            ticks["n"] += 1
            await asyncio.sleep(0.01)

    tick_task = asyncio.create_task(ticker())
    started = time.time()
    await asyncio.gather(*[vh._io(time.sleep, 0.5) for _ in range(10)])
    elapsed = time.time() - started
    tick_task.cancel()
    check("C6 10 个 0.5s 调用总耗时 < 1.6s（8 线程池，两批）", elapsed < 1.6, round(elapsed, 2))
    check("C6 期间事件循环没被卡住（ticker 有在跑）", ticks["n"] > 5, ticks["n"])


main_sync()
asyncio.run(main_async())

print("\n合计: %d 通过 / %d 失败" % (_results["pass"], _results["fail"]))
sys.exit(0 if _results["fail"] == 0 else 1)
