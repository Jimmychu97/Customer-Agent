# -*- coding: utf-8 -*-
"""订单查询 anti-content 自测（离线，不联网、不开浏览器）

背景（线上实证）：
    `recentOrderList` 每次请求都要带一个现算的 anti-content 签名。旧代码从 cookies 里取
    `anti_content`，但登录流程从来没写过这个键 —— 发出去的是空签名，平台固定回
    `40002 操作太过频繁`。于是订单列表/订单详情永远失败 → 发货拿不到 spec →
    报告出不来。这就是「订单查询老是出问题」的根因。

覆盖：
  Q1 新鲜签名在 TTL 内复用（只抓一次）
  Q2 超过 TTL 会重新抓
  Q3 抓不到时进入负缓存，不再连续开浏览器；仍返回旧签名
  Q4 invalidate 后强制重抓
  Q5 _build_headers 用新抓的签名（优先于 cookies 里的旧值）
  Q6 列表返回 40002 → 换新签名重试一次并成功
  Q7 列表仍被风控 → 用「合并发货页」接口按订单号命中，且带出 spec

运行：uv run python scripts/test_order_query.py
"""
import os
import sys
import time

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("LOG_FILE", "logs/test.log")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import loguru  # noqa: E402

_o = loguru.logger.add
loguru.logger.add = lambda *a, **k: (k.__setitem__("enqueue", False), _o(*a, **k))[1]

from Channel.pinduoduo import anti_content  # noqa: E402
from Channel.pinduoduo.utils.API import order_api as oa  # noqa: E402
from utils.logger_loguru import get_logger  # noqa: E402

ACCOUNT = "15521621375"
ORDER_SN = "261102-016871626300819"
SPEC = "GSX验机（全面信息）"

_results = {"pass": 0, "fail": 0}


def check(name, ok, detail=""):
    if ok:
        _results["pass"] += 1
        print("  [PASS] %s" % name)
    else:
        _results["fail"] += 1
        print("  [FAIL] %s %s" % (name, str(detail)[:300]))


class FakeApi(oa.MmsOrderAPI):
    """不碰网络：把 post 换成一串预置响应，并记录每次请求带的签名"""

    def __init__(self, posts):
        self.account_name = ACCOUNT
        self.proxy = None
        self.cookies = {"anti_content": "COOKIE_OLD"}
        self.logger = get_logger("TestOrderApi")
        self.max_retries = 1
        self.retry_delay = 0.0
        self.retry_backoff = 1.0
        self.channel_name = "pinduoduo"
        self.shop_id = "S1"
        self.user_id = "U1"
        self._posts = list(posts)
        self.calls = []

    def post(self, url, json_data=None, headers=None):
        self.calls.append({"url": url, "anti": (headers or {}).get("anti-content"),
                           "payload": dict(json_data or {})})
        return self._posts.pop(0) if self._posts else {}

    def _ensure_anti_content(self, force=False):  # 测试里不真去开浏览器
        return None


def rate_limited():
    return {"success": False, "errorCode": 40002, "errorMsg": "操作太过频繁，请稍后再试！"}


def list_ok(spec=SPEC):
    return {"success": True, "result": {
        "totalItemNum": 1,
        "pageItems": [{
            "order_sn": ORDER_SN, "goods_id": "1010294008341", "spec": spec,
            "goods_name": "gsx验机", "shipping_status": 0, "pay_status": 2,
            "order_status_str": "待发货",
        }],
    }}


def merge_ok(spec=SPEC):
    return {"success": True, "result": {
        "totalNo": 1,
        "mergeShippingMap": {"g1": [{
            "order_sn": ORDER_SN, "goods_id": "1010294008341", "spec": spec,
            "goods_name": "gsx验机", "shipping_status": 0, "pay_status": 2,
        }]},
    }}


