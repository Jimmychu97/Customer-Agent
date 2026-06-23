"""
核销自动检测处理器

规则：
- 只有序列号/IMEI → 建 Order 记录，引导用户补充卡券号（截断链，AI不介入）
- 只有卡券号      → 建 Order 记录，引导用户补充序列号/IMEI（截断链，AI不介入）
- 两个都有        → 调用外部 API 验证核销
  - 核销成功      → 发成功回复，放行链（AI可继续回答后续问题）
  - 核销失败/异常 → 发失败回复，截断链
"""
import re
import json
import asyncio
from typing import Optional, Dict, Any, Tuple
from bridge.context import Context, ContextType
from .base import BaseHandler


def _extract_imei(text: str) -> Optional[str]:
    """提取IMEI（15位纯数字，支持带空格格式 35 227917 289634 6）"""
    m = re.search(r'(?<!\d)(\d{15})(?!\d)', text)
    if m:
        return m.group(1)
    for m in re.finditer(r'(\d[\d ]{13,20}\d)', text):
        candidate = re.sub(r'\s+', '', m.group(1))
        if len(candidate) == 15:
            return candidate
    return None


def _extract_serial(text: str) -> Optional[str]:
    """提取苹果序列号
    苹果序列号规则：
    - 8-12位字母数字混合
    - 只含大写字母 A-Z 和数字 0-9
    - 不含 O（字母）、I、S（易与 0、5 混淡）
    - 必须含有字母（排除纯数字）
    """
    # 苹果序列号不包含 O、I、S
    APPLE_CHARS = r'[A-HJ-NP-RT-Z0-9]'
    upper = text.upper()
    for m in re.finditer(rf'(?<![A-Z0-9])({APPLE_CHARS}{{8,12}})(?![A-Z0-9])', upper):
        val = m.group(1)
        letters = len(re.findall(r'[A-Z]', val))
        if letters >= 2:
            return val
    return None


def _extract_coupon(text: str) -> Optional[str]:
    """提取拼多多卡券号（10-14位纯数字，排除15位IMEI）"""
    for m in re.finditer(r'(?<!\d)(\d{10,14})(?!\d)', text):
        return m.group(1)
    return None


# 核销结果常量
_VOUCHER_PENDING = "pending"   # 信息不完整，引导补充
_VOUCHER_SUCCESS = "success"   # 核销成功
_VOUCHER_FAILED  = "failed"    # 核销失败（API返回失败）
_VOUCHER_ERROR   = "error"     # 处理异常


def _format_gsx_result(data) -> str:
    """将 GSX 返回的 dict 或 list 格式化为可读文本"""
    if isinstance(data, list):
        data = data[0] if data else {}
    if not isinstance(data, dict):
        return str(data)
    return "\n".join(f"{k}：{v}" for k, v in data.items())


def _analyze_gsx_result(data) -> str:
    """基于 GSX 返回字段生成简明中文简析"""
    if isinstance(data, list):
        data = data[0] if data else {}
    if not isinstance(data, dict):
        return ""

    lines = []

    model = data.get("机型", "")
    if model:
        lines.append(f"● 机型：{model}")

    capacity = data.get("容量", "")
    color = data.get("颜色", "")
    if capacity:
        lines.append(f"● 容量：{capacity}")
    if color:
        lines.append(f"● 颜色：{color}")

    id_status = str(data.get("ID黑白", ""))
    if id_status:
        ok = "Clean" in id_status or "白名单" in id_status
        lines.append(f"● ID黑白：{'✅ 干净（白名单）' if ok else '⚠️ ' + id_status}")

    lock = str(data.get("网络锁", "") or data.get("是否有锁", ""))
    if lock:
        if "Unlocked" in lock or "无锁" in lock:
            lines.append("● 网络锁：✅ 无锁")
        elif "ON" in lock or "有锁" in lock:
            lines.append("● 网络锁：⚠️ 有锁（请确认运营商）")
        else:
            lines.append(f"● 网络锁：{lock}")

    activation = str(data.get("激活状态", ""))
    if activation:
        lines.append(f"● 激活状态：{activation}")

    activate_date = str(data.get("激活日期", ""))
    if activate_date:
        lines.append(f"● 激活日期：{activate_date}")

    warranty = str(data.get("保修状态", "") or data.get("保修剩余", ""))
    if warranty:
        in_w = "Active" in warranty or "在保" in warranty
        lines.append(f"● 保修：{'✅ ' if in_w else ''}{warranty}")

    official_replace = str(data.get("是否官换", "") or data.get("官换", ""))
    if official_replace:
        replaced = official_replace.strip() in ("是", "Yes")
        lines.append(f"● 官换机：{'⚠️ 是' if replaced else '✅ 否'}")

    official_refurb = str(data.get("是否官翻机", ""))
    if official_refurb:
        refurb = official_refurb.strip() in ("是", "Yes")
        lines.append(f"● 官翻机：{'⚠️ 是' if refurb else '✅ 否'}")

    blacklist = str(data.get("美国黑名单", "") or data.get("黑名单状态（GSMA）", ""))
    if blacklist:
        clean = "Clean" in blacklist or "白名单" in blacklist
        lines.append(f"● 黑名单：{'✅ 干净' if clean else '⚠️ ' + blacklist}")

    return "\n".join(lines) if lines else ""


