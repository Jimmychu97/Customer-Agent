# -*- coding: utf-8 -*-
"""核销流程防重复建单 / 防空参核销 自测脚本（离线，不联网）

覆盖场景：
1. 文本卡券 -> 同卡券图片 -> 序列号文本：全程只有 1 条 Order，核销 1 次
2. 文本卡券 -> 再发一次同卡券文本：仍 1 条 Order，不调用核销接口
3. 先发序列号 -> 再发一次序列号：仍 1 条 Order，不调用核销接口
4. 卡券+序列号核销失败(验券失败) -> 再发同卡券：不新建 Order，序列号保留
5. 卡券+序列号核销失败(序列号查询失败) -> 序列号被清空、卡券保留 -> 新序列号复用同一单
6. 待核销单超过续接窗口(11 分钟)后再发同卡券：仍复用同一单
7. 不同卡券 -> 各自建单（不同券属于不同订单）
8. 任何情况下核销接口都不会以空参数被调用

运行：PYTHONIOENCODING=utf-8 python scripts/test_voucher_dedup.py
"""
import asyncio
import os
import sys
import tempfile
import shutil

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime as dt, timedelta

from database.db_manager import DatabaseManager
from core.di_container import container
from database.models import Order
from bridge.context import Context, ContextType, ChannelType
from Message.handlers.voucher_handler import VoucherDetectionHandler

SHOP_ID = "SHOP1"
USER_ID = "ACC1"
CUST_UID = "CUST1"
COUPON = "123456789012"
COUPON2 = "999888777666"
SERIAL = "ABCDE12345"
SERIAL2 = "XYZAB67890"
META = {"shop_id": SHOP_ID, "user_id": USER_ID, "from_uid": CUST_UID}

# ===== 以下样本直接取自服务器日志 C:\Users\dmnjimmy\Downloads\app.log（2026-09-23） =====
# 15 位但不满足 Luhn 的数字：日志里这类消息曾触发"券码不能为空"的空参数核销
JUNK_15 = "123456789012345"
# 08:25:26 客户发来的两个 IMEI，旧代码把第二个的尾部当成了卡券号 549350251721
TWO_IMEIS = "355549350322480 355549350251721"
# 08:44:18 / 09:57:18 卡券图片的 OCR 结果
COUPON_IMG_OCR = (
    "08:31\n\n5G A\n\n卡券详情\n\n温馨提示：电子凭证未验证时请不要随意透露给其他人\n\n深度\n创新\n\n"
    "苹果GSX验机手机鉴定官换机\n翻新机序列号GXS查询真伪\niPad查验检测\n未使用\n"
    "有效日期：2026.09.23至2026.09.30\n9970 0179 1266 复制"
)
# 09-22 07:38 关于本机截图 OCR（序列号 D06X14PJCF 前面紧邻型号 MQ1C3CH/A）
ABOUT_OCR = (
    "关于本机\niPhone\n26.6.2\niPhone 14 Pro\nMQ1C3CH/A\nD06X14PJCF\n名称\niOS版本\n型号名称\n型号\n"
    "序列号\n部件与维修历史\n歌曲\n视频\n照片\n总容量\n可用容量\n256 GB\n238.74 GB\n08:31"
)

_results = {"pass": 0, "fail": 0}


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        _results["pass"] += 1
        print("  [PASS] %s" % name)
    else:
        _results["fail"] += 1
        print("  [FAIL] %s %s" % (name, detail))


def make_tmp_db():
    tmpdir = tempfile.mkdtemp(prefix="voucher_dedup_")
    db = DatabaseManager(db_path=os.path.join(tmpdir, "test.db"))
    container.register_singleton(DatabaseManager, instance=db)
    return db, tmpdir


def make_context(content: str, ctype: ContextType = ContextType.TEXT) -> Context:
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


