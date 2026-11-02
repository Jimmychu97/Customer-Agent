"""
核销/发货处理器：消息分派（can_handle / handle）+ 各职责 mixin 组合。
"""
from .state_mixin import StateMixin
from .texts_mixin import TextsMixin
from .notify_mixin import NotifyMixin
from .orders_mixin import OrdersMixin
from .platform_mixin import PlatformMixin
from .flows_mixin import FlowsMixin
from .service_mode_mixin import ServiceModeMixin
from .verify_mixin import VerifyMixin
from .ship_mixin import ShipMixin
from .report_mixin import ReportMixin
from ..base import BaseHandler
from .intents import _is_confirm_text
from .intents import _is_order_ask_text
from .intents import _is_resend_text
from .modes import _SERVICE_MODE_AUTO
from .modes import _SERVICE_MODE_SHIP
from .modes import _SERVICE_MODE_VOUCHER
from .parsers import _extract_coupon
from .parsers import _extract_coupon_strict
from .parsers import _extract_imei
from .parsers import _extract_order_sn
from .parsers import _extract_serial
from .parsers import _parse_json_content
from .texts import _SHIP_SERIAL_GUIDE
from bridge.context import Context
from bridge.context import ContextType
from typing import Any
from typing import Dict
from typing import Optional


class VoucherDetectionHandler(StateMixin, TextsMixin, NotifyMixin, OrdersMixin, PlatformMixin, FlowsMixin, ServiceModeMixin, VerifyMixin, ShipMixin, ReportMixin, BaseHandler):
    def __init__(self):
        super().__init__("VoucherDetectionHandler")

    def can_handle(self, context: Context) -> bool:
        """是否由本处理器接管

        文本消息按卡券/序列号规则匹配；订单信息消息（ORDER_INFO）只把
        「卡券核销」商品的订单卡交给 AI —— 仅发货 / 二选一 / 未配置 都由本处理器接管，
        至少要把订单号记下来，客户随后发序列号时才认得到是哪一单。
        """
        if context.type == ContextType.ORDER_INFO:
            data = _parse_json_content(context.content) or {}
            mode = self._resolve_service_mode_optional(
                getattr(context.kwargs, "shop_id", None), data.get("goods_id"))
            if mode == _SERVICE_MODE_VOUCHER:
                return False
            return bool(_extract_order_sn(str(data.get("order_id") or "")))
        if context.type != ContextType.TEXT:
            return False
        content = context.content or ""
        # 订单号也要接管：客户只发/纠正订单号时若放给 AI，AI 只会说"没查到这笔订单"，
        # 订单号永远落不到单上（客户就以为订单丢了）
        if (_extract_serial(content) or _extract_imei(content) or _extract_coupon(content)
                or _extract_order_sn(content)):
            return True
        # 客户回「确认」：只有确实有一条「序列号等确认」时才接管，
        # 否则会把客户别的「好的/确认」抢走（截断链，AI 就接不到了）
        kwargs = getattr(context, "kwargs", None)
        shop_id = getattr(kwargs, "shop_id", None) if kwargs else None
        from_uid = getattr(kwargs, "from_uid", None) if kwargs else None
        if shop_id and from_uid:
            if _is_confirm_text(content):
                return bool(self._peek_confirm_pending(shop_id, from_uid))
            # 客户开口要报告（重发/查报告）：只有确实有历史已发货单时才接管
            if _is_resend_text(content):
                shipped = self._latest_shipped_order(shop_id, from_uid)
                return bool(shipped and (shipped.get("gsx_result") or shipped.get("sku_id")))
            # 客户问「我的订单/待发货」：必须由本处理器去后台查，不能让 AI 瞎编
            if _is_order_ask_text(content):
                return True
        return False

    async def handle(self, context: Context, metadata: Dict[str, Any]) -> bool:
        # 订单信息消息（订单卡）走独立分支：直发商品靠它拿到拼多多订单号
        if context.type == ContextType.ORDER_INFO:
            return await self._handle_order_info(context, metadata)

        content = context.content or ""
        from_uid = metadata.get("from_uid")
        shop_id = metadata.get("shop_id")
        user_id = metadata.get("user_id")
        pending: Optional[str] = None

        # 图片来源时直接使用 ai_handler 已提取好的值，不重新提取
        # 避免 IMEI2 等被误截为卡券号
        if "_image_sn" in metadata or "_image_coupon" in metadata:
            sn = metadata.get("_image_sn") or None
            coupon_no = metadata.get("_image_coupon") or None
            self.logger.info(f"[核销] 图片来源，使用预提取值 sn={sn!r} coupon={coupon_no!r}")
            # 截图同样要看「待核销单还缺哪一项」：客户先发卡券、再发截图时，
            # 图片里的序列号要补到那张待核销单上，不能当成发货序列号
            if shop_id and from_uid:
                pending = self._get_pending_order_state(shop_id, from_uid)
        else:
            sn = _extract_imei(content) or _extract_serial(content)
            # 注意：这里不再根据待核销单的缺失项把用户真实发来的值置空。
            # 重复提交时由 _upsert_order 按卡券号/序列号复用同一张单，避免重复建单，
            # 也避免"两值皆空"时用空参数去核销。
            pending = self._get_pending_order_state(shop_id, from_uid)
            if pending == "need_coupon":
                # 待补卡券：用严格提取，避免把序列号/订单号误判为卡券号
                coupon_no = _extract_coupon_strict(content, exclude_imei=sn)
            else:
                coupon_no = _extract_coupon(content, exclude_imei=sn)

        if not all([shop_id, user_id, from_uid]):
            self.logger.warning(
                f"[核销] 缺少必要参数 shop_id={shop_id} user_id={user_id} from_uid={from_uid}，跳过处理"
            )
            return False

        # 序列号/IMEI 要客户确认后才查：客户回「确认」时直接续上，不再走商品/模式解析
        confirmed = await self._handle_sn_confirm(content, shop_id, user_id, from_uid)
        if confirmed is not None:
            return confirmed

        # 客户回「重发」：把最近一份报告再发一次
        if _is_resend_text(content):
            if await self._resend_last_report(shop_id, user_id, from_uid):
                return True

        # 客户问「我的订单 / 待发货」：自己去后台查他这笔单，告诉他商品，再要序列号
        if _is_order_ask_text(content):
            return await self._reply_order_overview(shop_id, user_id, from_uid)

        # 服务模式分派：直发商品只看订单号，不需要卡券
        order_info = _parse_json_content(content) or {}
        order_sn = _extract_order_sn(content) or _extract_order_sn(str(order_info.get("order_id") or ""))
        goods_id = metadata.get("goods_id") or order_info.get("goods_id")
        spec = str(metadata.get("spec") or order_info.get("spec") or "")
        goods_name = str(metadata.get("goods_name") or order_info.get("goods_name") or "")

        # 有序列号/订单号却没有商品上下文时：先按「客户订单」认领。
        # 订单里的商品ID才是准的——历史消息可能停在别的商品上，据此判定就会向客户要卡券。
        claimed_order = None
        claimed_goods_id = None
        claimed = None
        if (sn or order_sn) and not coupon_no and not goods_id:
            claimed = await self._resolve_customer_ship_order(shop_id, user_id, from_uid, sn or "")
            if claimed.get("status") == "one":
                claimed_order = claimed.get("order") or {}
                order_sn = order_sn or str(claimed_order.get("order_sn") or "") or None
                claimed_goods_id = claimed_order.get("goods_id")
                goods_id = goods_id or claimed_goods_id
                spec = spec or str(claimed_order.get("spec") or "")
                goods_name = goods_name or str(claimed_order.get("goods_name") or "")
                self.logger.info(
                    f"[发货] 按客户订单认领商品 goods_id={goods_id} spec={spec!r} "
                    f"order_sn={order_sn!r} source={claimed.get('source')}")

        # 普通文本消息不带商品ID：先回溯该客户最近的订单卡/商品卡，再退到店铺「唯一待发货订单」；
        # 否则模式会退化成默认的卡券核销，客户明明是仅发货商品也被要卡券。
        picked_order = None
        goods_from_history = False
        from core.service_mode import infer_goods_id_from_session

        if not goods_id:
            goods_id = infer_goods_id_from_session(shop_id, from_uid)
            goods_from_history = bool(goods_id)
        if claimed_goods_id:
            # 订单里的商品优先于历史消息推断：客户这单买的是什么，就按什么查
            if str(goods_id or "") != str(claimed_goods_id):
                self.logger.info(
                    f"[发货] 订单商品覆盖历史推断 goods_id={claimed_goods_id}"
                    f"（历史推断到 {goods_id}）")
            goods_id = claimed_goods_id
            goods_from_history = False
        if not goods_id or not order_sn:
            picked_order = self._pick_pending_reference(shop_id, user_id, from_uid, goods_id)
            if picked_order:
                if not goods_id:
                    goods_id = picked_order.get("goods_id")
                # 规格必须一起补上：验机配置是按 goods_id + 规格名查的，缺规格就查不到 server_id
                goods_name = goods_name or str(picked_order.get("goods_name") or "")
                spec = spec or str(picked_order.get("spec") or "")

        # 订单核对：多笔待发货订单时，只要商品+规格一致，也能确定该查哪个验机服务
        common = None
        if not goods_id or not spec:
            try:
                from core.order_lookup import common_pending_goods

                common = common_pending_goods(shop_id, user_id)
            except Exception as e:
                self.logger.debug(f"[订单查询] 商品核对失败: {e}")
                common = None
            if common:
                goods_id = goods_id or common.get("goods_id")
                spec = spec or str(common.get("spec") or "")
                goods_name = goods_name or str(common.get("goods_name") or "")

        # 服务模式一律按「客户这一单」判定：订单卡 > 已认领的本单 > 订单详情 >
        # 全店唯一待发货单 > 待发货商品一致 > 历史商品卡 > 本单落库模式。
        # 判定不出来就是 None（未确定），绝不再退化成默认的卡券核销——
        # 那正是「买的是发货商品却被要卡券」的来源。
        resolution = self._resolve_order_service_mode(
            shop_id,
            from_uid,
            message_goods_id=goods_id,
            message_spec=spec,
            order_sn=order_sn,
            claimed_order=claimed_order or None,
            picked_order=picked_order,
            common_goods=common,
            history_goods_id=goods_id if goods_from_history else None,
        )
        mode = resolution.mode
        if not goods_id and resolution.goods_id:
            goods_id = resolution.goods_id
            goods_from_history = resolution.is_weak
        spec = spec or resolution.spec
        # 卡片里带的订单号直接用上：客户发过订单卡片，就不用再找他要一次订单号
        if not order_sn and getattr(resolution, "order_sn", None):
            order_sn = str(resolution.order_sn) or None
            if order_sn:
                self.logger.info(f"[发货] 用会话卡片里的订单号 order_sn={order_sn}")

        # 弱依据（只在历史消息里见过商品卡）不足以断定「这单一定要卡券」：
        # 客户这单可能是发货商品，按未确定处理。
        if mode == _SERVICE_MODE_VOUCHER and resolution.is_weak and sn and not coupon_no:
            self.logger.info(
                f"[核销] 仅凭历史消息推断出卡券商品，改为按未确定处理 goods_id={goods_id} "
                f"sn={sn} from_uid={from_uid}")
            mode = None
        mode_is_known = mode is not None

        # 分派：确定是核销商品就走核销；仅发货商品给了卡券也忽略；
        # 二选一/未确定：给了卡券就核销，没给卡券按发货推进（绝不主动索要卡券）。
        if mode == _SERVICE_MODE_VOUCHER:
            ship_branch = False
        elif mode == _SERVICE_MODE_SHIP:
            ship_branch = True
        elif coupon_no:
            ship_branch = False
        else:
            ship_branch = True

        # 客户正在核销中途（已给过卡券、就等序列号）：模式未确定时继续走核销，
        # 不能把这条序列号当成发货序列号——否则那张待核销单永远补不上序列号。
        if mode is None and pending == "need_sn":
            ship_branch = False

        # 模式未确定 + 客户只给序列号：先试着认领「客户这一单」。
        # 认到唯一一笔 → 按发货推进（客户确认 + 验机前置才真的发货）；
        # 认不到 → 保持原引导（先落库、要订单卡片），既不擅自发货、也绝不索要卡券。
        session_id = f"pinduoduo{from_uid}"
        if mode is None and sn and not coupon_no and ship_branch:
            claimed_result = await self._resolve_customer_ship_order(
                shop_id, user_id, from_uid, sn)
            if str(claimed_result.get("status") or "") == "one":
                claimed = claimed_result.get("order") or {}
                order_sn = order_sn or str(claimed.get("order_sn") or "") or None
                goods_id = goods_id or claimed.get("goods_id")
                spec = spec or str(claimed.get("spec") or "")
                goods_name = goods_name or str(claimed.get("goods_name") or "")
                self.logger.info(
                    f"[发货] 模式未确定，按客户订单认领后推进发货 order_sn={order_sn!r} "
                    f"goods_id={goods_id} spec={spec!r} source={claimed_result.get('source')}")
            else:
                final_sn, _coupon, order_id, updates = await self._upsert_order(
                    sn, None, shop_id, from_uid, order_sn=order_sn or None)
                if order_id is not None:
                    self.logger.info(
                        f"[核销] 服务模式未确定且未认领到订单，先记录序列号 "
                        f"order_id={order_id} sn={final_sn!r} order_sn={order_sn!r} "
                        f"status={claimed_result.get('status')}")
                await self._send_updates(updates, shop_id, user_id, from_uid, session_id)
                guide = (
                    f"✅ 已记录您的序列号/IMEI：{final_sn or sn}\n"
                    f"请先核对一下这个序列号/IMEI 是否正确（不对就把正确的发我）\n\n"
                    f"麻烦把【订单卡片】或【订单号】发我（点击聊天框右下角「+」→ 选择「订单」→ 发送订单卡片），"
                    f"我按订单核对查询项目后，再请您确认一次就发货并出报告～"
                )
                await self._send(shop_id, user_id, from_uid, guide)
                self._record_message(session_id, "assistant", guide)
                return True

        # 仅发货但客户没给订单号：用命中的唯一待发货订单补上，客户就不用手抄订单号
        if ship_branch and not order_sn and picked_order:
            order_sn = str(picked_order.get("order_sn") or "") or None
            if order_sn:
                self.logger.info(
                    f"[发货] 用待发货订单补全订单号 order_sn={order_sn} from_uid={from_uid}"
                )

        # 客户只发订单号：按订单号核对历史单（含已发货），能出报告就直接出
        if order_sn and not sn and not coupon_no:
            return await self._handle_order_no_only(
                shop_id, user_id, from_uid, order_sn, f"pinduoduo{from_uid}")

        if not sn and not coupon_no and not (ship_branch and order_sn):
            self.logger.info(f"[核销] 本条消息未提取到卡券号/序列号 pending={pending!r}，跳过核销处理 content={content[:40]!r}")
            return False

        self.logger.info(
            f"[核销] 提取到信息 sn={sn!r} coupon={coupon_no!r} "
            f"mode={mode if mode_is_known else None} "
            f"order_sn={order_sn!r} from_uid={from_uid} shop_id={shop_id}"
        )

        self._record_message(session_id, "user", content)

        # 模式未知但该客户之前有已发货单：**不在这里抢答**——如果客户其实拍了新单，
        # 必须先把新单认领出来让他确认（旧单只是历史记录）。历史单的处理放在
        # 「查不到待发货单」的分支里，那时才提示「要看之前的报告就回复【重发】」。

        # 模式未确定（无商品ID、无历史单、订单接口不可用）且客户只给了序列号：
        # 不再固定让客户发订单卡片——先按「客户这一单」自动认领（见 _handle_ship_branch），
        # 认到唯一一笔就请他确认后发货，认不到才要订单卡片。全程绝不索要卡券。
        # 仅发货 / 二选一兼容（本条无卡券）→ 走无物流发货；核销路径一行不动
        if ship_branch:
            return await self._handle_ship_branch(
                sn, coupon_no, order_sn, goods_id, spec, goods_name,
                shop_id, user_id, from_uid, session_id, mode, claimed)

        return await self._handle_voucher_branch(
            sn, coupon_no, shop_id, user_id, from_uid, session_id, mode)
