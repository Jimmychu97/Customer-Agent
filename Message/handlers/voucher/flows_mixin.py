"""订单类消息的应答分支：只发订单号 / 订单卡 / 订单概览。"""
from .io_exec import _io
from .modes import _SERVICE_MODE_AUTO
from .modes import _SERVICE_MODE_SHIP
from .parsers import _extract_order_sn
from .parsers import _parse_json_content
from .texts import _SHIP_SERIAL_GUIDE
from bridge.context import Context
from typing import Any
from typing import Dict


class FlowsMixin:
    async def _handle_order_no_only(self, shop_id: str, user_id: str, from_uid: str,
                                    order_sn: str, session_id: str) -> bool:
        """客户只发订单号：核对本地历史单，已发货的能直接补出报告"""
        self._record_message(session_id, "user", order_sn)
        found = self._order_by_sn(shop_id, order_sn)

        # 本地没有这单：先去平台核对订单号（客户常把订单号复制半截，直接「已记录」会建错单）
        if not found:
            detail, cooling = None, False
            try:
                from core.order_lookup import get_order_detail, is_cooling_down

                detail = await _io(get_order_detail, shop_id, user_id, order_sn, retries=1)
                cooling = is_cooling_down(shop_id)
            except Exception as e:
                self.logger.debug(f"[发货] 订单号平台核对失败 order_sn={order_sn}: {e}")
            if detail is None and not cooling:
                self.logger.info(f"[发货] 订单号平台查不到，请客户核对 order_sn={order_sn}")
                guide = (
                    f"⚠️ 订单号 {order_sn} 在平台上没查到，可能是没复制全\n"
                    f"麻烦重新复制完整订单号发我（点击聊天框右下角「+」→ 选择「订单」→ 发送订单卡片），"
                    f"或直接把订单卡片转发给我～"
                )
                await self._send(shop_id, user_id, from_uid, guide)
                self._record_message(session_id, "assistant", guide)
                return True

        if found and found.get("status") == 1 and found.get("sn_imei"):
            self.logger.info(f"[发货] 订单已发货，按该单核对服务并补报告 order_sn={order_sn}")
            if await self._resend_or_query_report(
                    found["sn_imei"], found, shop_id, user_id, from_uid, order_sn):
                return True

        # 订单号绑到该客户「已有序列号」的那张单上（不限时间窗口）：
        # 客户先发序列号、隔一会儿再发订单号是常态，绑上就能直接发货，
        # 否则两边会互相要求对方重发（历史死循环就是这么来的）。
        bind_id, sn_of_row = self._bind_order_sn(shop_id, from_uid, order_sn)
        if bind_id is None:
            # 没有可绑定的单：先把订单号单独落库，序列号一到就能直接发货，
            # 否则客户再发序列号时又没有订单号，两边来回要
            _sn0, _cp0, bind_id, _up0 = await self._upsert_order(
                None, None, shop_id, from_uid, order_sn=order_sn)
            sn_of_row = _sn0 or ""
            if bind_id is not None:
                self.logger.info(f"[发货] 订单号先落库 order_id={bind_id} order_sn={order_sn}")
        row = self._order_row(bind_id)
        if sn_of_row and row.get("service_mode") in (_SERVICE_MODE_SHIP, _SERVICE_MODE_AUTO):
            self.logger.info(
                f"[发货] 订单号到达，该单已有序列号 {sn_of_row!r}，先请客户确认 order_sn={order_sn}")
            reply, _status = await self._ship_with_confirm(
                sn_of_row, order_sn, bind_id, shop_id, user_id, from_uid)
            if reply:
                await self._send(shop_id, user_id, from_uid, reply)
                self._record_message(session_id, "assistant", reply)
            return True

        if found:
            state = "已发货" if found.get("status") == 1 else "待发货"
            guide = (
                f"✅ 已核对到订单：{order_sn}\n本单状态：{state}\n"
                f"请把手机序列号/IMEI 发我，我马上查询并出报告。"
            )
        elif sn_of_row:
            # 序列号已经在手上，只是本单不是发货商品（核销类）：引导补卡券
            guide = (
                f"✅ 已记录订单号：{order_sn}\n"
                f"序列号/IMEI：{sn_of_row} 也已收到\n"
                f"如果是卡券核销商品，请把商品卡券号发我完成核销。"
            )
        else:
            guide = (
                f"✅ 已记录订单号：{order_sn}\n"
                f"请把手机序列号/IMEI 发我，我马上查询并出报告。"
            )
        await self._send(shop_id, user_id, from_uid, guide)
        self._record_message(session_id, "assistant", guide)
        return True

    async def _handle_order_info(self, context: Context, metadata: Dict[str, Any]) -> bool:
        """处理订单信息消息（订单卡）：把拼多多订单号落到 Order.order_sn 并引导补序列号/IMEI

        Returns:
            True 已应答（截断链）；False 解析失败或缺参数，放行给后续处理器
        """
        data = _parse_json_content(context.content) or {}
        order_sn = _extract_order_sn(str(data.get("order_id") or "")) or _extract_order_sn(str(context.content or ""))
        shop_id, user_id, from_uid = metadata.get("shop_id"), metadata.get("user_id"), metadata.get("from_uid")
        if not order_sn or not all([shop_id, user_id, from_uid]):
            self.logger.info(f"[发货] 订单信息无法处理，放行 order_sn={order_sn!r} from_uid={from_uid}")
            return False
        try:
            goods_name = str(data.get("goods_name") or "")
            goods_id = data.get("goods_id")
            spec = str(data.get("spec") or "")
            session_id = f"pinduoduo{from_uid}"
            self._record_message(session_id, "user", str(context.content or ""))
            # 订单卡里带 goods_id：顺手把本单的服务模式落库，
            # 之后客户只发序列号时就能按这张单的模式判定，不再依赖商品ID或订单接口
            order_mode = self._resolve_service_mode(shop_id, goods_id) if goods_id else None
            # 平台会重复推送同一张订单卡：已发货的单直接告知，
            # 不新建空单，也不再向客户索要已经发过的序列号
            shipped = self._order_by_sn(shop_id, order_sn)
            if shipped and shipped.get("status") == 1:
                self.logger.info(f"[发货] 订单卡重复推送，订单已发货 order_sn={order_sn}")
                # 已发货的历史单：不主动把报告刷一遍，只提示客户「要就开口」
                reply = await self._reply_for_shipped_order(
                    shipped.get("sn_imei") or "", shipped, shop_id, user_id, from_uid,
                    order_sn, goods_id, spec)
                if reply:
                    await self._send(shop_id, user_id, from_uid, reply)
                    self._record_message(session_id, "assistant", reply)
                return True

            # 客户先发序列号、后发订单卡是常见节奏：能补到已有单上就不新建单
            pending_id, _pending_sn = self._bind_order_sn(
                shop_id, from_uid, order_sn, service_mode=order_mode)
            if pending_id is None:
                pending_sn, _coupon, pending_id, _updates = await self._upsert_order(
                    None, None, shop_id, from_uid, order_sn=order_sn, service_mode=order_mode)
            else:
                pending_sn = self._sn_of_order(pending_id)

            # 该单已经有序列号、且本商品就是「仅发货/二选一兼容」→ 直接发货闭环，
            # 不再让客户把已经发过的序列号重发一遍。
            # 同一订单号重复发货由 _do_ship 内部拦截（_order_already_shipped / _already_shipped）。
            existing_sn = (pending_sn or "").strip()
            if existing_sn and order_mode in (_SERVICE_MODE_SHIP, _SERVICE_MODE_AUTO):
                self.logger.info(
                    f"[发货] 订单卡到达，该单已有序列号 {existing_sn!r} mode={order_mode}，"
                    f"先请客户确认 order_sn={order_sn} order_id={pending_id}")
                reply, _status = await self._ship_with_confirm(
                    existing_sn, order_sn, pending_id, shop_id, user_id, from_uid,
                    goods_id=goods_id, spec=spec, goods_name=goods_name)
                if reply:
                    await self._send(shop_id, user_id, from_uid, reply)
                    self._record_message(session_id, "assistant", reply)
                return True

            guide = (f"✅ 已记录您的订单号：{order_sn}\n"
                     + (f"商品：{goods_name}" if goods_name else "") + _SHIP_SERIAL_GUIDE)
            await self._send(shop_id, user_id, from_uid, guide)
            self._record_message(session_id, "assistant", guide)
            return True
        except Exception as e:
            self.logger.opt(exception=True).error(f"[发货] 处理订单信息失败: {e}")
            return False

    async def _reply_order_overview(self, shop_id: str, user_id: str, from_uid: str) -> bool:
        """客户问「我的订单/待发货」：去后台查他这笔单，告诉他是什么商品，再要序列号"""
        session_id = f"pinduoduo{from_uid}"
        resolved = await self._resolve_customer_ship_order(shop_id, user_id, from_uid)
        status = str(resolved.get("status") or "")
        self.logger.info(
            f"[发货] 客户询问订单，后台核对结果 status={status} "
            f"source={resolved.get('source')} from_uid={from_uid}")

        if status == "one":
            order = resolved.get("order") or {}
            text = (
                "📦 查到您有一笔待发货订单：\n"
                f"订单号：{order.get('order_sn') or ''}\n"
                + (f"商品：{order.get('goods_name')}\n" if order.get("goods_name") else "")
                + (f"查询项目：{order.get('spec')}\n" if order.get("spec") else "")
                + "\n请把手机序列号/IMEI 发我，我核对后请您确认，确认了就为您发货并出查询报告～"
            )
        elif status == "many":
            orders = resolved.get("orders") or []
            lines = "\n".join(self._order_line(i, o) for i, o in enumerate(orders[:5], 1))
            text = (
                f"📦 查到您名下有 {len(orders)} 笔待发货订单：\n{lines}\n\n"
                "麻烦回复要查的那一笔【订单号】并把手机序列号/IMEI 发我，"
                "我核对后请您确认，确认了就发货并出报告～"
            )
        elif status == "stale":
            text = (
                "您之前的订单已经发货完成啦～\n"
                "如果刚拍了下新的一单，麻烦把新订单卡片发我"
                "（点击聊天框右下角「+」→ 选择「订单」→ 发送订单卡片），"
                "再把手机序列号/IMEI 发我，我核对后请您确认就发货并出报告～\n"
                "📄 之前查过的报告要看 → 回复「重发」"
            )
        elif status == "unverified":
            # 店里有待发货单，但没法确认是不是这个客户的：只说流程，不报别人的单号
            text = (
                "📦 麻烦把您这单的订单卡片发我核对一下"
                "（点击聊天框右下角「+」→ 选择「订单」→ 发送订单卡片），"
                "同时把手机序列号/IMEI 发我，我核对到您这笔单后就为您发货并出报告～"
            )
        elif status == "unavailable":
            text = (
                "📦 后台订单暂时没查到（查询被限流），稍后我再帮您核～\n"
                "您也可以直接把【订单卡片】或【订单号】发我，我按订单核对后马上处理。"
            )
        else:
            text = (
                "📦 后台没查到您名下待发货的订单哦～\n"
                "麻烦把【订单卡片】或【订单号】发我（点击聊天框右下角「+」→ 选择「订单」→ 发送订单卡片），"
                "我按订单核对后马上帮您处理。"
            )

        await self._send(shop_id, user_id, from_uid, text)
        self._record_message(session_id, "assistant", text)
        return True