class Harness:
    """构造一个只做记录、不联网的处理器"""

    def __init__(self, db, verify_mode: str = "success"):
        self.db = db
        self.verify_mode = verify_mode
        self.sent = []
        self.verify_calls = []
        self.handler = VoucherDetectionHandler()

        async def fake_send(shop_id, user_id, from_uid, text):
            self.sent.append(text)
            return True

        self.handler._send = fake_send
        self.handler._record_message = lambda *a, **k: None

        async def fake_verify(sn, coupon, shop_id, user_id, from_uid, order_id):
            self.verify_calls.append({"sn": sn, "coupon": coupon, "order_id": order_id})
            if self.verify_mode == "success":
                self.handler._update_order_status(order_id, 1)
                return "核销成功！", "success"
            if self.verify_mode == "fail_preview":
                self.handler._update_order_status(order_id, 2)
                return "卡券核销失败：券无效", "failed"
            if self.verify_mode == "fail_gsx":
                self.handler._clear_order_sn(order_id)
                return "序列号/IMEI 有误", "failed"
            return "", "error"

        self.handler._do_verify = fake_verify

    def reset(self, verify_mode: str = None):
        if verify_mode:
            self.verify_mode = verify_mode
        with self.db.session_scope() as session:
            session.query(Order).delete()
        self.sent.clear()
        self.verify_calls.clear()

    def orders(self):
        with self.db.session_scope() as session:
            return [
                (o.id, o.sn_imei or "", o.coupons or "", o.status)
                for o in session.query(Order).order_by(Order.id).all()
            ]

    def age_orders(self, minutes: int):
        with self.db.session_scope() as session:
            for o in session.query(Order).all():
                o.created_at = dt.now() - timedelta(minutes=minutes)

    async def send_text(self, content: str):
        return await self.handler.handle(make_context(content), dict(META))

    async def send_image(self, sn, coupon):
        meta = dict(META)
        meta["_image_sn"] = sn
        meta["_image_coupon"] = coupon
        return await self.handler.handle(make_context(""), meta)

    def assert_no_empty_verify(self, label: str):
        bad = [c for c in self.verify_calls if not c["sn"] or not c["coupon"]]
        check("%s: 核销调用参数非空" % label, not bad, "bad=%s" % bad)


async def scenario_1(h: Harness):
    print("S1 文本卡券 -> 同卡券图片 -> 序列号文本")
    h.reset("success")
    await h.send_text(COUPON)
    check("S1: 发卡券后 1 条单", len(h.orders()) == 1, h.orders())
    await h.send_image(None, COUPON)
    check("S1: 同卡券图片后仍 1 条单", len(h.orders()) == 1, h.orders())
    check("S1: 图片重复提交不调用核销", len(h.verify_calls) == 0, h.verify_calls)
    await h.send_text(SERIAL)
    rows = h.orders()
    check("S1: 补序列号后仍 1 条单", len(rows) == 1, rows)
    check("S1: 核销 1 次", len(h.verify_calls) == 1, h.verify_calls)
    check("S1: 单内信息完整", bool(rows[0][1]) and bool(rows[0][2]), rows)
    h.assert_no_empty_verify("S1")


async def scenario_2(h: Harness):
    print("S2 文本卡券 -> 再发一次同卡券")
    h.reset("success")
    await h.send_text(COUPON)
    await h.send_text(COUPON)
    check("S2: 仍 1 条单", len(h.orders()) == 1, h.orders())
    check("S2: 不调用核销接口", len(h.verify_calls) == 0, h.verify_calls)
    check("S2: 回复为引导语", "请继续提供" in (h.sent[-1] or ""), h.sent[-1:])
    h.assert_no_empty_verify("S2")


async def scenario_3(h: Harness):
    print("S3 先序列号 -> 再发一次序列号")
    h.reset("success")
    await h.send_text(SERIAL)
    await h.send_text(SERIAL)
    check("S3: 仍 1 条单", len(h.orders()) == 1, h.orders())
    check("S3: 不调用核销接口", len(h.verify_calls) == 0, h.verify_calls)
    h.assert_no_empty_verify("S3")


