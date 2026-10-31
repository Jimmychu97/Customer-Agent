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
COUPON3 = "555666777888"
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
    print("S7 卡券号纠正 / 第二笔订单")
    # 7a：同一张待核销单（还没有序列号）又发来另一张卡券 → 按"纠正"处理，仍是 1 条单
    h.reset("success")
    await h.send_text(COUPON)
    await h.send_text(COUPON2)
    rows = h.orders()
    check("S7a: 待核销单内改卡券不新建单", len(rows) == 1, rows)
    check("S7a: 卡券号纠正为最新值", bool(rows) and rows[0][2] == COUPON2, rows)
    check("S7a: 已告知客户改券", any("已更新您的卡券号" in t for t in h.sent), h.sent)

    # 7b：上一单已核销成功后再来一张新卡券 → 属于第二笔，正常新建
    h.reset("success")
    await h.send_text(COUPON)
    await h.send_text(SERIAL)
    rows = h.orders()
    check("S7b: 第一单已核销成功", len(rows) == 1 and rows[0][3] == 1, rows)
    await h.send_text(COUPON2)
    rows = h.orders()
    check("S7b: 新卡券另建一单", len(rows) == 2, rows)
    check("S7b: 成功单的卡券号未被改写",
          len(rows) == 2 and rows[0][2] == COUPON and rows[1][2] == COUPON2, rows)


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


async def scenario_13(h: Harness):
    """卡券号打错后纠正（含已核销成功单的保护）"""
    print("S13 卡券号纠正")
    typo = COUPON[:-1]  # 少一位，仍是合法位数（11位）的卡券号
    h.reset("fail_preview")   # 打错的券在拼多多侧验券失败
    await h.send_text(SERIAL)
    await h.send_text(typo)
    rows = h.orders()
    check("S13: 打错的卡券号先落库并核销失败",
          len(rows) == 1 and rows[0][2] == typo and rows[0][1] == SERIAL and rows[0][3] == 2, rows)

    h.verify_mode = "success"
    await h.send_text(COUPON)  # 客户改对
    rows = h.orders()
    check("S13: 纠正不新建单", len(rows) == 1, rows)
    check("S13: 卡券号已改为正确值", bool(rows) and rows[0][2] == COUPON, rows)
    check("S13: 已告知客户改券", any("已更新您的卡券号" in t for t in h.sent), h.sent)
    check("S13: 用纠正后的卡券号核销",
          bool(h.verify_calls) and h.verify_calls[-1]["coupon"] == COUPON
          and h.verify_calls[-1]["sn"] == SERIAL, h.verify_calls)
    check("S13: 核销成功", bool(rows) and rows[0][3] == 1, rows)

    # 已核销成功的单：带着同一序列号又发来别的卡券号 →
    # 已成功单的凭据绝不被改写，但新券属于第二笔，必须另建一单并用新券核销
    await h.send_text(f"{SERIAL} {COUPON2}")
    rows = h.orders()
    check("S13: 成功单凭据不被改写", bool(rows) and rows[0][2] == COUPON and rows[0][3] == 1, rows)
    check("S13: 新券另建一单", len(rows) == 2 and rows[1][2] == COUPON2, rows)
    check("S13: 新券用新券核销",
          bool(h.verify_calls) and h.verify_calls[-1]["coupon"] == COUPON2, h.verify_calls)