def scenario_cache():
    print("Q1~Q4 anti-content 缓存 / 过期 / 负缓存 / 作废")
    calls = {"n": 0}

    def fake_capture(username, proxy=None, timeout=25.0):
        calls["n"] += 1
        return "SIG_%d" % calls["n"]

    orig = anti_content.capture
    anti_content.capture = fake_capture
    try:
        anti_content.invalidate(ACCOUNT)
        v1 = anti_content.ensure(ACCOUNT)
        v2 = anti_content.ensure(ACCOUNT)
        check("Q1 首次抓到签名", v1 == "SIG_1", v1)
        check("Q1 TTL 内复用缓存（只抓一次）", calls["n"] == 1 and v2 == "SIG_1", (calls, v2))

        anti_content._store[ACCOUNT]["ts"] = time.time() - (anti_content.DEFAULT_TTL + 1)
        v3 = anti_content.ensure(ACCOUNT)
        check("Q2 超过 TTL 重新抓", calls["n"] == 2 and v3 == "SIG_2", (calls, v3))

        anti_content.invalidate(ACCOUNT)
        anti_content.capture = lambda *a, **k: None
        anti_content._negative.pop(ACCOUNT, None)
        before = calls["n"]
        v4 = anti_content.ensure(ACCOUNT, force=True)
        v5 = anti_content.ensure(ACCOUNT, force=True)
        check("Q3 抓不到返回空（无旧值可退）", v4 == "" and v5 == "", (v4, v5))
        check("Q3 抓不到进入负缓存，不连续开浏览器", calls["n"] == before, calls)

        anti_content.capture = fake_capture
        anti_content.store(ACCOUNT, "SIG_KEEP")
        anti_content._negative.clear()
        anti_content.capture = lambda *a, **k: None
        v6 = anti_content.ensure(ACCOUNT, force=True)
        check("Q3 抓不到时退回旧签名", v6 == "SIG_KEEP", v6)

        anti_content.capture = fake_capture
        calls["n"] = 0
        anti_content.invalidate(ACCOUNT)
        anti_content._negative.clear()
        v7 = anti_content.ensure(ACCOUNT, force=True)
        check("Q4 invalidate 后强制重抓", v7 == "SIG_1" and calls["n"] == 1, (v7, calls))
    finally:
        anti_content.capture = orig
        anti_content.invalidate(ACCOUNT)
        anti_content._negative.clear()


def scenario_headers():
    print("Q5 请求头用新签名")
    anti_content.store(ACCOUNT, "SIG_FRESH")
    try:
        api = FakeApi([])
        headers = api._build_headers()
        check("Q5 anti-content 用新抓的签名（不是 cookies 旧值）",
              headers.get("anti-content") == "SIG_FRESH", headers.get("anti-content"))
        check("Q5 没有新签名时退回 cookies",
              _headers_without_cache(api) == "COOKIE_OLD", None)
    finally:
        anti_content.invalidate(ACCOUNT)


def _headers_without_cache(api):
    saved = anti_content.current
    anti_content.current = lambda *a, **k: ""
    try:
        return api._build_headers().get("anti-content")
    finally:
        anti_content.current = saved


def scenario_retry():
    print("Q6 40002 时换新签名重试一次")
    anti_content.store(ACCOUNT, "SIG_OLD")
    papers = [rate_limited(), list_ok()]
    api = FakeApi(papers)
    # 只有「40002 后强制刷新」才会拿到新签名；普通 ensure 保持旧值
    api._ensure_anti_content = (
        lambda force=False: anti_content.store(ACCOUNT, "SIG_NEW") if force else None)
    try:
        result = api.list_orders(page_num=1, page_size=50)
        check("Q6 重试后查询成功", result.get("success") is True, result)
        check("Q6 第一次用的是旧签名", api.calls[0]["anti"] == "SIG_OLD", api.calls[:1])
        check("Q6 重试用的是新签名", api.calls[1]["anti"] == "SIG_NEW", api.calls[1:])
        check("Q6 订单带出 spec", (result.get("orders") or [{}])[0].get("spec") == SPEC, result)
    finally:
        anti_content.invalidate(ACCOUNT)