async def scenario_4(h: Harness):
    print("S4 卡券+序列号(验券失败) -> 再发同卡券")
    h.reset("fail_preview")
    await h.send_text("序列号 %s 卡券 %s" % (SERIAL, COUPON))
    rows = h.orders()
    check("S4: 首次 1 条单", len(rows) == 1, rows)
    check("S4: 卡券问题不清空序列号", rows[0][1] == SERIAL, rows)
    await h.send_text(COUPON)
    rows2 = h.orders()
    check("S4: 再发同卡券不新建单", len(rows2) == 1, rows2)
    h.assert_no_empty_verify("S4")


async def scenario_5(h: Harness):
    print("S5 卡券+序列号(序列号查询失败) -> 新序列号复用同一单")
    h.reset("fail_gsx")
    await h.send_text("序列号 %s 卡券 %s" % (SERIAL, COUPON))
    rows = h.orders()
    check("S5: 1 条单", len(rows) == 1, rows)
    check("S5: 序列号被清空", rows[0][1] == "", rows)
    check("S5: 卡券保留", rows[0][2] == COUPON, rows)
    check("S5: 状态仍待核销", rows[0][3] == 0, rows)
    h.verify_mode = "success"
    await h.send_text(SERIAL2)
    rows2 = h.orders()
    check("S5: 新序列号仍 1 条单", len(rows2) == 1, rows2)
    check("S5: 新序列号写入同一单", rows2[0][1] == SERIAL2, rows2)
    check("S5: 核销成功", rows2[0][3] == 1, rows2)
    h.assert_no_empty_verify("S5")


async def scenario_6(h: Harness):
    print("S6 待核销单超过续接窗口后再发同卡券")
    h.reset("success")
    await h.send_text(COUPON)
    h.age_orders(11)
    await h.send_text(COUPON)
    check("S6: 仍 1 条单", len(h.orders()) == 1, h.orders())
    h.assert_no_empty_verify("S6")


async def scenario_7(h: Harness):
    print("S7 不同卡券 -> 各自建单")
    h.reset("success")
    await h.send_text(COUPON)
    await h.send_text(COUPON2)
    rows = h.orders()
    check("S7: 两张不同卡券 2 条单", len(rows) == 2, rows)
    check("S7: 卡券分别落库", sorted(r[2] for r in rows) == sorted([COUPON, COUPON2]), rows)


async def scenario_8(db):
    print("S8 空参数不触发核销接口")
    handler = VoucherDetectionHandler()
    handler._record_message = lambda *a, **k: None
    called = {"n": 0}

    def boom(*a, **k):
        called["n"] += 1
        raise AssertionError("空参数不应调用核销接口")

    handler._query_and_verify = boom
    reply, status = await handler._do_verify("", COUPON, SHOP_ID, USER_ID, CUST_UID, 1)
    check("S8: 空序列号被拦截", called["n"] == 0 and reply == "", (called, reply))
    reply2, status2 = await handler._do_verify(SERIAL, "", SHOP_ID, USER_ID, CUST_UID, 1)
    check("S8: 空卡券被拦截", called["n"] == 0 and reply2 == "", (called, reply2))


class _FakeBot:
    """只返回固定识别文本的假 Bot，用于跑通图片识别路径"""

    def __init__(self, recognized_text: str):
        self.recognized_text = recognized_text

    async def recognize_image(self, image_url, context):
        return self.recognized_text


class _ClassPatch:
    """临时替换 VoucherDetectionHandler 的发送/建单/核销方法（图片路径内部会 new 处理器）"""

    def __init__(self, h: "Harness"):
        self.h = h

    def __enter__(self):
        from Message.handlers import voucher_handler as vh
        self.vh = vh
        self.orig = (
            vh.VoucherDetectionHandler._send,
            vh.VoucherDetectionHandler._record_message,
            vh.VoucherDetectionHandler._do_verify,
        )

        async def fake_send(inst, shop_id, user_id, from_uid, text):
            self.h.sent.append(text)
            return True

        async def fake_verify(inst, sn, coupon, shop_id, user_id, from_uid, order_id):
            self.h.verify_calls.append({"sn": sn, "coupon": coupon, "order_id": order_id})
            inst._update_order_status(order_id, 1 if self.h.verify_mode == "success" else 2)
            return ("核销成功！" if self.h.verify_mode == "success" else "核销失败"), self.h.verify_mode

        vh.VoucherDetectionHandler._send = fake_send
        vh.VoucherDetectionHandler._record_message = lambda *a, **k: None
        vh.VoucherDetectionHandler._do_verify = fake_verify
        return self

    def __exit__(self, *exc):
        (self.vh.VoucherDetectionHandler._send,
         self.vh.VoucherDetectionHandler._record_message,
         self.vh.VoucherDetectionHandler._do_verify) = self.orig
        return False


