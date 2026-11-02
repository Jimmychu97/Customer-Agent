# -*- coding: utf-8 -*-
"""核销成功必须有报告：三种情形（离线，不联网）

背景（线上 2026-11-02 16:26）：验券成功、核销成功，但商品没同步到商品管理 →
查不到验机配置 → 代码静默跳过报告 → 客户只收到「核销成功」，以为报告随后会到。

覆盖：
  S1 商品未同步（无验机配置）→ 核销成功 + 明确告知「报告暂时没出」与下一步
  S2 商品+规格命中验机配置 → 报告由报告链发出（_do_verify 返回 None 表示已全部发送）
  S3 券里的规格写法与库不一致，但该商品只有一个规格 → 自动采用库规格，仍能出报告
"""
import asyncio
import os
import shutil
import sys
import tempfile

os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["LOG_FILE"] = "logs/test.log"
os.environ.setdefault("ORDER_BROWSER_FALLBACK", "0")
os.environ.setdefault("ORDER_HEARTBEAT", "0")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import loguru  # noqa: E402

_o = loguru.logger.add
loguru.logger.add = lambda *a, **k: (k.__setitem__("enqueue", False), _o(*a, **k))[1]

import tempfile as _tf  # noqa: E402

_tf.tempdir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "temp")
os.makedirs(_tf.tempdir, exist_ok=True)

from database.db_manager import DatabaseManager                     # noqa: E402
from database.models import GoodsCommit, Shop                        # noqa: E402
from core.di_container import container                              # noqa: E402
import Channel.pinduoduo.utils.API.mms_voucher_api as mv_api          # noqa: E402
import Channel.pinduoduo.utils.API.gsxunlocking_api as gsx_api        # noqa: E402
from Message.handlers import voucher_handler as vh                    # noqa: E402

SHOP, ACC, CUST = "S1", "A1", "C1"
GOODS_OK, GOODS_MISSING = "9300001", "9300002"
SPEC_IN_DB = "全面验机报告+人工解析"
SERIAL, COUPON = "VQ627F3QQH", "888401748943"

_results = {"pass": 0, "fail": 0}


def check(name, cond, detail=""):
    if cond:
        _results["pass"] += 1
        print(f"  [PASS] {name}")
    else:
        _results["fail"] += 1
        print(f"  [FAIL] {name}  <- {detail}")


class FakeVoucherAPI:
    """只记录调用，不联网"""

    preview_result = {}
    verify_result = {"success": True}

    def __init__(self, shop_id=None, user_id=None):
        self.shop_id = shop_id

    def preview(self, code):
        return dict(self.preview_result)

    def verification(self, code, store_id=0, order_sn=""):
        return dict(self.verify_result)


async def main():
    tmp = tempfile.mkdtemp(prefix="verify_report_")
    db = DatabaseManager(db_path=os.path.join(tmp, "t.db"))
    container.register_singleton(DatabaseManager, instance=db)

    with db.session_scope() as s:
        shop = Shop(channel_id=1, shop_id=SHOP, shop_name="t")
        s.add(shop)
        s.flush()
        # GOODS_OK：库里有一个规格「全面验机报告+人工解析」，配了苹果真心 1014
        s.add(GoodsCommit(shop_id=SHOP, goods_id=GOODS_OK, sku_id="k1",
                          spec_name=SPEC_IN_DB, zhenxin_server_id=1014, device_type="apple"))
        # GOODS_MISSING：商品根本没同步到商品管理

    mv_api.MmsVoucherAPI = FakeVoucherAPI
    gsx_api.query_gsxunlocking = lambda sn, srv: {"success": True, "result": {"型号": "iPhone 15"}}

    h = vh.VoucherDetectionHandler()
    sent_reports = []

    async def fake_sender(serial, goods_id, spec, report_data, order_id, shop_id, user_id,
                          from_uid, voucher_success_text=""):
        sent_reports.append({"serial": serial, "goods_id": goods_id, "spec": spec,
                             "report_data": report_data})

    h._query_gsxunlocking_and_send = fake_sender
    h._query_gsx_and_send = fake_sender
    h._record_message = lambda *a, **k: None
    h._record_outgoing = lambda *a, **k: None

    try:
        print("S1 商品未同步（查不到验机配置）")
        FakeVoucherAPI.preview_result = {
            "success": True, "goods_id": GOODS_MISSING, "goods_name": "gsx验机查询",
            "spec": "全面验机 推荐", "order_sn": "261102-095797921340",
            "order_amount": 990, "store_id": 1}
        sent_reports.clear()
        text, status = await h._do_verify(SERIAL, COUPON, SHOP, ACC, CUST, None)
        check("S1 核销成功", status == "success" and "核销成功" in (text or ""), (status, text))
        check("S1 明确告知报告还没出", "报告暂时没出" in (text or ""), text)
        check("S1 给出下一步（订单卡片/转人工）",
              "订单卡片" in (text or "") or "转人工" in (text or ""), text)
        check("S1 没有假装发了报告", sent_reports == [], sent_reports)

        print("S2 商品+规格命中验机配置 → 出报告")
        FakeVoucherAPI.preview_result = {
            "success": True, "goods_id": GOODS_OK, "goods_name": "gsx验机查询",
            "spec": SPEC_IN_DB, "order_sn": "261102-095797921341",
            "order_amount": 990, "store_id": 1}
        sent_reports.clear()
        text, status = await h._do_verify(SERIAL, COUPON + "1", SHOP, ACC, CUST, None)
        check("S2 报告链被调用 1 次", len(sent_reports) == 1, sent_reports)
        check("S2 报告数据非空", sent_reports and sent_reports[0]["report_data"],
              sent_reports)
        check("S2 返回值交给报告链（不再重复发）", text is None, text)

        print("S3 券规格与库规格写法不一致（商品只有唯一规格）")
        FakeVoucherAPI.preview_result = {
            "success": True, "goods_id": GOODS_OK, "goods_name": "gsx验机查询",
            "spec": "全面验机 推荐", "order_sn": "261102-095797921342",
            "order_amount": 990, "store_id": 1}
        sent_reports.clear()
        text, status = await h._do_verify(SERIAL, COUPON + "2", SHOP, ACC, CUST, None)
        check("S3 仍能出报告（按唯一规格取编码）", len(sent_reports) == 1, sent_reports)
        check("S3 采用库里的规格名", sent_reports and sent_reports[0]["spec"] == SPEC_IN_DB,
              sent_reports)
    finally:
        try:
            db.engine.dispose()
        except Exception:
            pass
        shutil.rmtree(tmp, ignore_errors=True)

    total = _results["pass"] + _results["fail"]
    print(f"\n核销出报告自测：{_results['pass']}/{total} 通过")
    return 0 if _results["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