def scenario_merge_fallback():
    print("Q7 列表被风控 → 合并发货页兜底命中订单号")
    anti_content.store(ACCOUNT, "SIG_A")
    papers = [rate_limited(), rate_limited(), merge_ok()]
    api = FakeApi(papers)
    api._ensure_anti_content = lambda force=False: None
    try:
        result = api.get_order_by_sn(ORDER_SN)
        check("Q7 兜底后按订单号命中", result.get("success") is True, result)
        order = result.get("order") or {}
        check("Q7 命中订单带 goods_id/spec",
              order.get("goods_id") == "1010294008341" and order.get("spec") == SPEC, order)
        check("Q7 走了合并发货页接口",
              any(c["url"] == api.MERGE_ORDER_LIST_URL for c in api.calls),
              [c["url"] for c in api.calls])
    finally:
        anti_content.invalidate(ACCOUNT)


def scenario_page_size():
    print("Q9 按订单号查详情：pageSize 用 50，平台嫌大自动降到 20（线上 100 恒失败）")
    anti_content.store(ACCOUNT, "SIG_A")
    too_big = {"success": False, "errorCode": 4000000,
               "errorMsg": "单页数据量过大或非法"}
    papers = [too_big, list_ok()]
    api = FakeApi(papers)
    api._ensure_anti_content = lambda force=False: None
    try:
        result = api.get_order_by_sn(ORDER_SN)
        sizes = [c["payload"].get("pageSize") for c in api.calls
                 if c["url"] == api.ORDER_LIST_URL]
        check("Q9 首次 pageSize=50（不是 100）", sizes and sizes[0] == 50, sizes)
        check("Q9 平台嫌大后降到 20 重试", 20 in sizes, sizes)
        check("Q9 降级后命中订单并带出 spec",
              (result.get("order") or {}).get("spec") == SPEC, result)
    finally:
        anti_content.invalidate(ACCOUNT)


def scenario_pending_rule():
    print("Q10 待发货判定：中文状态兜底（线上出现过原始 50 笔 / 待发货 0 笔）")
    api = FakeApi([])
    numeric = api._normalize_order({"order_sn": "S1", "shipping_status": 0, "pay_status": 2})
    text_only = api._normalize_order({"order_sn": "S2", "shipping_status": 5,
                                      "pay_status": 5, "order_status_str": "待发货"})
    shipped = api._normalize_order({"order_sn": "S3", "shipping_status": 1, "pay_status": 2,
                                    "order_status_str": "已发货"})
    check("Q10 数字口径照旧识别待发货", numeric.get("pending_shipment") is True, numeric)
    check("Q10 只有中文状态也能识别待发货",
          text_only.get("pending_shipment") is True, text_only)
    check("Q10 已发货不会被误判", shipped.get("pending_shipment") is False, shipped)


def scenario_background():
    print("Q8 后台补签名：不阻塞调用方")
    orig = anti_content.capture
    anti_content.capture = lambda *a, **k: "SIG_BG"
    try:
        anti_content.invalidate(ACCOUNT)
        anti_content._negative.clear()
        started = time.time()
        anti_content.ensure_background(ACCOUNT)
        elapsed = time.time() - started
        check("Q8 ensure_background 立刻返回", elapsed < 0.2, elapsed)
        for _ in range(40):
            if anti_content.current(ACCOUNT):
                break
            time.sleep(0.05)
        check("Q8 后台线程最终填入签名", anti_content.current(ACCOUNT) == "SIG_BG",
              anti_content.current(ACCOUNT))
    finally:
        anti_content.capture = orig
        anti_content.invalidate(ACCOUNT)
        anti_content._negative.clear()


def main():
    scenario_cache()
    scenario_headers()
    scenario_retry()
    scenario_merge_fallback()
    scenario_page_size()
    scenario_pending_rule()
    scenario_background()
    print("\n结果: %d 通过, %d 失败" % (_results["pass"], _results["fail"]))
    return 0 if _results["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