async def scenario_9(h: Harness):
    """走 ai_handler 的图片路径（客户发图片卡券的场景）"""
    from Message.handlers.ai_handler import AIReplyHandler
    from Message.handlers import voucher_handler as vh

    print("S9 图片识别路径：重复卡券 / 序列号图片")
    orig_send = vh.VoucherDetectionHandler._send
    orig_record = vh.VoucherDetectionHandler._record_message
    orig_verify = vh.VoucherDetectionHandler._do_verify

    async def fake_send(self, shop_id, user_id, from_uid, text):
        h.sent.append(text)
        return True

    async def fake_verify(self, sn, coupon, shop_id, user_id, from_uid, order_id):
        h.verify_calls.append({"sn": sn, "coupon": coupon, "order_id": order_id})
        self._update_order_status(order_id, 1)
        return "核销成功！", "success"

    vh.VoucherDetectionHandler._send = fake_send
    vh.VoucherDetectionHandler._record_message = lambda *a, **k: None
    vh.VoucherDetectionHandler._do_verify = fake_verify

    try:
        # S9a: 已发过卡券，图片里还是同一张卡券 -> 不得新建单、不得调用核销
        h.reset("success")
        await h.send_text(COUPON)
        ai = AIReplyHandler(bot=_FakeBot(COUPON))
        reply = await ai._get_ai_reply_with_image(
            "http://img", make_context("", ContextType.IMAGE), dict(META)
        )
        check("S9a: 图片卡券不新建单", len(h.orders()) == 1, h.orders())
        check("S9a: 图片卡券不调用核销", len(h.verify_calls) == 0, h.verify_calls)
        check("S9a: 由核销处理器接管", reply is None, reply)

        # S9b: 已发过卡券，图片是序列号 -> 复用同一单并核销一次
        h.reset("success")
        await h.send_text(COUPON)
        ai = AIReplyHandler(bot=_FakeBot("序列号 %s" % SERIAL))
        await ai._get_ai_reply_with_image("http://img", make_context("", ContextType.IMAGE), dict(META))
        rows = h.orders()
        check("S9b: 仍 1 条单", len(rows) == 1, rows)
        check("S9b: 序列号写入", rows and rows[0][1] == SERIAL, rows)
        check("S9b: 核销 1 次", len(h.verify_calls) == 1, h.verify_calls)

        # S9c: 图片里同时有卡券和序列号 -> 建单并核销一次
        h.reset("success")
        ai = AIReplyHandler(bot=_FakeBot("序列号 %s 卡券 %s" % (SERIAL, COUPON)))
        await ai._get_ai_reply_with_image("http://img", make_context("", ContextType.IMAGE), dict(META))
        rows = h.orders()
        check("S9c: 1 条单", len(rows) == 1, rows)
        check("S9c: 核销 1 次", len(h.verify_calls) == 1, h.verify_calls)
        h.assert_no_empty_verify("S9")
    finally:
        vh.VoucherDetectionHandler._send = orig_send
        vh.VoucherDetectionHandler._record_message = orig_record
        vh.VoucherDetectionHandler._do_verify = orig_verify