def scenario_14():
    """卡券号格式与位数规则（沿用 git 历史约定）"""
    from Message.handlers.voucher_handler import _extract_coupon, _extract_coupon_strict

    print("S14 卡券号格式/位数规则")
    check("S14: 9 位不提取", _extract_coupon("123456789") is None, _extract_coupon("123456789"))
    check("S14: 10 位（下限）提取", _extract_coupon("1234567890") == "1234567890")
    check("S14: 12 位提取", _extract_coupon("997001791266") == "997001791266")
    check("S14: 14 位（上限）提取", _extract_coupon("12345678901234") == "12345678901234")
    check("S14: 15 位按 IMEI 不提取", _extract_coupon("355549350251721") is None,
          _extract_coupon("355549350251721"))
    check("S14: 空格分组提取", _extract_coupon("9970 0179 1266") == "997001791266")
    check("S14: 连字符分组提取", _extract_coupon("9970-0179-1266") == "997001791266")
    check("S14: 手机号不提取", _extract_coupon("13676048541") is None)
    check("S14: 订单号不提取", _extract_coupon("260922-544452154900751") is None,
          _extract_coupon("260922-544452154900751"))
    check("S14: 待补卡券严格模式认 13 位",
          _extract_coupon_strict("1234567890123") == "1234567890123")
    check("S14: 待补卡券严格模式不认 14 位",
          _extract_coupon_strict("12345678901234") is None,
          _extract_coupon_strict("12345678901234"))
    check("S14: 严格模式下 IMEI 不提取", _extract_coupon_strict("355549350251721") is None)


async def scenario_15(h: Harness):
    """第一张券核销成功后再来第二张新券（同一台机器/同一序列号）——必须另建单并用新券核销

    这是线上真实反馈的 BUG：旧逻辑在「序列号相同」时直接复用已核销成功的单，
    把新卡券当成"误发"忽略掉，于是又拿旧券去核销 -> 券已使用 -> 客户看到"核销失败"。
    """
    from Message.handlers.ai_handler import AIReplyHandler

    print("S15 已核销成功后再来第二张新卡券")

    # 15a：同一序列号 + 新卡券（文本）
    h.reset("success")
    await h.send_text("序列号 %s 卡券 %s" % (SERIAL, COUPON))
    rows = h.orders()
    check("S15a: 第一单核销成功", len(rows) == 1 and rows[0][3] == 1 and rows[0][2] == COUPON, rows)

    h.verify_calls.clear()
    await h.send_text("序列号 %s 卡券 %s" % (SERIAL, COUPON2))
    rows = h.orders()
    check("S15a: 新卡券另建一单（共 2 单）", len(rows) == 2, rows)
    check("S15a: 第二单卡券号是新券", len(rows) == 2 and rows[1][2] == COUPON2, rows)
    check("S15a: 核销用的是新券而不是旧券",
          len(h.verify_calls) == 1 and h.verify_calls[-1]["coupon"] == COUPON2, h.verify_calls)
    check("S15a: 第一单凭据未被改写",
          len(rows) == 2 and rows[0][2] == COUPON and rows[0][3] == 1, rows)

    # 15b：只发新卡券（无序列号）
    h.reset("success")
    await h.send_text("序列号 %s 卡券 %s" % (SERIAL, COUPON))
    h.verify_calls.clear()
    await h.send_text(COUPON2)
    rows = h.orders()
    check("S15b: 只发新券也另建一单", len(rows) == 2, rows)
    check("S15b: 新券暂不核销（还缺序列号）", len(h.verify_calls) == 0, h.verify_calls)
    check("S15b: 提示补序列号", "请继续提供" in (h.sent[-1] or ""), h.sent[-1:])

    # 15c：新卡券 + 新序列号
    h.reset("success")
    await h.send_text("序列号 %s 卡券 %s" % (SERIAL, COUPON))
    h.verify_calls.clear()
    await h.send_text("序列号 %s 卡券 %s" % (SERIAL2, COUPON2))
    rows = h.orders()
    check("S15c: 新券+新序列号另建一单", len(rows) == 2, rows)
    check("S15c: 核销用的是新券",
          len(h.verify_calls) == 1 and h.verify_calls[-1]["coupon"] == COUPON2, h.verify_calls)

    # 15d：重复提交同一张已核销成功的券 → 不新建单、状态与凭据保持不变
    h.reset("success")
    await h.send_text("序列号 %s 卡券 %s" % (SERIAL, COUPON))
    await h.send_text("序列号 %s 卡券 %s" % (SERIAL, COUPON))
    rows = h.orders()
    check("S15d: 重复提交同券不新建单", len(rows) == 1, rows)
    check("S15d: 成功状态与凭据保持不变",
          len(rows) == 1 and rows[0][3] == 1 and rows[0][2] == COUPON, rows)

    # 15e：第二张新券是图片（OCR 出同一序列号 + 新卡券）
    h.reset("success")
    await h.send_text("序列号 %s 卡券 %s" % (SERIAL, COUPON))
    h.verify_calls.clear()
    with _ClassPatch(h):
        ai = AIReplyHandler(bot=_FakeBot("序列号 %s 卡券 %s" % (SERIAL, COUPON2)))
        await ai._get_ai_reply_with_image(
            "http://img", make_context("", ContextType.IMAGE), dict(META)
        )
    rows = h.orders()
    check("S15e: 图片新券另建一单", len(rows) == 2, rows)
    check("S15e: 图片新券核销用的是新券",
          len(h.verify_calls) == 1 and h.verify_calls[-1]["coupon"] == COUPON2, h.verify_calls)

    h.assert_no_empty_verify("S15")


