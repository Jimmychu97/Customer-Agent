"""
核销自动检测处理器

规则：
- 只有序列号  → 建 Order 记录，提示补充卡券号
- 只有卡券号  → 建 Order 记录，提示补充序列号
- 两个都有    → 调用外部 API 验证，成功则执行核销
"""
import re
import asyncio
from typing import Optional, Dict, Any
from bridge.context import Context, ContextType
from .base import BaseHandler


def _extract_imei(text: str) -> Optional[str]:
    """提取IMEI（15位纯数字，支持带空格格式 35 227917 289634 6）"""
    # 先直接匹配15位连续数字
    m = re.search(r'(?<!\d)(\d{15})(?!\d)', text)
    if m:
        return m.group(1)
    # 再尝试合并相邻数字段（去掉空格后为15位）
    for m in re.finditer(r'(\d[\d ]{13,20}\d)', text):
        candidate = re.sub(r'\s+', '', m.group(1))
        if len(candidate) == 15:
            return candidate
    return None


def _extract_serial(text: str) -> Optional[str]:
    """提取苹果序列号（8-12位字母数字混合，必须含字母，排除纯数字）"""
    upper = text.upper()
    for m in re.finditer(r'(?<![A-Z0-9])([A-Z0-9]{8,12})(?![A-Z0-9])', upper):
        val = m.group(1)
        # 必须含字母（排除纯数字，避免与卡券号混淆）
        # 且字母占比至少20%（避免误匹配纯数字+少量字母）
        letters = len(re.findall(r'[A-Z]', val))
        if letters >= 2:
            return val
    return None


def _extract_coupon(text: str) -> Optional[str]:
    """提取拼多多卡券号（10-14位纯数字，排除15位IMEI）"""
    for m in re.finditer(r'(?<!\d)(\d{10,14})(?!\d)', text):
        return m.group(1)
    return None