async def scenario_10(h: Harness):
    """按服务器 09-23 日志里 from_uid=4111009570 的真实顺序回放"""
    from Message.handlers.ai_handler import AIReplyHandler

    print("S10 服务器日志回放（09-23 08:13~09:57 同一客户）")
    h.reset("fail_preview")
    await h.send_text("G2KN4GP92Q")
    await h.send_text(JUNK_15)
    await h.send_text("355549350251721")
    await h.send_text(JUNK_15)
    await h.send_text("997001791266")
    await h.send_text("355549350322480")
    await h.send_text(TWO_IMEIS)
    await h.send_text("997001791266")
    with _ClassPatch(h):
        for _ in range(2):  # 08:44 与 09:57 两次发同一张卡券图片
            ai = AIReplyHandler(bot=_FakeBot(COUPON_IMG_OCR))
            await ai._get_ai_reply_with_image(
                "http://img", make_context("", ContextType.IMAGE), dict(META)
            )

    rows = h.orders()
    coupon_rows = [r for r in rows if r[2] == "997001791266"]
    check("S10: 同一张卡券只有 1 行", len(coupon_rows) == 1, rows)
    check("S10: 未把 IMEI 尾部当卡券建单", not [r for r in rows if r[2] == "549350251721"], rows)
    check("S10: 全表只有 1 行带卡券号", len([r for r in rows if r[2]]) == 1, rows)
    check("S10: 卡券行最终为核销失败", bool(coupon_rows) and coupon_rows[0][3] == 2, rows)
    h.assert_no_empty_verify("S10")

    # 08:25:26 的原始场景：没有任何待核销单时，两个 IMEI 不能被拆成"序列号 + 假卡券"
    h.reset("fail_preview")
    await h.send_text(TWO_IMEIS)
    rows = h.orders()
    check("S10b: 两个 IMEI 只建 1 行且无卡券号",
          len(rows) == 1 and rows[0][2] == "" and rows[0][1] == "355549350322480", rows)
    check("S10b: 不触发核销", len(h.verify_calls) == 0, h.verify_calls)

    # 08:13:15 / 08:16:15 的原始场景：15 位非 Luhn 数字不应触发空参数核销
    h.reset("fail_preview")
    await h.send_text(JUNK_15)
    rows = h.orders()
    check("S10c: 无效 15 位数字不建单", len(rows) == 0, rows)
    check("S10c: 不触发核销", len(h.verify_calls) == 0, h.verify_calls)


def scenario_11():
    """提取逻辑：全部用日志里的真实样本"""
    from Message.handlers.voucher_handler import (
        _extract_serial, _extract_coupon, _extract_imei, _luhn_check,
    )

    print("S11 提取逻辑（日志真实样本）")
    check("S11: IMEI2 未通过 Luhn（样本有效）", not _luhn_check(JUNK_15))
    check("S11: 型号+序列号连写可识别", _extract_serial("MQ1C3CH/A D06X14PJCF") == "D06X14PJCF",
          _extract_serial("MQ1C3CH/A D06X14PJCF"))
    check("S11: 关于本机 OCR 可识别序列号", _extract_serial(ABOUT_OCR) == "D06X14PJCF",
          _extract_serial(ABOUT_OCR))
    check("S11: 两个 IMEI 不产生卡券号", _extract_coupon(TWO_IMEIS) is None, _extract_coupon(TWO_IMEIS))
    check("S11: 单个 IMEI 不产生卡券号", _extract_coupon("355549350251721") is None,
          _extract_coupon("355549350251721"))
    check("S11: 15 位非 Luhn 数字不产生卡券号", _extract_coupon(JUNK_15) is None, _extract_coupon(JUNK_15))
    check("S11: 卡券图片 OCR 仍能识别卡券", _extract_coupon(COUPON_IMG_OCR) == "997001791266",
          _extract_coupon(COUPON_IMG_OCR))
    check("S11: 16 位粘连数字串仍取尾部", _extract_coupon("1234997001791266") == "997001791266",
          _extract_coupon("1234997001791266"))
    check("S11: IMEI 提取不受影响", _extract_imei(TWO_IMEIS) == "355549350322480",
          _extract_imei(TWO_IMEIS))
    check("S11: 带空格的序列号仍能识别", _extract_serial("D06 X14 PJCF") == "D06X14PJCF",
          _extract_serial("D06 X14 PJCF"))