async def scenario_16(db):
    """已核销成功的券重复提交：不再重复调用核销接口，也不误报核销失败"""
    from Message.handlers.voucher_handler import VoucherDetectionHandler
    from Message.handlers.voucher_handler import _VOUCHER_SUCCESS
    from database.models import Order as _Order

    print("S16 已核销成功的券重复提交")
    handler = VoucherDetectionHandler()
    handler._record_message = lambda *a, **k: None
    calls = {"n": 0}

    def fake_query_and_verify(*a, **k):
        calls["n"] += 1
        return "核销成功！", None

    handler._query_and_verify = fake_query_and_verify

    with db.session_scope() as session:
        session.query(_Order).delete()
        order = _Order(shop_id=SHOP_ID, session_id=CUST_UID,
                       sn_imei=SERIAL, coupons=COUPON, status=1)
        session.add(order)
        session.flush()
        order_id = order.id

    reply, status = await handler._do_verify(SERIAL, COUPON, SHOP_ID, USER_ID, CUST_UID, order_id)
    check("S16: 已核销成功的券不再调用核销接口", calls["n"] == 0, calls)
    check("S16: 回复明确已核销成功（不是核销失败）",
          bool(reply) and "已核销成功" in reply and "失败" not in reply, reply)
    check("S16: 状态标记为成功", status == _VOUCHER_SUCCESS, status)


async def scenario_17(h: Harness):
    """旧单还没核销成功（待核销/失败）时，同一序列号改发新券——按纠正处理，但绝不能拿旧券核销"""
    print("S17 旧单未成功时同序列号改发新券")
    h.reset("fail_preview")                     # 第一张券验券失败 -> status=2
    await h.send_text("序列号 %s 卡券 %s" % (SERIAL, COUPON))
    rows = h.orders()
    check("S17a: 首单核销失败", len(rows) == 1 and rows[0][3] == 2, rows)

    h.verify_mode = "success"
    h.verify_calls.clear()
    await h.send_text("序列号 %s 卡券 %s" % (SERIAL, COUPON2))
    rows = h.orders()
    check("S17a: 纠正不新建单", len(rows) == 1, rows)
    check("S17a: 卡券号改为新券", bool(rows) and rows[0][2] == COUPON2, rows)
    check("S17a: 核销用新券（不是旧券）",
          len(h.verify_calls) == 1 and h.verify_calls[-1]["coupon"] == COUPON2, h.verify_calls)
    check("S17a: 核销成功", bool(rows) and rows[0][3] == 1, rows)

    # 17b: 第一张券还在待核销（只有券、缺序列号）→ 又来一张新券 → 仍按纠正，补序列号后用新券核销
    h.reset("success")
    await h.send_text(COUPON)
    await h.send_text(COUPON2)
    rows = h.orders()
    check("S17b: 仍 1 条单", len(rows) == 1, rows)
    check("S17b: 卡券号是最后一张", bool(rows) and rows[0][2] == COUPON2, rows)
    h.verify_calls.clear()
    await h.send_text(SERIAL)
    rows = h.orders()
    check("S17b: 用最后一张券核销",
          len(h.verify_calls) == 1 and h.verify_calls[-1]["coupon"] == COUPON2, h.verify_calls)
    check("S17b: 核销成功", bool(rows) and rows[0][3] == 1, rows)
    h.assert_no_empty_verify("S17")


