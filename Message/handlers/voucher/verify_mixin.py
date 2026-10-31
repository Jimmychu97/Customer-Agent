"""卡券核销：调验券接口并生成回复。"""
from .texts import _VOUCHER_ERROR
from .texts import _VOUCHER_FAILED
from .texts import _VOUCHER_SUCCESS
from typing import Optional
from typing import Tuple
import asyncio


class VerifyMixin:
    def _already_verified(self, order_id: Optional[int], coupon_no: str) -> bool:
        """该单是否"已核销成功，且卡券号与本次一致"

        客户把同一张已核销成功的券再发一次时，不要重复调用验券接口
        （接口会返回"已核销"错误，容易被误报成"核销失败"）。
        """
        if not order_id or not coupon_no:
            return False
        try:
            from database import db_manager
            from database.models import Order
            with db_manager.session_scope() as session:
                order = session.query(Order).filter(Order.id == order_id).first()
                return bool(order and order.status == 1 and order.coupons == coupon_no)
        except Exception as e:
            self.logger.debug(f"[核销] 查询已核销状态失败 order_id={order_id}: {e}")
            return False

    async def _do_verify(
        self,
        sn: str,
        coupon_no: str,
        shop_id: str,
        user_id: str,
        from_uid: str,
        order_id: Optional[int],
    ) -> Tuple[Optional[str], str]:
        """触发核销流程，核销成功后串行 await GSX查询+LLM分析"""
        # 双保险：参数不完整时绝不调用核销接口
        if not sn or not coupon_no:
            self.logger.warning(
                f"[核销] 参数不完整 sn={sn!r} coupon={coupon_no!r} order_id={order_id}，跳过核销调用"
            )
            return "", _VOUCHER_ERROR
        # 该券已经核销成功 → 直接告知，不再打接口（避免"已核销"错误被误报成核销失败）
        if self._already_verified(order_id, coupon_no):
            self.logger.info(
                f"[核销] 卡券已核销成功，跳过重复核销调用 order_id={order_id} coupon={coupon_no}"
            )
            return f"✅ 该卡券已核销成功，无需重复核销\n卡券号：{coupon_no}", _VOUCHER_SUCCESS
        self.logger.info(f"[核销] 双条件齐备，触发核销 order_id={order_id}")
        result_text, gsx_params = await asyncio.to_thread(
            self._query_and_verify,
            sn, coupon_no, shop_id, user_id, from_uid, order_id
        )
        status = _VOUCHER_SUCCESS if result_text and "核销成功" in result_text else _VOUCHER_FAILED
        if status == _VOUCHER_SUCCESS and gsx_params:
            goods_id, spec, report_data, device_type = gsx_params
            if device_type == "apple":
                await self._query_gsxunlocking_and_send(sn, goods_id, spec, report_data, order_id, shop_id, user_id, from_uid, result_text)
            else:
                await self._query_gsx_and_send(sn, goods_id, spec, report_data, order_id, shop_id, user_id, from_uid, result_text)
            return None, status  # 核销成功时消息已在 _query_gsx_and_send 里全部发送
        return result_text, status

    def _query_and_verify(
        self,
        serial: str,
        voucher_code: str,
        shop_id: str,
        user_id: str,
        from_uid: str,
        order_id: int,
    ) -> Tuple[str, Optional[Tuple[str, str]]]:
        """MMS 验券 + GSX查询 + 核销，返回 (result_text, gsx_params)
        顺序：preview → GSX查询（失败则提示重新提供序列号，不核销）→ verification → 更新DB
        """
        from Channel.pinduoduo.utils.API.mms_voucher_api import MmsVoucherAPI
        api = MmsVoucherAPI(shop_id=shop_id, user_id=user_id)

        preview = api.preview(voucher_code)
        if not preview["success"]:
            self._update_order_status(order_id, 2)
            # 归类为"卡券问题"：保留已填的序列号，让客户只核对卡券号
            self.logger.warning(
                f"[核销] 验券失败（卡券问题）card_no={voucher_code} err={preview.get('error_msg')}"
            )
            return (
                f"❌ 卡券核销失败：{preview.get('error_msg', '未知错误')}\n"
                f"卡券号：{voucher_code}\n"
                f"该卡券号可能无效或已被使用，请核对卡券号后重新发送（序列号已保留，无需重发）"
            ), None

        goods_name = preview.get("goods_name", "")
        spec = preview.get("spec", "")
        order_sn = preview.get("order_sn", "")
        order_amount = preview.get("order_amount", 0)
        store_id = preview.get("store_id", 0)
        goods_id = str(preview.get("goods_id", ""))

        self.logger.info(
            f"[核销] 验券成功 card_no={voucher_code} order_sn={order_sn} goods={goods_name}"
        )

        # 验机查询：核销前先验证序列号，失败则清空 sn_imei 让用户重新提供
        query_cfg = self._get_query_config(goods_id, spec)
        server_id   = query_cfg["server_id"]
        zhenxin_sid = query_cfg["zhenxin_server_id"]
        device_type = query_cfg["device_type"]
        report_data = None

        if device_type == "apple" and zhenxin_sid:
            from Channel.pinduoduo.utils.API.gsxunlocking_api import query_gsxunlocking
            zx_result = query_gsxunlocking(serial, zhenxin_sid)
            if not zx_result["success"]:
                self.logger.warning(
                    f"[核销] GsxUnlocking查询失败，序列号可能有误 sn={serial}: {zx_result.get('error_msg')}"
                )
                self._clear_order_sn(order_id)
                # 归类为"序列号问题"：只清序列号，卡券号保留
                return (
                    f"❌ 序列号/IMEI 有误：{zx_result.get('error_msg', '未知错误')}\n"
                    f"请重新提供正确的序列号或 IMEI 码（卡券号已保留，无需重发）"
                ), None
            report_data = zx_result["result"]  # dict
        elif server_id:
            from Channel.pinduoduo.utils.API.gsx_api import query_gsx
            gsx_result = query_gsx(serial, server_id)
            if not gsx_result["success"]:
                self.logger.warning(
                    f"[核销] GSX查询失败，序列号可能有误 sn={serial} key={server_id}: {gsx_result.get('error_msg')}"
                )
                self._clear_order_sn(order_id)
                # 归类为"序列号问题"：只清序列号，卡券号保留
                return (
                    f"❌ 序列号/IMEI 有误：{gsx_result.get('error_msg', '未知错误')}\n"
                    f"请重新提供正确的序列号或 IMEI 码（卡券号已保留，无需重发）"
                ), None
            report_data = gsx_result["data"]  # dict
        else:
            self.logger.warning(f"[核销] 未找到查询配置，跳过验机验证 goods_id={goods_id} spec={spec!r}")

        result = api.verification(voucher_code, store_id=store_id or 0, order_sn=order_sn or "")
        if result["success"]:
            self._update_order_status(order_id, 1)
            self.logger.info(f"[核销] 核销成功 serial={serial} card_no={voucher_code} order_id={order_id}")
            gsx_params = (goods_id, spec, report_data, device_type)
            return (
                f"核销成功！\n"
                f"序列号/IMEI：{serial}\n"
                f"卡券号：{voucher_code}\n"
                f"商品：{goods_name}\n"
                + (f"规格：{spec}\n" if spec else "")
                + f"订单号：{order_sn}\n"
                f"订单金额：{order_amount / 100:.2f}元"
            ), gsx_params
        else:
            self.logger.warning(f"[核销] 核销失败 card_no={voucher_code} order_id={order_id} err={result.get('error_msg')}")
            self._update_order_status(order_id, 2)
            # 归类为"核销环节问题"：券信息正确，核销接口本身返回失败
            return (
                f"❌ 核销失败：{result.get('error_msg', '未知错误')}\n"
                f"卡券号：{voucher_code}\n"
                f"序列号/IMEI：{serial}"
            ), None

    async def _handle_voucher_branch(
        self,
        sn,
        coupon_no,
        shop_id: str,
        user_id: str,
        from_uid: str,
        session_id: str,
        mode=None,
    ) -> bool:
        """卡券核销：缺哪项就引导补齐，两项齐了才调验券接口。

        mode: 已判定的服务模式（确定是核销商品时为 0），写回本单供后续消息与 AI 复用。
        """
        # 只有卡券，没有序列号/IMEI → 存单后检查是否已凑齐
        if coupon_no and not sn:
            final_sn, final_coupon, order_id, updates = await self._upsert_order(sn, coupon_no, shop_id, from_uid)
            self._set_order_service_mode(order_id, mode)
            await self._send_updates(updates, shop_id, user_id, from_uid, session_id)
            if final_sn and final_coupon:
                notify = f"✅ 已记录您的卡券号：{coupon_no}，正在核销..."
                await self._send(shop_id, user_id, from_uid, notify)
                self._record_message(session_id, "assistant", notify)
                reply, _ = await self._do_verify(final_sn, final_coupon, shop_id, user_id, from_uid, order_id)
                if reply:
                    await self._send(shop_id, user_id, from_uid, reply)
                    self._record_message(session_id, "assistant", reply)
            else:
                guide = (
                    f"✅ 已记录您的卡券号：{coupon_no}\n"
                    f"如有误请直接发文字或截图纠正\n"
                    f"\n"
                    f"请继续提供以下任意一项完成核销：\n"
                    f"\n"
                    f"📱 IMEI码（推荐）\n"
                    f"拨号界面输入 *#06# 即可显示\n"
                    f"\n"
                    f"🔢 序列号\n"
                    f"设置 → 通用 → 关于本机 → 序列号"
                )
                await self._send(shop_id, user_id, from_uid, guide)
                self._record_message(session_id, "assistant", guide)
            return True

        # 只有序列号/IMEI，没有卡券 → 存单后检查是否已凑齐
        if sn and not coupon_no:
            final_sn, final_coupon, order_id, updates = await self._upsert_order(sn, coupon_no, shop_id, from_uid)
            self._set_order_service_mode(order_id, mode)
            await self._send_updates(updates, shop_id, user_id, from_uid, session_id)
            if final_sn and final_coupon:
                notify = f"✅ 已记录您的序列号/IMEI：{sn}，正在核销..."
                await self._send(shop_id, user_id, from_uid, notify)
                self._record_message(session_id, "assistant", notify)
                reply, _ = await self._do_verify(final_sn, final_coupon, shop_id, user_id, from_uid, order_id)
                if reply:
                    await self._send(shop_id, user_id, from_uid, reply)
                    self._record_message(session_id, "assistant", reply)
            else:
                guide = (
                    f"✅ 已记录您的序列号/IMEI：{sn}\n"
                    f"如有误请直接发文字或截图纠正\n"
                    f"\n"
                    f"请提供卡券号完成核销：\n"
                    f"待收货 → 点击商品 → 查看卡券 → 复制卡券发给客服"
                )
                await self._send(shop_id, user_id, from_uid, guide)
                self._record_message(session_id, "assistant", guide)
            return True

        # 同一条消息里两个都有 → 直接核销
        # 用 _upsert_order 返回的 final 值，避免消息里误提取的 coupon 覆盖已存的正确值
        final_sn, final_coupon, order_id, updates = await self._upsert_order(sn, coupon_no, shop_id, from_uid)
        self._set_order_service_mode(order_id, mode)
        await self._send_updates(updates, shop_id, user_id, from_uid, session_id)

        # 兜底：只要有一项为空就绝不调用核销接口（防止空序列号/空卡券把券核销掉）
        if not (final_sn and final_coupon):
            if not final_coupon:
                guide = (
                    f"✅ 已记录您的序列号/IMEI：{final_sn}\n"
                    f"如有误请直接发文字或截图纠正\n"
                    f"\n"
                    f"请提供卡券号完成核销：\n"
                    f"待收货 → 点击商品 → 查看卡券 → 复制卡券发给客服"
                )
            else:
                guide = (
                    f"✅ 已记录您的卡券号：{final_coupon}\n"
                    f"如有误请直接发文字或截图纠正\n"
                    f"\n"
                    f"请继续提供以下任意一项完成核销：\n"
                    f"\n"
                    f"📱 IMEI码（推荐）\n"
                    f"拨号界面输入 *#06# 即可显示\n"
                    f"\n"
                    f"🔢 序列号\n"
                    f"设置 → 通用 → 关于本机 → 序列号"
                )
            await self._send(shop_id, user_id, from_uid, guide)
            self._record_message(session_id, "assistant", guide)
            return True

        reply, _ = await self._do_verify(final_sn, final_coupon, shop_id, user_id, from_uid, order_id)
        if reply:
            await self._send(shop_id, user_id, from_uid, reply)
            self._record_message(session_id, "assistant", reply)
        return True