async def scenario_12(h: Harness):
    """客户纠正序列号/IMEI：文字和图片识别结果一视同仁，都不能再新建单号"""
    from Message.handlers.ai_handler import AIReplyHandler

    print("S12 序列号/IMEI 纠正（文本 + 图片识别）")
    h.reset("success")
    await h.send_text("G2KN4GP92Q")          # 客户先发错
    await h.send_text("355549350251721")     # 文字纠正
    rows = h.orders()
    check("S12: 文字纠正不新建单", len(rows) == 1, rows)
    check("S12: 文字纠正已写入", bool(rows) and rows[0][1] == "355549350251721", rows)
    check("S12: 已告知客户更新", any("已更新您的序列号" in t for t in h.sent), h.sent)

    # 图片识别出来的序列号同样能纠正（关于本机截图 OCR）
    with _ClassPatch(h):
        ai = AIReplyHandler(bot=_FakeBot(ABOUT_OCR))
        await ai._get_ai_reply_with_image(
            "http://img", make_context("", ContextType.IMAGE), dict(META)
        )
    rows = h.orders()
    check("S12: 图片纠正不新建单", len(rows) == 1, rows)
    check("S12: 图片识别的序列号已写入", bool(rows) and rows[0][1] == "D06X14PJCF", rows)
    check("S12: 图片纠正也告知客户", any("D06X14PJCF" in t and "已更新" in t for t in h.sent), h.sent)

    # 纠正后再发卡券 → 用纠正后的序列号核销，且仍然只有 1 条单
    await h.send_text("997001791266")
    rows = h.orders()
    check("S12: 补齐卡券后仍只有 1 条单", len(rows) == 1, rows)
    check("S12: 用纠正后的序列号核销",
          bool(h.verify_calls) and h.verify_calls[-1]["sn"] == "D06X14PJCF"
          and h.verify_calls[-1]["coupon"] == "997001791266", h.verify_calls)
    check("S12: 核销成功写回状态", bool(rows) and rows[0][3] == 1, rows)
    h.assert_no_empty_verify("S12")

    # 已核销成功的单不能被失败状态回退（重发同一张券走复核流程）
    h.verify_mode = "fail_preview"
    await h.send_text("997001791266")
    rows = h.orders()
    check("S12: 复核失败不回退成功状态", bool(rows) and rows[0][3] == 1, rows)
    check("S12: 复核不新建单", len(rows) == 1, rows)

    # 已核销成功的单也不允许被改写序列号（成功那次的 GSX 结果是最终凭据）
    from Message.handlers.voucher_handler import _luhn_check

    def _luhn_fix(prefix14: str) -> str:
        for d in "0123456789":
            if _luhn_check(prefix14 + d):
                return prefix14 + d
        return prefix14 + "0"

    other_imei = _luhn_fix("35554935099999")
    h.verify_mode = "success"
    await h.send_text(f"{other_imei} 997001791266")
    rows = h.orders()
    check("S12: 已成功单不被改写序列号",
          len(rows) == 1 and rows[0][1] == "D06X14PJCF", rows)
    check("S12: 已成功单不新建单", len(rows) == 1, rows)


async def main():
    db, tmpdir = make_tmp_db()
    try:
        h = Harness(db)
        await scenario_1(h)
        await scenario_2(h)
        await scenario_3(h)
        await scenario_4(h)
        await scenario_5(h)
        await scenario_6(h)
        await scenario_7(h)
        await scenario_8(db)
        await scenario_9(h)
        await scenario_10(h)
        await scenario_12(h)
        scenario_11()
    finally:
        try:
            db.engine.dispose()
        except Exception:
            pass
        shutil.rmtree(tmpdir, ignore_errors=True)

    print("")
    print("RESULT: pass=%d fail=%d" % (_results["pass"], _results["fail"]))
    return 0 if _results["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