async def scenario_18(db):
    """同一序列号 + 不同卡券，端到端回归（券只能用一次，模拟拼多多侧真实返回）

    线上 BUG 的完整复现路径：第一次券核销成功后，同一个序列号再发新券，
    旧逻辑把新券当误发忽略、又拿旧券去核销 -> 接口返回"该券已被核销" -> 客户看到核销失败。
    """
    from Message.handlers.voucher_handler import VoucherDetectionHandler

    print("S18 同一序列号 + 不同卡券（券只能用一次，端到端）")
    handler = VoucherDetectionHandler()
    handler._record_message = lambda *a, **k: None
    sent, used, calls = [], set(), []

    async def fake_send(shop_id, user_id, from_uid, text):
        sent.append(text)
        return True

    def fake_api(serial, voucher_code, shop_id, user_id, from_uid, order_id):
        calls.append({"sn": serial, "coupon": voucher_code})
        if voucher_code in used:          # 拼多多侧：券已被核销
            handler._update_order_status(order_id, 2)
            return f"❌ 卡券核销失败：该券已被核销\n卡券号：{voucher_code}", None
        used.add(voucher_code)
        handler._update_order_status(order_id, 1)
        return "核销成功！", None

    handler._send = fake_send
    handler._query_and_verify = fake_api

    async def send(text):
        sent.clear()
        await handler.handle(make_context(text), dict(META))

    with db.session_scope() as s:
        s.query(Order).delete()

    await send("序列号 %s 卡券 %s" % (SERIAL, COUPON))
    rows = _all_orders(db)
    check("S18: 第一张券核销成功",
          len(rows) == 1 and rows[0][2] == COUPON and rows[0][3] == 1, rows)
    check("S18: 第一次调用接口用第 1 张券",
          [c["coupon"] for c in calls] == [COUPON], calls)

    await send("序列号 %s 卡券 %s" % (SERIAL, COUPON2))
    rows = _all_orders(db)
    check("S18: 同一序列号换新券 -> 另建一单", len(rows) == 2, rows)
    check("S18: 第二单用新券且核销成功",
          len(rows) == 2 and rows[1][2] == COUPON2 and rows[1][3] == 1, rows)
    check("S18: 第二次调用接口用的是新券",
          [c["coupon"] for c in calls] == [COUPON, COUPON2], calls)
    check("S18: 客户收到核销成功（不是核销失败）",
          bool(sent) and "核销成功" in sent[-1] and "失败" not in sent[-1], sent[-1:])
    check("S18: 第一单凭据未被改写",
          rows[0][2] == COUPON and rows[0][3] == 1, rows)

    # 再重复提交同一张已成功的券 -> 不应再打接口，也不应报失败
    await send("序列号 %s 卡券 %s" % (SERIAL, COUPON2))
    rows = _all_orders(db)
    check("S18: 重复提交已成功的券不再调用接口", len(calls) == 2, calls)
    check("S18: 重复提交回复已核销成功",
          bool(sent) and "已核销成功" in sent[-1] and "失败" not in sent[-1], sent[-1:])
    check("S18: 订单未被改成核销失败", all(r[3] != 2 for r in rows), rows)


def _all_orders(db):
    with db.session_scope() as s:
        return [(o.id, o.sn_imei or "", o.coupons or "", o.status)
                for o in s.query(Order).order_by(Order.id).all()]


