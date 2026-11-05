"""无物流发货：客户确认 → 调平台发货 → 出验机报告。"""

from .modes import _SERVICE_MODE_SHIP
from .intents import _is_confirm_text
from .io_exec import _io
from .parsers import _extract_coupon
from .parsers import _extract_imei
from .parsers import _extract_order_sn
from .parsers import _extract_serial
from .state import _ship_inflight
from .state import _ship_inflight_guard
from .texts import _NO_REPORT_REORDER_TEXT
from .texts import _REPORT_ALREADY_SENT_TEXT
from .texts import _REPORT_EMPTY_TEXT
from .texts import _SHIP_ERROR
from .texts import _SHIP_FAILED
from .texts import _SHIP_IN_FLIGHT_TEXT
from .texts import _SHIP_NEED_ORDER_TEXT
from .texts import _SHIP_NOT_CONFIGURED_TEXT
from .texts import _SHIP_SERIAL_GUIDE
from .texts import _SHIP_SUCCESS
from .texts import _UPSELL_HINT
from typing import Any
from typing import Dict
from typing import Optional
from typing import Tuple
import asyncio


class ShipMixin:
    def _ship_via_platform(self, order_sn: str, shop_id: str, user_id: str) -> Dict[str, Any]:
        """调用拼多多「无物流发货」接口

        Args:
            order_sn: 拼多多订单号
            shop_id:  平台店铺ID（用于取账号 cookies）
            user_id:  账号ID（用于取账号 cookies）

        Returns:
            MmsOrderAPI.ship_without_logistics 的结果字典，
            形如 {"success": bool, "error_msg": str, "result_code": int|None, ...}
        """
        from Channel.pinduoduo.utils.API.order_api import MmsOrderAPI

        api = MmsOrderAPI(str(shop_id), str(user_id))
        return api.ship_without_logistics(order_sn)

    @staticmethod
    def _is_already_shipped_status(detail: Dict[str, Any]) -> bool:
        """平台已发货、待收货状态表示该订单服务已使用，不能再次发货。"""
        status = str((detail or {}).get("order_status_str") or "")
        return "待收货" in status or "已发货" in status

    async def _do_ship(self, sn: str, order_sn: str, shop_id: str, user_id: str, from_uid: str,
                       order_id: Optional[int], goods_id=None, spec: str = "",
                       goods_name: str = "") -> Tuple[Optional[str], str]:
        """发货入口：同一订单号同时只允许一次发货调用（防平台重复推送导致双发）

        平台会把同一条消息重复推送，两个消费者线程会在写库之前同时通过「已发货」检查，
        因此这里再加一道进程内在途占位；数据库里的 status 判定仍是最外层的防呆。
        """
        args = (shop_id, user_id, from_uid, order_id, goods_id, spec, goods_name)
        if not order_sn:
            return await self._do_ship_locked(sn, order_sn, *args)
        key = f"{shop_id}:{order_sn}"
        with _ship_inflight_guard:
            if key in _ship_inflight:
                self.logger.info(
                    f"[发货] 订单 {order_sn} 正在发货中，忽略本条并发的重复请求 sn={sn!r}")
                return _SHIP_IN_FLIGHT_TEXT, _SHIP_SUCCESS
            _ship_inflight.add(key)
        try:
            return await self._do_ship_locked(sn, order_sn, *args)
        finally:
            with _ship_inflight_guard:
                _ship_inflight.discard(key)

    async def _do_ship_locked(self, sn: str, order_sn: str, shop_id: str, user_id: str,
                              from_uid: str, order_id: Optional[int], goods_id=None,
                              spec: str = "", goods_name: str = "") -> Tuple[Optional[str], str]:
        """触发无物流发货流程（与 _do_verify 对称）

        参数不完整绝不调用平台接口；已发货成功的单直接短路；成功后复用核销路径的
        验机报告链（voucher_success_text 传发货成功文案）。
        """
        if not sn:
            # 客户只发了订单号：先看他之前用过的序列号（存在 orders 表里），有就让他确认，
            # 不再干巴巴地要一遍——线上「明明发了序列号又要序列号」多数是这条路径
            known = self._known_serial(shop_id, from_uid, order_sn) if order_sn else ""
            if known:
                self._attach_serial(order_id, known)
                self.logger.info(
                    f"[发货] 缺序列号，改用客户历史序列号 {known!r} 请他确认 "
                    f"order_sn={order_sn!r} order_id={order_id}")
                # 只确认序列号一次；订单身份已在本次流程中确定，确认后直接查询并发货。
                await self._ask_serial_confirm(
                    known, goods_id, spec or "", order_id, order_sn,
                    shop_id, user_id, from_uid)
                return None, _SHIP_SUCCESS
            self.logger.warning(
                f"[发货] 缺少序列号 sn={sn!r} order_sn={order_sn!r} order_id={order_id}，跳过发货调用"
            )
            return _SHIP_SERIAL_GUIDE, _SHIP_ERROR

        # 没有订单号就发不了货，也就不查询：按「先发货成功、再出报告」的顺序走
        if not order_sn:
            # 防呆：该单其实已经发过货了（客户把序列号又发了一遍），
            # 不要再让客户重复提供订单号；能补报告就补，补不出就引导重新下单查询
            if self._shipped_by_id(order_id):
                self.logger.info(
                    f"[发货] 该单已发货，忽略重复提交的序列号 sn={sn!r} order_id={order_id}")
                reply = await self._reply_for_shipped_order(
                    sn, self._order_by_id(order_id), shop_id, user_id, from_uid,
                    "", goods_id, spec or "")
                return reply, _SHIP_SUCCESS
            self.logger.info(f"[发货] 缺订单号，暂不查询 sn={sn} from_uid={from_uid}")
            return _SHIP_NEED_ORDER_TEXT, _SHIP_ERROR

        # 幂等：同一个订单号只要已在本店发过货，就绝不再打一次平台接口（重复发货不可回滚）
        if self._order_already_shipped(shop_id, order_sn):
            self.logger.info(f"[发货] 订单号已发货成功，改为核对历史单补报告 order_sn={order_sn} sn={sn}")
            reply = await self._reply_for_shipped_order(
                sn, self._order_by_sn(shop_id, order_sn), shop_id, user_id, from_uid,
                order_sn, goods_id, spec or "")
            return reply, _SHIP_SUCCESS

        if self._already_shipped(order_id, order_sn):
            self.logger.info(f"[发货] 已发货，跳过重复调用 order_id={order_id} order_sn={order_sn}")
            reply = await self._reply_for_shipped_order(
                sn, self._order_by_id(order_id), shop_id, user_id, from_uid,
                order_sn, goods_id, spec or "")
            return reply, _SHIP_SUCCESS

        # 查不到（接口失败/风控/订单不在最近 30 天）就拒绝发货，发货不可逆，不能把
        # 「平台稍后会拒绝」当作业务保护。待收货/已发货同样绝不进入发货接口。
        from core.order_lookup import get_order_detail

        detail = await asyncio.to_thread(get_order_detail, shop_id, user_id, order_sn, retries=2)
        if detail is None:
            self.logger.warning(
                f"[发货] 无法核对订单状态，拒绝发货 order_sn={order_sn}")
            return (
                f"⚠️ 订单 {order_sn} 暂时无法核对状态，本次未发货。\n"
                "请稍后再试或重新发送订单卡片，我核对到「待发货」后才会处理。"
            ), _SHIP_ERROR
        if not goods_id:
            goods_id = detail.get("goods_id")
        if not spec:
            spec = str(detail.get("spec") or "")
        goods_name = goods_name or str(detail.get("goods_name") or "")
        if not detail.get("pending_shipment"):
            state = (detail.get("order_status_str")
                     or f"shipping_status={detail.get('shipping_status')} "
                        f"pay_status={detail.get('pay_status')}")
            self.logger.warning(
                f"[发货] 订单不是待发货状态，拒绝发货 order_sn={order_sn} 状态={state}")
            if self._is_already_shipped_status(detail):
                return (
                    f"✅ 该订单已发货完成，服务已使用。\n"
                    f"订单号：{order_sn}\n当前状态：{state}\n\n"
                    "如需继续查询，请重新下单。"
                ), _SHIP_SUCCESS
            return (
                f"❌ 该订单当前不是「待发货」状态，本次未发货。\n"
                f"订单号：{order_sn}\n当前状态：{state}"
            ), _SHIP_FAILED

        # 验机前置：序列号在验机库查不到就绝不发货。
        # 发货不可回滚，而报告是发货后必须给的——查不到就没有补救余地，只能先拦住。
        query_cfg, spec, spec_count, verify_goods = self._ship_query_config(goods_id, spec or "")
        server_id = query_cfg["server_id"]
        zhenxin_sid = query_cfg["zhenxin_server_id"]
        device_type = query_cfg["device_type"]
        self.logger.info(
            f"[发货] 订单 {order_sn} 商品核对 goods_id={goods_id} spec={spec!r} "
            f"-> device_type={device_type} server_id={server_id} zhenxin={zhenxin_sid} "
            f"specs={spec_count}"
        )
        report_query_enabled = bool(server_id or zhenxin_sid)
        query_key = self._query_type_key(goods_id, spec, query_cfg)

        # 验机商品、但这次连规格都没拿到（客户只发了订单号/订单详情被风控）：发货前就拦住。
        # 先发货再要规格的后果是报告永远出不来——线上 17:28 就是这么丢的报告。
        # 注意：规格有值只是没配编码（商家漏配）不拦，照常发货并在报告链上明确告知。
        if spec_count > 1 and not spec and not report_query_enabled:
            self.logger.warning(
                f"[发货] 多规格商品未拿到规格，发货前拦下 order_sn={order_sn} "
                f"goods_id={goods_id} spec={spec!r} specs={spec_count} order_id={order_id}")
            return self._ship_spec_needed_text(order_sn, spec_count), _SHIP_ERROR

        report_deduped = self._report_recently_sent(shop_id, from_uid, sn, query_key)
        serial_needs_confirm = self._serial_needs_confirm(shop_id, from_uid, sn, query_key)

        def _reject_text(err) -> str:
            return (
                f"❌ 序列号/IMEI 有误，尚未发货\n"
                f"序列号/IMEI：{sn}\n订单号：{order_sn}\n"
                f"验机库查不到这个序列号：{err or '未知错误'}\n\n"
                f"请核对后把正确的序列号/IMEI 发我，我马上为您发货并出报告～"
            )

        busy_text = (
            f"⚠️ 验机服务暂时不可用，本次尚未发货\n"
            f"序列号/IMEI：{sn}\n订单号：{order_sn}\n\n"
            f"序列号先给您留着，稍后再发我一次这个序列号/IMEI，我马上为您发货并出报告～"
        )

        report_data = None
        if report_query_enabled and not report_deduped and not serial_needs_confirm:
            await self._notify_query_pending(shop_id, user_id, from_uid, sn, order_sn)
            try:
                if device_type == "apple" and zhenxin_sid:
                    from Channel.pinduoduo.utils.API.gsxunlocking_api import query_gsxunlocking
                    zx = await _io(query_gsxunlocking, sn, zhenxin_sid)
                    if not zx.get("success"):
                        self._clear_order_sn(order_id)
                        self.logger.warning(
                            f"[发货] 验机查不到序列号，拒绝发货 sn={sn} order_sn={order_sn} "
                            f"zhenxin={zhenxin_sid}: {zx.get('error_msg')}")
                        return _reject_text(zx.get("error_msg")), _SHIP_FAILED
                    report_data = zx["result"]
                elif server_id:
                    from Channel.pinduoduo.utils.API.gsx_api import query_gsx
                    gsx = await _io(query_gsx, sn, server_id)
                    if not gsx.get("success"):
                        self._clear_order_sn(order_id)
                        self.logger.warning(
                            f"[发货] 验机查不到序列号，拒绝发货 sn={sn} order_sn={order_sn} "
                            f"server_id={server_id}: {gsx.get('error_msg')}")
                        return _reject_text(gsx.get("error_msg")), _SHIP_FAILED
                    report_data = gsx["data"]
            except Exception as e:
                # 接口超时/异常时无法判定序列号真伪：不发货，但必须给客户回话，不能静默丢弃
                self.logger.opt(exception=True).error(
                    f"[发货] 验机查询异常，本次不发货 sn={sn} order_sn={order_sn}: {e}")
                return busy_text, _SHIP_ERROR

        try:
            result = await asyncio.to_thread(self._ship_via_platform, order_sn, shop_id, user_id)
        except NotImplementedError:
            self.logger.error(f"[发货] 发货接口尚未实现，引导人工处理 order_sn={order_sn}")
            return _SHIP_NOT_CONFIGURED_TEXT, _SHIP_ERROR
        except Exception as e:
            self.logger.opt(exception=True).error(f"[发货] 发货调用异常 order_sn={order_sn}: {e}")
            return f"❌ 发货失败：{e}\n订单号：{order_sn}", _SHIP_FAILED
        if not (isinstance(result, dict) and result.get("success")):
            err = result.get("error_msg") if isinstance(result, dict) else str(result)
            self.logger.warning(f"[发货] 发货失败 order_sn={order_sn} err={err}")
            self._update_order_status(order_id, 2)
            return f"❌ 发货失败：{err or '未知错误'}\n订单号：{order_sn}", _SHIP_FAILED
        # 成功：状态/订单号/本单实际服务模式一起写库，再走验机报告链
        self._update_order_status(order_id, 1, order_sn)
        self.logger.info(
            f"[发货] 发货成功 sn={sn} order_sn={order_sn} order_id={order_id} "
            f"result_code={result.get('result_code')!r}"
        )
        success_text = (
            f"发货成功！\n序列号/IMEI：{sn}\n订单号：{order_sn}\n"
            + (f"商品：{goods_name}\n" if goods_name else "")
            + "我们已为您完成发货，无需寄回机器，感谢您的耐心等待。"
        )

        # 规格没对上就不查：宁可不查，也不用别的套餐顶替（客户买什么就查什么）
        if not report_query_enabled:
            note, _matched = self._pending_ship_note(shop_id, user_id)
            self.logger.warning(
                f"[发货] 未匹配到验机服务，跳过查询，改要订单核对 "
                f"order_sn={order_sn} goods_id={goods_id} spec={spec!r} "
                f"specs={spec_count} 验机商品={verify_goods}"
            )
            return (
                success_text + "\n\n"
                + note
                + "\n这单的查询规格还没核对上，麻烦把【订单卡片】再发我一次"
                  "（点击聊天框右下角「+」→ 选择「订单」→ 发送订单卡片），我按订单核对后马上补报告～"
            ), _SHIP_SUCCESS

        # 刚发过这份报告就别再查一遍（重复推消息不重复烧接口、不重复刷屏）
        if report_deduped:
            self.logger.info(
                f"[发货] 报告刚发过，跳过重复查询 sn={sn} order_sn={order_sn} "
                f"query_key={query_key!r}")
            return success_text + "\n\n" + _REPORT_ALREADY_SENT_TEXT, _SHIP_SUCCESS

        # 货已发、序列号/IMEI 还没经客户确认：先让客户核对，确认后再查（避免 OCR 认错就查）
        if serial_needs_confirm:
            await self._ask_serial_confirm(
                sn, goods_id, spec or "", order_id, order_sn,
                shop_id, user_id, from_uid, prefix=success_text)
            return None, _SHIP_SUCCESS

        # 苹果设备走 GsxUnlocking 报告链，其它走 GSX 报告链
        sender = self._query_gsxunlocking_and_send if device_type == "apple" else self._query_gsx_and_send
        await sender(sn, str(goods_id or ""), spec or "", report_data, order_id,
                     shop_id, user_id, from_uid, success_text,
                     on_empty_note=_REPORT_EMPTY_TEXT)
        return None, _SHIP_SUCCESS

    def _product_spec_count(self, goods_id: str) -> int:
        """该商品在 goods_commit 里配了几个规格（0 = 不是验机商品，纯发货）"""
        if not goods_id:
            return 0
        try:
            from database import db_manager
            from database.models import GoodsCommit

            with db_manager.session_scope() as session:
                rows = session.query(GoodsCommit.spec_name).filter(
                    GoodsCommit.goods_id == str(goods_id),
                    GoodsCommit.spec_name.isnot(None),
                ).all()
            return len({s for (s,) in rows if s})
        except Exception as e:
            self.logger.debug(f"[发货] 读取商品规格失败 goods_id={goods_id}: {e}")
            return 0

    def _is_verify_goods(self, goods_id) -> bool:
        """这个商品是不是「验机服务」商品

        判据两条任一成立：
          1. goods_commit 里有规格行（正常同步过 SKU）；
          2. 商品知识库里有这个商品（服务模式 0/2 卖的都是验机服务）。
        线上 976314624020 属于第 2 种：SKU 没同步、规格行缺失，但商品确实卖验机服务；
        只看第 1 条会把它当「纯发货商品」放行，于是又出现「发货成功却没报告」。
        """
        goods = str(goods_id or "")
        if not goods:
            return False
        if self._product_spec_count(goods) > 0:
            return True
        try:
            from database import db_manager
            from database.models import ProductKnowledge

            with db_manager.session_scope() as session:
                row = session.query(ProductKnowledge.id).filter(
                    ProductKnowledge.goods_id == int(goods)).first()
            return row is not None
        except Exception:
            return False

    def _ship_query_config(self, goods_id, spec: str):
        """解析发货要用的验机口径：返回 (cfg, spec, spec_count, is_verify_goods)

        spec 缺失时**只**用「商品唯一规格」兜底，多规格绝不猜：客户买的是哪个套餐，
        报告字段就得对得上。判定为验机商品却解析不到编码时，调用方必须先别发货：
          - spec_count > 0 且解析不到编码 → 客户还没说清买的是哪个套餐
          - 是验机商品（含只在商品知识库里的）→ 必须先核对查询项目再发
          - 不是验机商品（纯发货）        → 照常发货

        线上 17:28 / 21:56 两单都是「先发货、报告永远补不出来」，根因就在这里放行太早。
        """
        goods = str(goods_id or "")
        empty = {"server_id": None, "zhenxin_server_id": None, "device_type": None}
        resolved = spec or ""
        cfg = self._resolve_query_config(goods, resolved) if goods else dict(empty)
        spec_count = self._product_spec_count(goods)
        has_code = bool(cfg.get("server_id") or cfg.get("zhenxin_server_id"))
        if goods and not has_code and spec_count == 1:
            try:
                from Message.core.conversation_product import _backfill_single_spec

                single = _backfill_single_spec(goods)
            except Exception:
                single = None
            if single:
                resolved = single
                cfg = self._resolve_query_config(goods, resolved)
                has_code = bool(cfg.get("server_id") or cfg.get("zhenxin_server_id"))
                self.logger.info(
                    f"[发货] 规格缺失，按商品唯一规格兜底 goods_id={goods} "
                    f"spec={resolved!r} has_code={has_code}")
        verify_goods = bool(spec_count) or self._is_verify_goods(goods)
        return cfg, resolved, spec_count, verify_goods

    async def _ship_with_confirm(self, sn: str, order_sn: str, order_id: Optional[int],
                                 shop_id: str, user_id: str, from_uid: str,
                                 goods_id=None, spec: str = "", goods_name: str = "",
                                 newest: bool = False, assumed: bool = False):
        """需要客户确认才发货：未确认→回确认信息（不发货）；已确认→真的发货并出报告

        确认信息里要带「这笔订单买的是什么查询项目」（全面验机 / 网络锁 / 特惠激活锁…），
        这个项目决定查哪个 SERVERID；客户确认的就是「这台机器的序列号 + 这个查询项目」。
        """
        if order_sn and (not spec or not goods_name or not goods_id):
            try:
                from core.order_lookup import get_order_detail

                detail = await _io(get_order_detail, shop_id, user_id, order_sn, retries=1)
            except Exception as e:
                self.logger.debug(f"[发货] 按订单补查询项目失败 order_sn={order_sn}: {e}")
                detail = None
            if detail:
                goods_id = goods_id or detail.get("goods_id")
                spec = spec or str(detail.get("spec") or "")
                goods_name = goods_name or str(detail.get("goods_name") or "")

        # 验机商品但没拿到规格：连"确认"都别发——客户确认了也发不了货（报告对不上），
        # 先要订单卡片/套餐名，把查询项目核对清楚再走确认 → 发货 → 出报告。
        cfg, resolved_spec, spec_count, verify_goods = self._ship_query_config(goods_id, spec or "")
        missing_spec = not (spec or "").strip() and not resolved_spec
        # 只有「多规格但客户没说买哪个」才拦：这种情况问一句就能解决。
        # 规格表里一条都没有（SKU 没同步）时拦下来也没人能回答，照常发货 + 明确告知没报告。
        if (spec_count > 1 and missing_spec
                and not (cfg.get("server_id") or cfg.get("zhenxin_server_id"))):
            self.logger.warning(
                f"[发货] 验机商品没拿到规格，先不发货 order_sn={order_sn!r} "
                f"goods_id={goods_id} spec={spec!r} specs={spec_count} from_uid={from_uid}")
            return self._ship_spec_needed_text(order_sn, spec_count), _SHIP_ERROR
        if resolved_spec and not spec:
            spec = resolved_spec

        # 序列号只确认一次：订单到达后直接按订单套餐查询并继续发货，
        # 不再追加第二张「序列号+订单」确认卡片。
        query_key = self._query_type_key(goods_id, spec or "", cfg)
        if self._serial_needs_confirm(shop_id, from_uid, sn, query_key):
            await self._ask_serial_confirm(
                sn, goods_id, spec, order_id, order_sn,
                shop_id, user_id, from_uid)
            return None, _SHIP_SUCCESS
        self._mark_serial_confirmed(shop_id, from_uid, sn, query_key)
        return await self._do_ship(sn, order_sn, shop_id, user_id, from_uid, order_id,
                                   goods_id=goods_id, spec=spec, goods_name=goods_name)

    async def _ask_ship_confirm(self, sn: str, order_sn: str, order_id: Optional[int],
                                goods_id, spec: str, goods_name: str,
                                shop_id: str, user_id: str, from_uid: str,
                                newest: bool = False, assumed: bool = False) -> None:
        """发货前让客户确认「序列号 + 这笔订单的查询项目」，确认后才调发货接口"""
        self._set_confirm_pending(
            shop_id, from_uid, kind="ship", sn=sn, order_sn=order_sn or "",
            order_id=order_id, goods_id=goods_id, spec=spec or "")
        text = self._ship_confirm_text(sn, order_sn, goods_name or "", spec or "",
                                       newest, assumed)
        await self._send(shop_id, user_id, from_uid, text)
        self._record_message(f"pinduoduo{from_uid}", "assistant", text)
        cfg = self._get_query_config(str(goods_id or ""), spec or "")
        self.logger.info(
            f"[发货] 发货前待客户确认 sn={sn} order_sn={order_sn!r} spec={spec!r} "
            f"-> device_type={cfg.get('device_type')} server_id={cfg.get('server_id')} "
            f"zhenxin={cfg.get('zhenxin_server_id')} order_id={order_id} from_uid={from_uid}")

    async def _ask_serial_confirm(self, sn: str, goods_id, spec: str, order_id: Optional[int],
                                  order_sn: str, shop_id: str, user_id: str, from_uid: str,
                                  prefix: str = "") -> None:
        """记下「待确认」并让客户核对序列号；确认后才去查验机"""
        # 查询项目身份要落到确认记录里，避免激活锁确认结果错误复用于全面验机。
        query_key = self._query_type_key(goods_id, spec)
        self._set_confirm_pending(
            shop_id, from_uid, kind="query", sn=sn, goods_id=goods_id, spec=spec or "",
            query_key=query_key, order_id=order_id, order_sn=order_sn or "")
        text = self._serial_confirm_text(sn, prefix)
        await self._send(shop_id, user_id, from_uid, text)
        self._record_message(f"pinduoduo{from_uid}", "assistant", text)
        self.logger.info(
            f"[核销] 序列号待客户确认后才查 sn={sn} order_sn={order_sn!r} "
            f"order_id={order_id} from_uid={from_uid}")

    async def _ask_serial_only_confirm(self, sn: str, order_id: Optional[int],
                                       shop_id: str, user_id: str, from_uid: str) -> None:
        """仅收到序列号时先让客户确认；未拿到订单前不猜查询项目、不查询。"""
        self._set_confirm_pending(
            shop_id, from_uid, kind="serial", sn=sn, order_id=order_id,
            order_sn="", goods_id=None, spec="")
        text = (
            f"✅ 已记录序列号/IMEI：{sn}\n"
            "请确认这个序列号/IMEI 是否正确。确认无误请回复「确认」，"
            "然后发送订单卡片；我会按订单对应的查询项目为您查询。"
        )
        await self._send(shop_id, user_id, from_uid, text)
        self._record_message(f"pinduoduo{from_uid}", "assistant", text)
        self.logger.info(f"[订单] 已收到序列号，等待客户确认后再收订单 sn={sn}")

    async def _resume_confirmed_serial(self, info: Dict[str, Any], shop_id: str,
                                       user_id: str, from_uid: str) -> bool:
        sn = str(info.get("sn") or "")
        order_sn = str(info.get("order_sn") or "")
        goods_id = info.get("goods_id")
        spec = str(info.get("spec") or "")
        order_id = info.get("order_id")
        session_id = f"pinduoduo{from_uid}"
        query_key = self._query_type_key(goods_id, spec)
        self._mark_serial_confirmed(shop_id, from_uid, sn, query_key)
        self.logger.info(
            f"[核销] 客户已确认序列号，开始查验机 sn={sn} order_sn={order_sn!r} "
            f"order_id={order_id} spec={spec!r}")
        if info.get("kind") == "serial":
            # 只确认了设备标识，尚无订单就不能判定激活锁/全面验机套餐，更不能发货。
            self._mark_serial_confirmed(shop_id, from_uid, sn)
            reply = (
                f"✅ 已确认序列号/IMEI：{sn}\n"
                "请发送这笔订单的【订单卡片】（聊天框右下角「+」→ 订单 → 发送订单卡片），"
                "我会按订单对应的查询项目马上为您查询。"
            )
            await self._send(shop_id, user_id, from_uid, reply)
            self._record_message(session_id, "assistant", reply)
            return True
        if info.get("kind") == "ship":
            self._mark_ship_confirmed(shop_id, from_uid, sn, order_sn)
            self.logger.info(
                f"[发货] 客户已确认「序列号+订单」，开始发货并查询 sn={sn} order_sn={order_sn!r}")
            reply, _status = await self._do_ship(
                sn, order_sn, shop_id, user_id, from_uid, order_id,
                goods_id=goods_id, spec=spec)
            if reply:
                await self._send(shop_id, user_id, from_uid, reply)
                self._record_message(session_id, "assistant", reply)
            return True
        if order_sn:
            # 已确认序列号后按本订单查询，随后直接进入发货前状态校验，不再弹第二次确认卡片。
            found = self._order_by_sn(shop_id, order_sn) or {"order_sn": order_sn,
                                                              "order_id": order_id}
            handled = await self._resend_or_query_report(
                sn, found, shop_id, user_id, from_uid, order_sn, goods_id, spec)
            if handled:
                reply, _status = await self._do_ship(
                    sn, order_sn, shop_id, user_id, from_uid, order_id,
                    goods_id=goods_id, spec=spec)
            else:
                reply = (f"✅ 已确认序列号/IMEI：{sn}\n"
                         "这单的验机服务口径没核对上，麻烦把【订单卡片】发我"
                         "（点击聊天框右下角「+」→ 选择「订单」→ 发送订单卡片），"
                         "我按订单帮您查～")
        else:
            reply, _status = await self._query_report_only(
                sn, goods_id, spec, shop_id, user_id, from_uid, order_id)
            if reply is None and not self._report_recently_sent(shop_id, from_uid, sn, query_key):
                # 「确认」之后的续接不允许一句话都没有：客户已经确认了，
                # 这里静默就等于让他干等（线上 16:28 客户连问三次没人回）。
                self.logger.error(
                    f"[报告缺失] 客户确认后没有发出任何消息，补发兜底话术 "
                    f"sn={sn} order_id={order_id} goods_id={goods_id} spec={spec!r}")
                reply = _REPORT_EMPTY_TEXT
        if reply:
            await self._send(shop_id, user_id, from_uid, reply)
            self._record_message(session_id, "assistant", reply)
        return True

    async def _handle_sn_confirm(self, content: str, shop_id: str, user_id: str,
                                 from_uid: str) -> Optional[bool]:
        """客户回「确认」后，用记下的序列号/订单去发货或查验机

        以前是「先 pop 再判意图」：客户回一句「确认了」「好的稍等」都会把待确认
        静默作废，之后再回「确认」就查无 pending —— 表现出来就是
        「客户确认了却不查、也不回」。现在先判意图：
          - 确认        → pop 并真正去查询/发货
          - 换了新序列号/卡券/订单号 → pop（客户换单了），交给后续流程
          - 其它短句    → 保留 pending，并回一句「我还在等您确认这一条」
        """
        info = self._peek_confirm_pending(shop_id, from_uid)
        if not info:
            return None
        if _is_confirm_text(content):
            self._pop_confirm_pending(shop_id, from_uid)
            return await self._resume_confirmed_serial(info, shop_id, user_id, from_uid)
        if (_extract_serial(content) or _extract_imei(content)
                or _extract_coupon(content) or _extract_order_sn(content)):
            self._pop_confirm_pending(shop_id, from_uid)
            self.logger.info(
                f"[核销] 客户发了新的序列号/卡券/订单号，作废待确认 from_uid={from_uid} "
                f"content={str(content or '')[:20]!r}")
            return None
        self.logger.info(
            f"[确认丢失] 没确认也没换单，保留待确认并提示 from_uid={from_uid} "
            f"content={str(content or '')[:20]!r}")
        note = self._serial_confirm_reminder_text(
            str(info.get("sn") or ""), str(info.get("order_sn") or ""))
        await self._send(shop_id, user_id, from_uid, note)
        self._record_message(f"pinduoduo{from_uid}", "assistant", note)
        return True

    async def _resend_last_report(self, shop_id: str, user_id: str, from_uid: str) -> bool:
        """客户开口要报告（重发/查报告）：按最近一张已发货单补发或补查"""
        shipped = self._latest_shipped_order(shop_id, from_uid)
        if not shipped:
            self.logger.info(f"[发货] 客户要报告，但没有历史已发货单 from_uid={from_uid}")
            return False
        sn = str(shipped.get("sn_imei") or "")
        self._clear_report_sent(shop_id, from_uid, sn)
        # 客户都开口要了，就不再问一次「确认序列号」
        self._mark_serial_confirmed(shop_id, from_uid, sn)
        goods_id, spec = None, ""
        if not shipped.get("sku_id") and shipped.get("order_sn"):
            # 历史单没记验机口径：按订单号补一次规格，才查得对
            try:
                from core.order_lookup import get_order_detail

                detail = await _io(get_order_detail, shop_id, user_id,
                                  str(shipped["order_sn"]), retries=1)
                if detail:
                    goods_id = detail.get("goods_id")
                    spec = str(detail.get("spec") or "")
            except Exception as e:
                self.logger.debug(f"[发货] 补规格失败 order_sn={shipped.get('order_sn')}: {e}")
        self.logger.info(
            f"[发货] 客户要报告，开始补发/补查 sn={sn} order_sn={shipped.get('order_sn')} spec={spec!r}")
        if await self._resend_or_query_report(
                sn, shipped, shop_id, user_id, from_uid, shipped.get("order_sn") or "",
                goods_id, spec):
            return True
        await self._send(
            shop_id, user_id, from_uid,
            "这单的验机服务口径没找到，麻烦把【订单卡片】发我"
            "（点击聊天框右下角「+」→ 选择「订单」→ 发送订单卡片），我按订单帮您查～")
        return True

    def _can_query_report(self, goods_id=None, spec: str = "",
                          found: Optional[Dict[str, Any]] = None) -> bool:
        """这单还能不能补出报告：有历史报告、有 SKU 口径，或能按商品+规格解析到验机服务

        用来决定对客话术：**查不了就绝不说「马上帮您查」**，否则客户会一直等。
        """
        found = found or {}
        if found.get("gsx_result") or found.get("sku_id"):
            return True
        if goods_id in (None, "", 0, "0"):
            return False

        effective_spec = (spec or "").strip()
        if not effective_spec:
            # 商品只有一个规格时查询项目是确定的，可以直接补查；
            # 多规格又不知道规格，才是真的查不了。
            try:
                from Message.core.conversation_product import _backfill_single_spec

                effective_spec = _backfill_single_spec(str(goods_id)) or ""
            except Exception:
                effective_spec = ""
        if not effective_spec:
            return False

        try:
            cfg = self._resolve_query_config(goods_id, effective_spec)
        except Exception:
            return False
        return bool(cfg.get("server_id") or cfg.get("zhenxin_server_id"))

    async def _reply_for_shipped_order(self, sn: str, found: Optional[Dict[str, Any]], shop_id: str,
                                       user_id: str, from_uid: str, order_sn: str = "",
                                       goods_id=None, spec: str = "") -> Optional[str]:
        """本单已发货（历史单）：不主动发历史报告，等客户开口再发

        客户再发一次序列号时，先确认「有没有新单」；命中的是已发货的历史单，
        就只回一句提示，把「要不要报告」的选择留给客户，避免把旧报告刷屏。

        三种口径（关键：查不了就明说，不许承诺马上补）：
          - 有历史报告        → 回复「重发」
          - 没报告但能查      → 回复「查报告」
          - 没报告也查不了    → 说明这单没出报告，需重新下单或转人工
        """
        found = found or {}
        has_report = bool(found.get("gsx_result"))
        can_query = self._can_query_report(goods_id, spec, found)
        self.logger.info(
            f"[发货] 命中已发货历史单，不主动发报告 sn={sn!r} order_sn={order_sn!r} "
            f"has_report={has_report} can_query={can_query}")
        head = (f"✅ 订单 {order_sn} 之前已经发货完成啦\n" if order_sn
                else "✅ 这单之前已经发货完成啦\n")
        line = f"序列号/IMEI：{sn}\n" if sn else ""
        if has_report:
            tail = "需要这份验机报告的话回复「重发」，我马上发给您～"
        elif can_query:
            tail = "需要这份验机报告的话回复「查报告」，我马上帮您查～"
        else:
            tail = _NO_REPORT_REORDER_TEXT
        return head + line + "\n" + tail + _UPSELL_HINT

    async def _resend_or_query_report(self, sn: str, found: Dict[str, Any], shop_id: str,
                                      user_id: str, from_uid: str, order_sn: str = "",
                                      goods_id=None, spec: str = "") -> bool:
        """按历史订单核对该单用的验机服务：有历史报告直接重发，没有就用当时的服务重新查询

        sku_id 为空（上次核销/发货时规格没核对上）时，用本次的 goods_id + 规格现场解析，
        所以客户把序列号再发一次就能自愈补报告，不用人工介入。
        """
        order_id = found.get("order_id")
        tail = f"✅ 已核对到订单：{order_sn or found.get('order_sn') or ''}\n这是您本单的验机报告～"

        query_key = str(found.get("query_key") or self._query_type_key(goods_id, spec))
        # 同一查询项目内才去重；激活锁和全面验机不能共用序列号去重记录。
        if self._report_recently_sent(shop_id, from_uid, sn, query_key):
            self.logger.info(
                f"[发货] 报告刚发过，忽略重复请求 sn={sn} order_sn={order_sn}")
            await self._send(shop_id, user_id, from_uid, _REPORT_ALREADY_SENT_TEXT)
            return True

        if found.get("gsx_result"):
            self.logger.info(f"[发货] 历史报告已存在，直接重发 sn={sn} order_sn={order_sn}")
            await self._query_gsxunlocking_and_send(
                sn, "", "", found["gsx_result"], order_id, shop_id, user_id, from_uid, tail)
            return True

        zhenxin_sid = found.get("sku_id") or ""
        server_id = ""
        if not zhenxin_sid:
            cfg = self._resolve_query_config(goods_id, spec or "")
            zhenxin_sid = str(cfg.get("zhenxin_server_id") or "")
            server_id = str(cfg.get("server_id") or "")
        if not (zhenxin_sid or server_id):
            self.logger.info(
                f"[发货] 历史单缺少验机服务口径，无法补发报告 order_sn={order_sn} "
                f"goods_id={goods_id} spec={spec!r}")
            # 查不了就明说，并给出可执行的下一步（重新下单 / 转人工），
            # 不再让客户等一句「马上整理报告」。
            await self._send(shop_id, user_id, from_uid, _NO_REPORT_REORDER_TEXT)
            return True

        # 序列号/IMEI 必须客户确认后才查验机
        if self._serial_needs_confirm(shop_id, from_uid, sn, query_key):
            await self._ask_serial_confirm(
                sn, goods_id, spec or "", found.get("order_id"), order_sn,
                shop_id, user_id, from_uid)
            return True

        if zhenxin_sid:
            await self._notify_query_pending(shop_id, user_id, from_uid, sn, order_sn)
            from Channel.pinduoduo.utils.API.gsxunlocking_api import query_gsxunlocking
            self.logger.info(
                f"[发货] 用历史订单的验机服务重新查询 sn={sn} zhenxin={zhenxin_sid} order_sn={order_sn}"
            )
            result = query_gsxunlocking(sn, zhenxin_sid)
            data = result.get("result")
            sender = self._query_gsxunlocking_and_send
        else:
            await self._notify_query_pending(shop_id, user_id, from_uid, sn, order_sn)
            from Channel.pinduoduo.utils.API.gsx_api import query_gsx
            self.logger.info(
                f"[发货] 用历史订单的验机服务重新查询 sn={sn} server_id={server_id} order_sn={order_sn}"
            )
            result = query_gsx(sn, server_id)
            data = result.get("data")
            sender = self._query_gsx_and_send
        if not result["success"]:
            await self._send(
                shop_id, user_id, from_uid,
                f"❌ 验机查询失败：{result.get('error_msg', '未知错误')}\n请核对序列号后再发我。",
            )
            return True
        await sender(sn, "", "", data, order_id, shop_id, user_id, from_uid, tail,
                     on_empty_note=_REPORT_EMPTY_TEXT)
        self._mark_report_sent(shop_id, from_uid, sn, query_key)
        return True

    async def _handle_serial_then_ship(
        self, sn: str, order_sn, goods_id, spec: str, goods_name: str,
        shop_id: str, user_id: str, from_uid: str, session_id: str,
        msg_id: str = "",
    ) -> bool:
        """订单与序列号齐全后先按订单套餐查询；待发货状态仅在真正发货前校验。"""
        from .handler import _mark_handled, _recently_handled

        dup_key = f"serial:{shop_id}:{from_uid}:{msg_id}:{sn}:{order_sn or ''}"
        if _recently_handled(dup_key, ttl=120):
            self.logger.info(
                f"[发货] 同一条序列号/订单在 120s 内已处理过，静默跳过 sn={sn} order_sn={order_sn!r}")
            return True
        _mark_handled(dup_key)
        self._record_message(session_id, "user", f"序列号 {sn}")

        # 序列号先到时只保存并让客户确认。订单尚未到达时，不能猜测查询套餐。
        if not order_sn:
            order_id = self._save_standalone_serial(sn, shop_id, from_uid)
            if self._serial_needs_confirm(shop_id, from_uid, sn):
                await self._ask_serial_only_confirm(sn, order_id, shop_id, user_id, from_uid)
            else:
                text = (
                    f"✅ 已确认序列号/IMEI：{sn}\n"
                    "请发送订单卡片，我会按订单对应的查询项目为您查询。"
                )
                await self._send(shop_id, user_id, from_uid, text)
                self._record_message(session_id, "assistant", text)
            return True

        detail = None
        stored_order_id = None
        if order_sn:
            # 订单先落库。即使订单接口暂时 40002，也保留已收到的订单号和序列号，
            # 后续无需再向客户索要序列号。
            _stored_sn, _coupon, stored_order_id, updates = await self._upsert_order(
                sn, None, shop_id, from_uid, order_sn=str(order_sn),
                service_mode=_SERVICE_MODE_SHIP)
            await self._send_updates(updates, shop_id, user_id, from_uid, session_id)
            sn = _stored_sn or sn
            try:
                from core.order_lookup import get_order_detail
                detail = await asyncio.to_thread(
                    get_order_detail, shop_id, user_id, str(order_sn), retries=1)
            except Exception as e:
                self.logger.warning(f"[订单查询] 读取订单详情失败 order_sn={order_sn}: {e}")
            if detail:
                # 有平台详情时它优先于会话商品，防止上一件商品的套餐串到本订单。
                goods_id = detail.get("goods_id") or goods_id
                spec = str(detail.get("spec") or spec or "")
                goods_name = str(detail.get("goods_name") or goods_name or "")
            elif not (goods_id and spec):
                text = (
                    f"✅ 已记录订单号：{order_sn}\n"
                    f"✅ 已记录序列号/IMEI：{sn}\n"
                    "订单的查询项目暂时核对不到，本次先不查询；请重新发送订单卡片，"
                    "无需重复发送序列号。"
                )
                await self._send(shop_id, user_id, from_uid, text)
                self._record_message(session_id, "assistant", text)
                return True
        elif not goods_id:
            try:
                from core.service_mode import infer_goods_id_from_session
                goods_id = infer_goods_id_from_session(shop_id, from_uid)
            except Exception as e:
                self.logger.debug(f"[发货] 会话商品推断失败: {e}")

        if not spec and goods_id:
            try:
                from Message.core.conversation_product import _backfill_single_spec
                spec = _backfill_single_spec(str(goods_id)) or ""
            except Exception as e:
                self.logger.debug(f"[发货] 补全唯一规格失败 goods_id={goods_id}: {e}")

        query_cfg = self._resolve_query_config(goods_id, spec)
        query_key = self._query_type_key(goods_id, spec, query_cfg)
        if not (query_cfg.get("server_id") or query_cfg.get("zhenxin_server_id")):
            text = (
                f"⚠️ 已收到序列号/IMEI：{sn}\n"
                + (f"订单号：{order_sn}\n" if order_sn else "")
                + "当前没有核对到这笔订单对应的查询项目，本次未查询、未发货。"
                "请重新发送订单卡片或联系人工核对。"
            )
            await self._send(shop_id, user_id, from_uid, text)
            self._record_message(session_id, "assistant", text)
            return True

        # 订单行已在收到订单时创建；拿到套餐后仅补项目身份，绝不因同一序列号覆盖别的套餐。
        if stored_order_id is not None:
            self._set_order_query_key(stored_order_id, query_key)
            order_id = stored_order_id
        else:
            final_sn, _coupon, order_id, updates = await self._upsert_order(
                sn, None, shop_id, from_uid, service_mode=_SERVICE_MODE_SHIP,
                query_key=query_key)
            await self._send_updates(updates, shop_id, user_id, from_uid, session_id)
            sn = final_sn or sn

        # 先确认序列号，才允许根据订单查询。序列号之前已单独确认过时，默认确认记录
        # 会放行这里；若客户直接把序列号和订单一起发来，则在此询问一次后再查询。
        if self._serial_needs_confirm(shop_id, from_uid, sn):
            await self._ask_serial_confirm(
                sn, goods_id, spec, order_id, str(order_sn), shop_id, user_id, from_uid)
            return True
        self._mark_serial_confirmed(shop_id, from_uid, sn, query_key)
        reply, query_status = await self._query_report_only(
            sn, goods_id, spec, shop_id, user_id, from_uid, order_id)
        # 报告发送器会写去重记录；这里再写一次保证替换后的发送器也不会让后续发货重复查询。
        if query_status == _SHIP_SUCCESS:
            self._mark_report_sent(shop_id, from_uid, sn, query_key)
        if reply:
            await self._send(shop_id, user_id, from_uid, reply)
            self._record_message(session_id, "assistant", reply)

        if not order_sn:
            guide = (
                "📦 如需发货，请发送这笔订单的【订单卡片】"
                "（聊天框右下角「+」→ 订单 → 发送订单卡片）。"
            )
            await self._send(shop_id, user_id, from_uid, guide)
            self._record_message(session_id, "assistant", guide)
            return True

        # 用户已确认序列号后，订单到达即查询并自动发货；_do_ship_locked 会在发货前
        # 实时校验订单是否仍为待发货，避免第二次向客户发送确认卡片。
        self.logger.info(
            f"[发货] 按订单实际查询项目处理并自动发货 order_sn={order_sn} goods_id={goods_id} "
            f"spec={spec!r} query_key={query_key!r} sn={sn}")
        reply, _status = await self._do_ship(
            sn, str(order_sn), shop_id, user_id, from_uid, order_id,
            goods_id=goods_id, spec=spec, goods_name=goods_name)
        if reply:
            await self._send(shop_id, user_id, from_uid, reply)
            self._record_message(session_id, "assistant", reply)
        return True

    async def _handle_ship_branch(
        self,
        sn,
        coupon_no,
        order_sn,
        goods_id,
        spec,
        goods_name,
        shop_id: str,
        user_id: str,
        from_uid: str,
        session_id: str,
        mode,
        claimed=None,
    ) -> bool:
        """仅发货 / 二选一兼容（本条无卡券）：认领订单 → 客户确认 → 平台发货 → 出报告。"""
        # 发货商品：客户给卡券也一律忽略，只要给序列号就走查询/发货
        if coupon_no:
            self.logger.info(
                f"[发货] 本单为发货商品，忽略客户提供的卡券 {coupon_no!r}，"
                f"只按序列号处理 sn={sn!r} mode={mode}")
        # 客户只给序列号：按「对话用户ID + 平台订单状态」自己认领订单——
        # 唯一一笔就直接用它（不用客户再发订单号）；多笔就让他指定；没有才要订单卡片。
        final_sn = (sn or "")
        order_id = None
        newest_flag = bool((claimed or {}).get("newest"))
        assumed_flag = bool((claimed or {}).get("assumed"))

        if final_sn and not order_sn:
            resolved = await self._resolve_customer_ship_order(shop_id, user_id, from_uid, final_sn)
            status = str(resolved.get("status") or "")
            newest_flag = newest_flag or bool(resolved.get("newest"))
            assumed_flag = assumed_flag or bool(resolved.get("assumed"))
            if status == "many":
                order_id = self._save_standalone_serial(final_sn, shop_id, from_uid)
                orders = resolved.get("orders") or []
                lines = "\n".join(
                    self._order_line(i, o) for i, o in enumerate(orders[:5], 1))
                more = ("" if len(orders) <= 5
                        else f"\n（还有 {len(orders) - 5} 笔，可发订单卡片指定）")
                text = (
                    f"✅ 已收到序列号/IMEI：{final_sn}\n"
                    f"您名下有 {len(orders)} 笔待发货订单：\n{lines}{more}\n\n"
                    f"麻烦把要查的那一笔的【订单卡片】发我（点击聊天框右下角「+」→ 选择「订单」→ 发送订单卡片），"
                    f"我按订单发货并出报告～"
                )
                await self._send(shop_id, user_id, from_uid, text)
                self._record_message(session_id, "assistant", text)
                self.logger.info(
                    f"[发货] 客户名下 {len(orders)} 笔待发货单，请客户指定 "
                    f"order_sns={[o.get('order_sn') for o in orders[:5]]} "
                    f"source={resolved.get('source')} order_id={order_id}")
                return True
            if status == "unverified":
                # 店里有待发货单，但本会话对不上是哪一笔：绝不把别人的单号发出去
                order_id = self._save_standalone_serial(final_sn, shop_id, from_uid)
                text = (
                    f"✅ 已收到序列号/IMEI：{final_sn}\n"
                    f"请顺便核对一下这个序列号/IMEI 是否正确（不对就把正确的发我）\n\n"
                    f"麻烦把您这单的订单卡片发我（点击聊天框右下角「+」→ 选择「订单」→ 发送订单卡片），"
                    f"我核对到您这笔单后就为您发货并出报告～"
                )
                await self._send(shop_id, user_id, from_uid, text)
                self._record_message(session_id, "assistant", text)
                self.logger.info(
                    f"[发货] 待发货单对不上客户，请客户发订单卡片 sn={final_sn} "
                    f"order_id={order_id}")
                return True
            if status in ("none", "unavailable", "stale"):
                # 没有待发货的新单：如果这客户之前有已发货单+报告，就提示「要报告回复重发」，
                # 不刷历史报告；有旧的报告需求就在结尾提示。
                shipped_hist = self._latest_shipped_order(shop_id, from_uid)
                if shipped_hist and (shipped_hist.get("gsx_result")
                                     or shipped_hist.get("sku_id")):
                    order_id = self._save_standalone_serial(final_sn, shop_id, from_uid)
                    reply = await self._reply_for_shipped_order(
                        final_sn, shipped_hist, shop_id, user_id, from_uid,
                        str(shipped_hist.get("order_sn") or ""))
                    if reply:
                        await self._send(shop_id, user_id, from_uid, reply)
                        self._record_message(session_id, "assistant", reply)
                        self.logger.info(
                            f"[发货] 没有新待发货单，命中历史已发货单 "
                            f"order_sn={shipped_hist.get('order_sn')} sn={final_sn} "
                            f"status={status} order_id={order_id}")
                        return True
                note, _matched = self._pending_ship_note(shop_id, user_id)
                text = (
                    f"✅ 已收到序列号/IMEI：{final_sn}\n"
                    f"请顺便核对一下这个序列号/IMEI 是否正确（不对就把正确的发我）\n\n"
                    + note
                    + "\n麻烦把订单卡片发我（点击聊天框右下角「+」→ 选择「订单」→ 发送订单卡片），"
                      "我按订单核对查询项目后，再请您确认一次就发货并出报告～"
                )
                await self._send(shop_id, user_id, from_uid, text)
                self._record_message(session_id, "assistant", text)
                self.logger.info(
                    f"[发货] 没认领到待发货单（{status}），请客户发订单卡片 sn={final_sn}")
                return True

            # status == "one"：就是他刚下的那一笔，不需要客户再发订单号
            claimed = resolved.get("order") or {}
            order_sn = str(claimed.get("order_sn") or "") or None
            goods_id = goods_id or claimed.get("goods_id")
            spec = spec or str(claimed.get("spec") or "")
            goods_name = goods_name or str(claimed.get("goods_name") or "")
            order_id = self._save_standalone_serial(
                final_sn, shop_id, from_uid, order_id=claimed.get("order_id"))
            bind_id, _sn = self._bind_order_sn(
                shop_id, from_uid, order_sn, sn=final_sn,
                service_mode=_SERVICE_MODE_SHIP)
            order_id = bind_id or order_id or claimed.get("order_id")
            self.logger.info(
                f"[发货] 自动认领客户待发货单 order_sn={order_sn!r} sn={final_sn} "
                f"goods_id={goods_id} spec={spec!r} source={resolved.get('source')} "
                f"order_id={order_id}")

        pending_id, pending_order_sn = self._pend_ship_order(shop_id, from_uid, sn, order_sn)
        final_order_sn = order_sn or pending_order_sn or ""
        if sn:
            if pending_id is not None:
                order_id = order_id or pending_id
            else:
                final_sn, _coupon, order_id, updates = await self._upsert_order(
                    sn, None, shop_id, from_uid, order_sn=final_order_sn or None,
                    service_mode=_SERVICE_MODE_SHIP)
                await self._send_updates(updates, shop_id, user_id, from_uid, session_id)

        if final_sn and final_order_sn:
            # 序列号 + 订单号齐了：先请客户确认「序列号+订单」，确认后才发货并出报告
            reply, _status = await self._ship_with_confirm(
                final_sn, final_order_sn, order_id, shop_id, user_id, from_uid,
                goods_id=goods_id, spec=spec, goods_name=goods_name,
                newest=newest_flag, assumed=assumed_flag)
        elif final_sn:
            if self._shipped_by_id(order_id):
                # 防呆：本单已经发过货，客户又把序列号发了一遍；
                # 能补报告就补，补不出来（货已发完）就引导重新下单查询
                self.logger.info(
                    f"[发货] 该单已发货，忽略重复提交的序列号 sn={final_sn!r} "
                    f"order_id={order_id}")
                reply = await self._reply_for_shipped_order(
                    final_sn, self._order_by_id(order_id), shop_id, user_id, from_uid,
                    final_order_sn, goods_id, spec or "")
            elif mode == _SERVICE_MODE_SHIP or mode is None:
                # 仅发货（或模式还没确定）：没有订单就没法发货，先引导补订单，
                # 不擅自出报告（模式未确定时更不能自作主张去查）
                reply = (
                    f"✅ 已收到序列号/IMEI：{final_sn}\n"
                    f"请顺便核对一下这个序列号/IMEI 是否正确（不对就把正确的发我）\n\n"
                    f"还没核对到您的订单，麻烦把【订单卡片】发我"
                    f"（点击聊天框右下角「+」→ 选择「订单」→ 发送订单卡片），"
                    f"我按订单核对查询项目后，再请您确认一次就发货并出报告～"
                )
            else:
                # 二选一兼容：客户可能只想先查，报告不依赖订单号
                reply, _status = await self._query_report_only(
                    final_sn, goods_id, spec, shop_id, user_id, from_uid, order_id)
        else:
            # 只有订单号（或只给了卡券）：订单号先按订单核对，别直接说「已记录」
            if final_order_sn:
                return await self._handle_order_no_only(
                    shop_id, user_id, from_uid, final_order_sn, session_id)
            reply = ("本商品不需要卡券，只要序列号/IMEI 就能查～" if coupon_no else "")
            reply += _SHIP_SERIAL_GUIDE
        if reply:
            await self._send(shop_id, user_id, from_uid, reply)
            self._record_message(session_id, "assistant", reply)
        return True