class VoucherDetectionHandler(BaseHandler):
    """
    核销自动检测处理器

    单条件：建 Order 记录并提示用户补充缺失信息。
    双条件：调用外部 API 验证通过后执行核销。
    """

    def __init__(self):
        super().__init__("VoucherDetectionHandler")

    def can_handle(self, context: Context) -> bool:
        if context.type != ContextType.TEXT:
            return False
        content = context.content or ""
        return bool(
            _extract_serial(content)
            or _extract_imei(content)
            or _extract_coupon(content)
        )

    async def handle(self, context: Context, metadata: Dict[str, Any]) -> bool:
        content = context.content or ""
        # IMEI 优先，其次序列号
        sn = _extract_imei(content) or _extract_serial(content)
        coupon_no = _extract_coupon(content)

        from_uid = metadata.get("from_uid")
        shop_id = metadata.get("shop_id")
        user_id = metadata.get("user_id")

        if not all([shop_id, user_id, from_uid]):
            self.logger.warning(
                f"[核销] 缺少必要参数 shop_id={shop_id} user_id={user_id} from_uid={from_uid}，跳过处理"
            )
            return False

        self.logger.info(
            f"[核销] 提取到信息 sn={sn!r} coupon={coupon_no!r} "
            f"from_uid={from_uid} shop_id={shop_id}"
        )

        reply = await self._upsert_and_process_async(sn, coupon_no, shop_id, user_id, from_uid)
        if reply:
            return await self._send(shop_id, user_id, from_uid, reply)
        return False

    async def _upsert_and_process_async(
        self,
        sn: Optional[str],
        coupon_no: Optional[str],
        shop_id: str,
        user_id: str,
        from_uid: str,
    ) -> Optional[str]:
        """创建或补全 Order 记录，双条件齐备时触发 API 查询+核销"""
        try:
            from database import db_manager
            from database.models import Order

            with db_manager.session_scope() as session:
                # 查找该店铺+会话所有待处理订单（包含 sn 或 coupon 任意一方已有值的）
                orders = (
                    session.query(Order)
                    .filter_by(shop_id=shop_id, session_id=from_uid, status=0)
                    .order_by(Order.created_at.desc())
                    .all()
                )

                # 优先找能合并的：有 sn 无 coupon，或有 coupon 无 sn
                order = None
                for o in orders:
                    if sn and not o.sn_imei and o.coupons:   # 本次带 sn，找有 coupon 无 sn 的
                        order = o; break
                    if coupon_no and not o.coupons and o.sn_imei:  # 本次带 coupon，找有 sn 无 coupon 的
                        order = o; break
                # 没找到可合并的，取最新一条
                if order is None and orders:
                    order = orders[0]

                if order is None:
                    self.logger.info(
                        f"[核销] 未找到已有 Order，新建记录 "
                        f"sn={sn!r} coupon={coupon_no!r} from_uid={from_uid}"
                    )
                    order = Order(
                        shop_id=shop_id,
                        session_id=from_uid,
                        sn_imei=sn or "",
                        coupons=coupon_no or "",
                        status=0,
                    )
                    session.add(order)
                else:
                    self.logger.info(
                        f"[核销] 找到已有 Order id={order.id}，"
                        f"DB sn_imei={order.sn_imei!r} coupons={order.coupons!r}，"
                        f"本次传入 sn={sn!r} coupon={coupon_no!r}"
                    )
                    # 仅补全空字段，不覆盖已有值
                    if sn and not order.sn_imei:
                        self.logger.info(f"[核销] 补全 sn_imei={sn!r}")
                        order.sn_imei = sn
                    if coupon_no and not order.coupons:
                        self.logger.info(f"[核销] 补全 coupons={coupon_no!r}")
                        order.coupons = coupon_no

                session.flush()
                order_id = order.id
                final_serial = order.sn_imei or ""
                final_coupon = order.coupons or ""
                # session_scope 自动 commit

            self.logger.info(
                f"[核销] Order 已保存 order_id={order_id} "
                f"sn_imei={final_serial!r} coupons={final_coupon!r}"
            )

            # 单条件：提示补充缺失字段
            if not final_serial and not final_coupon:
                self.logger.warning(f"[核销] Order id={order_id} 双字段均为空，逻辑异常")
                return "处理异常，请重新发送序列号或卡券号。"
            if not final_serial:
                self.logger.info(f"[核销] Order id={order_id} 缺少序列号，提示用户补充")
                return (
                    f"✅ 已记录您的卡券号：{final_coupon}\n"
                    f"\n"
                    f"请继续提供以下任意一项完成核销：\n"
                    f"\n"
                    f"📱 IMEI码（推荐）\n"
                    f"拨号界面输入 *#06# 即可显示\n"
                    f"\n"
                    f"🔢 序列号\n"
                    f"设置 → 通用 → 关于本机 → 序列号"
                )
            if not final_coupon:
                self.logger.info(f"[核销] Order id={order_id} 缺少卡券，提示用户补充")
                return (
                    f"✅ 已记录您的序列号/IMEI：{final_serial}\n"
                    f"\n"
                    f"请提供卡券号完成核销：\n"
                    f"待收货 → 点击商品 → 查看卡券 → 复制卡券发给客服"
                )

            # 双条件：双字段都齐备，触发 API 查询+核销
            self.logger.info(f"[核销] Order id={order_id} 双条件齐备，触发核销流程")
            await self._send(shop_id, user_id, from_uid, "好的，正在查询~")
            loop = asyncio.get_event_loop()
            return await asyncio.to_thread(
                self._query_and_verify,
                final_serial, final_coupon, shop_id, user_id, from_uid, order_id, loop
            )

        except Exception as e:
            self.logger.opt(exception=True).error(f"[核销] Order 处理异常: {e}")
            return "处理时发生错误，请稍后重试。"

    def _query_and_verify(
        self,
        serial: str,
        voucher_code: str,
        shop_id: str,
        user_id: str,
        from_uid: str,
        order_id: int,
        loop: asyncio.AbstractEventLoop = None,
    ) -> str:
        """MMS 验券 + 核销"""
        from Channel.pinduoduo.utils.API.mms_voucher_api import MmsVoucherAPI
        api = MmsVoucherAPI(shop_id=shop_id, user_id=user_id)

        # 1. 验券
        preview = api.preview(voucher_code)
        if not preview["success"]:
            self._update_order_status(order_id, 2)
            return f"验券失败：{preview.get('error_msg', '未知错误')}"

        goods_name = preview.get("goods_name", "")
        spec = preview.get("spec", "")
        order_sn = preview.get("order_sn", "")
        order_amount = preview.get("order_amount", 0)
        store_id = preview.get("store_id", 0)

        self.logger.info(
            f"[核销] 验券成功 card_no={voucher_code} order_sn={order_sn} goods={goods_name}"
        )

        # 2. 执行核销，把 order_sn 一起传入
        result = api.verification(voucher_code, store_id=store_id or 0, order_sn=order_sn or "")
        if result["success"]:
            self._update_order_status(order_id, 1)
            self.logger.info(f"[核销] 核销成功 serial={serial} card_no={voucher_code} order_id={order_id}")

            # 3. 通过 goods_id + spec 匹配 ServerId，调 GSX 查询（异步，查询完再发给用户）
            if loop is not None:
                asyncio.run_coroutine_threadsafe(
                    self._query_gsx_and_send(
                        serial, str(preview.get("goods_id", "")), spec, order_id, shop_id, user_id, from_uid
                    ),
                    loop
                )

            return (
                f"核销成功！\n"
                f"序列号/IMEI：{serial}\n"
                f"卡券号：{voucher_code}\n"
                f"商品：{goods_name}\n"
                + (f"规格：{spec}\n" if spec else "")
                + f"订单号：{order_sn}\n"
                f"订单金额：{order_amount / 100:.2f}元"
            )
        else:
            self.logger.warning(f"[核销] 核销失败 card_no={voucher_code} order_id={order_id} err={result.get('error_msg')}")
            self._update_order_status(order_id, 2)
            return f"核销失败：{result.get('error_msg', '未知错误')}\n卡券号：{voucher_code}"

    async def _query_gsx_and_send(
        self, serial: str, goods_id: str, spec: str,
        order_id: int, shop_id: str, user_id: str, from_uid: str
    ) -> None:
        """异步调 GSX 查询，保存结果并发送给用户"""
        import json
        try:
            from database import db_manager
            from database.models import GoodsCommit, Order
            from Channel.pinduoduo.utils.API.gsx_api import query_gsx

            with db_manager.session_scope() as session:
                gc = session.query(GoodsCommit).filter_by(
                    goods_id=goods_id,
                    spec_name=spec,
                ).first()
                server_id = gc.sku_id if gc else None

            if not server_id:
                self.logger.warning(f"GSX查询：未找到 goods_id={goods_id} spec={spec} 对应的 ServerId")
                return

            result = await asyncio.to_thread(query_gsx, serial, server_id)
            if not result["success"]:
                self.logger.warning(f"[核销] GSX查询失败 sn={serial}: {result.get('error_msg')}")
                return

            gsx_data = result["data"]
            self.logger.info(f"[核销] GSX查询完成 server_id={server_id} sn={serial}")

            with db_manager.session_scope() as session:
                order = session.query(Order).filter_by(id=order_id).first()
                if order:
                    order.sku_id = server_id
                    order.gsx_result = json.dumps(gsx_data, ensure_ascii=False)

            gsx_text = json.dumps(gsx_data, ensure_ascii=False)
            fixed = "亲，五行好平+关注+收藏+截图+文字好平赏个。以后有关于这部机器的验机问题，都可以随时联系美高技术员，技术员将为您解答！非常感谢您选择我们,祝你购机愉快~"
            await self._send(shop_id, user_id, from_uid, f"{gsx_text}\n\n{fixed}")

        except Exception as e:
            self.logger.opt(exception=True).error(f"[核销] GSX查询失败: {e}")

    def _update_order_status(self, order_id: int, status: int) -> None:
        """更新 Order 状态"""
        try:
            from database import db_manager
            from database.models import Order
            with db_manager.session_scope() as session:
                order = session.query(Order).filter_by(id=order_id).first()
                if order:
                    order.status = status
        except Exception as e:
            self.logger.opt(exception=True).error(f"[核销] 更新 Order 状态失败 order_id={order_id}: {e}")

    async def _send(self, shop_id: str, user_id: str, from_uid: str, text: str) -> bool:
        try:
            from Channel.pinduoduo.utils.API.send_message import SendMessage
            result = SendMessage(shop_id, user_id).send_text(from_uid, text)
            return isinstance(result, dict) and result.get("success", False)
        except Exception as e:
            self.logger.opt(exception=True).error(f"[核销] 发送回复失败 from_uid={from_uid}: {e}")
            return False