async def scenario_19(db):
    """线上 BUG 之二：客户分两次发 —— 先发新卡券，再发序列号

    第一张券核销成功后，客户发新券 B（先落库待补序列号），再发序列号 S；
    旧逻辑在"只带序列号"的消息里又命中了第一张已核销成功的单，
    于是返回旧券 A -> 回复"该卡券已核销成功，卡券号还是第一张"，新券永远核销不了。
    """
    from Message.handlers.voucher_handler import VoucherDetectionHandler

    print("S19 先发新券、再发序列号（分两次）")
    handler = VoucherDetectionHandler()
    handler._record_message = lambda *a, **k: None
    sent, used, calls = [], set(), []

    async def fake_send(shop_id, user_id, from_uid, text):
        sent.append(text)
        return True

    def fake_api(serial, voucher_code, shop_id, user_id, from_uid, order_id):
        calls.append({"sn": serial, "coupon": voucher_code})
        if voucher_code in used:
            handler._update_order_status(order_id, 2)
            return f"❌ 卡券核销失败：该券已被核销\n卡券号：{voucher_code}", None
        used.add(voucher_code)
        handler._update_order_status(order_id, 1)
        return "核销成功！", None

    handler._send = fake_send
    handler._query_and_verify = fake_api

    async def send(text):
        sent.clear()
        await handler.handle(make_context(text), dict(META))

    with db.session_scope() as s:
        s.query(Order).delete()

    await send("序列号 %s 卡券 %s" % (SERIAL, COUPON))
    rows = _all_orders(db)
    check("S19: 第一张券核销成功", len(rows) == 1 and rows[0][3] == 1, rows)

    await send(COUPON2)                       # 新券，分开发（不带序列号）
    rows = _all_orders(db)
    check("S19: 新券先落库、等待序列号",
          len(rows) == 2 and rows[1][2] == COUPON2 and rows[1][1] == "", rows)

    await send("序列号 %s" % SERIAL)           # 再补序列号 -> 必须凑齐新券那一单
    rows = _all_orders(db)
    check("S19: 新单凑齐序列号+新券",
          len(rows) == 2 and rows[1][1] == SERIAL and rows[1][2] == COUPON2, rows)
    check("S19: 新单核销成功", len(rows) == 2 and rows[1][3] == 1, rows)
    check("S19: 接口调用顺序 = [第一张券, 新券]",
          [c["coupon"] for c in calls] == [COUPON, COUPON2], calls)
    check("S19: 回复不再说'该卡券已核销成功'",
          bool(sent) and "无需重复核销" not in sent[-1] and "核销成功" in sent[-1], sent[-1:])
    check("S19: 第一单凭据不变", rows[0][2] == COUPON and rows[0][3] == 1, rows)

    # 再换第三张券 + 新序列号，同样要能核销
    await send(COUPON3)
    await send("序列号 %s" % SERIAL2)
    rows = _all_orders(db)
    check("S19b: 第三张券另建单并核销成功",
          len(rows) == 3 and rows[2][1] == SERIAL2 and rows[2][2] == COUPON3 and rows[2][3] == 1, rows)
    check("S19b: 接口调用顺序 = [券1, 券2, 券3]",
          [c["coupon"] for c in calls] == [COUPON, COUPON2, COUPON3], calls)

    # S19c：反过来 —— 客户先发序列号、再发新券，最后又发一次序列号，也必须核销新券
    with db.session_scope() as s:
        s.query(Order).delete()
    calls.clear()
    used.clear()
    await send("序列号 %s 卡券 %s" % (SERIAL, COUPON))       # 第一笔
    await send("序列号 %s" % SERIAL)                          # 只发序列号（此刻没有新券）
    await send(COUPON2)                                       # 再发新券
    rows = _all_orders(db)
    check("S19c: 新券另建一单待补序列号",
          len(rows) == 2 and rows[1][2] == COUPON2 and rows[1][1] == "", rows)
    await send("序列号 %s" % SERIAL)                          # 再补序列号
    rows = _all_orders(db)
    check("S19c: 新单凑齐并核销成功",
          len(rows) == 2 and rows[1][1] == SERIAL and rows[1][2] == COUPON2 and rows[1][3] == 1, rows)
    check("S19c: 接口调用顺序 = [券1, 券2]",
          [c["coupon"] for c in calls] == [COUPON, COUPON2], calls)


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
        await scenario_13(h)
        await scenario_15(h)
        await scenario_16(db)
        await scenario_17(h)
        await scenario_18(db)
        await scenario_19(db)
        scenario_11()
        scenario_14()
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