class VoucherDetectionHandler(BaseHandler):
    """
    核销自动检测处理器

    引导中（单条件）：发引导提示，截断链（AI不介入，避免干扰核销流程）。
    核销成功：发成功回复，放行链（AI可继续回答客户后续问题）。
    核销失败/异常：发失败回复，截断链。
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

        # 只有卡券，没有序列号/IMEI → 存单后检查是否已凑齐
        if coupon_no and not sn:
            final_sn, final_coupon, order_id = await self._upsert_order(sn, coupon_no, shop_id, from_uid)
            if final_sn and final_coupon:
                # 凑齐了，触发核销
                reply, _ = await self._do_verify(final_sn, final_coupon, shop_id, user_id, from_uid, order_id)
                if reply:
                    await self._send(shop_id, user_id, from_uid, reply)
            else:
                await self._send(shop_id, user_id, from_uid,
                    f"✅ 已记录您的卡券号：{coupon_no}\n"
                    f"\n"
                    f"请继续提供以下任意一项完成核销：\n"
                    f"\n"
                    f"📱 IMEI码（推荐）\n"
                    f"拨号界面输入 *#06# 即可显示\n"
                    f"\n"
                    f"🔢 序列号\n"
                    f"设置 → 通用 → 关于本机 → 序列号"
                )
            return True

        # 只有序列号/IMEI，没有卡券 → 存单后检查是否已凑齐
        if sn and not coupon_no:
            final_sn, final_coupon, order_id = await self._upsert_order(sn, coupon_no, shop_id, from_uid)
            if final_sn and final_coupon:
                # 凑齐了，触发核销
                reply, _ = await self._do_verify(final_sn, final_coupon, shop_id, user_id, from_uid, order_id)
                if reply:
                    await self._send(shop_id, user_id, from_uid, reply)
            else:
                await self._send(shop_id, user_id, from_uid,
                    f"✅ 已记录您的序列号/IMEI：{sn}\n"
                    f"\n"
                    f"请提供卡券号完成核销：\n"
                    f"待收货 → 点击商品 → 查看卡券 → 复制卡券发给客服"
                )
            return True

        # 同一条消息里两个都有 → 直接核销
        _, __, order_id = await self._upsert_order(sn, coupon_no, shop_id, from_uid)
        reply, _ = await self._do_verify(sn, coupon_no, shop_id, user_id, from_uid, order_id)
        if reply:
            await self._send(shop_id, user_id, from_uid, reply)
        return True

    async def _upsert_order(
        self,
        sn: Optional[str],
        coupon_no: Optional[str],
        shop_id: str,
        from_uid: str,
    ) -> Tuple[str, str, Optional[int]]:
        """建单或补全已有单

        Returns:
            (final_sn, final_coupon, order_id)
        """
        try:
            from database import db_manager
            from database.models import Order
            from datetime import datetime as dt, timedelta

            ten_min_ago = dt.now() - timedelta(minutes=10)
            with db_manager.session_scope() as session:
                order = session.query(Order).filter(
                    Order.shop_id == shop_id,
                    Order.session_id == from_uid,
                    Order.status == 0,
                    Order.created_at >= ten_min_ago,
                ).order_by(Order.id.desc()).first()

                if order is None:
                    order = Order(
                        shop_id=shop_id,
                        session_id=from_uid,
                        sn_imei=sn or "",
                        coupons=coupon_no or "",
                        status=0,
                    )
                    session.add(order)
                    session.flush()
                    self.logger.info(f"[核销] 新建 Order sn={sn!r} coupon={coupon_no!r}")
                else:
                    if sn and not order.sn_imei:
                        order.sn_imei = sn
                    if coupon_no and not order.coupons:
                        order.coupons = coupon_no
                    session.flush()
                    self.logger.info(f"[核销] 补全 Order id={order.id} sn={order.sn_imei!r} coupon={order.coupons!r}")

                return order.sn_imei or "", order.coupons or "", order.id
        except Exception as e:
            self.logger.error(f"[核销] 建单失败: {e}")
            return sn or "", coupon_no or "", None

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
        self.logger.info(f"[核销] 双条件齐备，触发核销 order_id={order_id}")
        await self._send(shop_id, user_id, from_uid, "好的，正在查询~")
        result_text, gsx_params = await asyncio.to_thread(
            self._query_and_verify,
            sn, coupon_no, shop_id, user_id, from_uid, order_id
        )
        status = _VOUCHER_SUCCESS if result_text and "核销成功" in result_text else _VOUCHER_FAILED
        if status == _VOUCHER_SUCCESS and gsx_params:
            goods_id, spec = gsx_params
            # GSX报告 → AI分析 → 核销成功 → 五星好评
            await self._query_gsx_and_send(sn, goods_id, spec, order_id, shop_id, user_id, from_uid, result_text)
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
        """MMS 验券 + 核销，返回 (result_text, gsx_params)"""
        from Channel.pinduoduo.utils.API.mms_voucher_api import MmsVoucherAPI
        api = MmsVoucherAPI(shop_id=shop_id, user_id=user_id)

        preview = api.preview(voucher_code)
        if not preview["success"]:
            self._update_order_status(order_id, 2)
            return f"验券失败：{preview.get('error_msg', '未知错误')}", None

        goods_name = preview.get("goods_name", "")
        spec = preview.get("spec", "")
        order_sn = preview.get("order_sn", "")
        order_amount = preview.get("order_amount", 0)
        store_id = preview.get("store_id", 0)

        self.logger.info(
            f"[核销] 验券成功 card_no={voucher_code} order_sn={order_sn} goods={goods_name}"
        )

        result = api.verification(voucher_code, store_id=store_id or 0, order_sn=order_sn or "")
        if result["success"]:
            self._update_order_status(order_id, 1)
            self.logger.info(f"[核销] 核销成功 serial={serial} card_no={voucher_code} order_id={order_id}")
            gsx_params = (str(preview.get("goods_id", "")), spec)
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
            return f"核销失败：{result.get('error_msg', '未知错误')}\n卡券号：{voucher_code}", None

    async def _query_gsx_and_send(
        self, serial: str, goods_id: str, spec: str,
        order_id: int, shop_id: str, user_id: str, from_uid: str,
        voucher_success_text: str = ""
    ) -> None:
        """异步调 GSX 查询，保存结果并发送给用户
        发送顺序：GSX报告 → AI分析 → 核销成功 → 五星好评
        """
        try:
            from database import db_manager
            from database.models import GoodsCommit, Order
            from Channel.pinduoduo.utils.API.gsx_api import query_gsx

            with db_manager.session_scope() as session:
                gc = session.query(GoodsCommit).filter_by(
                    goods_id=goods_id,
                    spec_name=spec,
                ).first()
                gsx_key = str(gc.server_id) if gc and gc.server_id else None

            if not gsx_key:
                self.logger.warning(
                    f"GSX查询：未在 GoodsCommit 找到 "
                    f"goods_id={goods_id} spec_name={spec!r} 对应的 server_id，跳过 GSX 查询"
                )
                return

            result = await asyncio.to_thread(query_gsx, serial, gsx_key)
            if not result["success"]:
                self.logger.warning(f"[核销] GSX查询失败 sn={serial}: {result.get('error_msg')}")
                return

            gsx_data = result["data"]
            self.logger.info(f"[核销] GSX查询完成 sn={serial} key={gsx_key}")

            with db_manager.session_scope() as session:
                order = session.query(Order).filter_by(id=order_id).first()
                if order:
                    order.sku_id = gsx_key
                    order.gsx_result = json.dumps(gsx_data, ensure_ascii=False)

            gsx_text = _format_gsx_result(gsx_data)
            fixed = "亲，五行好平+关注+收藏+截图+文字好平赏个。以后有关于这部机器的验机问题，都可以随时联系美高技术员，技术员将为您解答！非常感谢您选择我们,祝你购机愉快~"
            session_id = f"pinduoduo{from_uid}"

            # 1. 发送 GSX 原始报告
            await self._send(shop_id, user_id, from_uid, gsx_text)

            # 2. 复用 CustomerAgent LLM 客户端生成分析，结果写入会话历史
            analysis = await self._llm_analyze_gsx(gsx_text, session_id)
            if analysis:
                await self._send(shop_id, user_id, from_uid, analysis)

            # 3. 发送核销成功消息
            if voucher_success_text:
                await self._send(shop_id, user_id, from_uid, voucher_success_text)

            # 4. 发送固定结语
            await self._send(shop_id, user_id, from_uid, fixed)

        except Exception as e:
            self.logger.opt(exception=True).error(f"[核销] GSX查询失败: {e}")

    async def _llm_analyze_gsx(self, gsx_text: str, session_id: str) -> str:
        """用独立 LLMClient 分析 GSX 报告，并将结果写入会话历史"""
        try:
            from Agent.CustomerAgent.custom.llm_client import LLMClient
            from Agent.CustomerAgent.custom.agent_config import AgentConfig
            from Agent.CustomerAgent.custom.session_manager import SessionManager

            agent_config = AgentConfig.load_from_config()
            if not agent_config.validate():
                self.logger.warning("[核销] LLM 配置无效，跳过GSX分析")
                return ""

            llm_client = LLMClient(
                api_key=agent_config.api_key,
                api_base=agent_config.api_base,
                model_name=agent_config.model_name,
                temperature=agent_config.temperature,
            )
            await llm_client.initialize()

            prompt = (
                "你是一名专业的苹果手机验机顾问，请根据以下 GSX 验机报告，"
                "用简单易懂的中文向买家详细解释每一项内容的含义，"
                "重点说明该机器的整体状况、是否有风险、值不值得购买，"
                "最后告知用户如有疑问可以继续询问。\n\n"
                f"验机报告：\n{gsx_text}"
            )

            response = await llm_client.chat(
                messages=[{"role": "user", "content": prompt}],
                tool_choice="none",
            )
            analysis = (response.content or "").strip()

            # 把 GSX 报告和分析写入会话历史
            session_content = f"[已完成 GSX 验机查询]\n验机报告：\n{gsx_text}"
            if analysis:
                self.logger.info(f"[核销] GSX分析完成，长度={len(analysis)}")
                session_content += f"\n\n分析：\n{analysis}"

            session_manager = SessionManager(
                db_path=agent_config.db_path,
                token_window=agent_config.token_window,
                compress_ratio=agent_config.compress_ratio,
                retain_count=agent_config.retain_count,
                model_name=agent_config.model_name,
            )
            session_manager.add_message(
                session_id=session_id,
                role="assistant",
                content=session_content,
            )
            return analysis

        except Exception as e:
            self.logger.opt(exception=True).warning(f"[核销] LLM 分析 GSX 报告失败: {e}")
            return ""

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
            success = isinstance(result, dict) and result.get("success", False)
            if success:
                self.logger.info(f"[核销] 发送成功 from_uid={from_uid} text={text[:30]!r}")
            else:
                self.logger.warning(f"[核销] 发送失败 from_uid={from_uid} result={result}")
            return success
        except Exception as e:
            self.logger.opt(exception=True).error(f"[核销] 发送回复失败 from_uid={from_uid}: {e}")
            return False
