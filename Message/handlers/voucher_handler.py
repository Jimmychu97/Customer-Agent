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


def _luhn_check(number: str) -> bool:
    """Luhn 算法校验 IMEI是否合法"""
    digits = [int(d) for d in number]
    odd_sum = sum(digits[-1::-2])
    even_sum = sum(sum(divmod(d * 2, 10)) for d in digits[-2::-2])
    return (odd_sum + even_sum) % 10 == 0


def _extract_imei(text: str) -> Optional[str]:
    """提取 IMEI
    规则：15位纯数字 + Luhn 算法校验
    支持带空格格式：35 227917 289634 6
    """
    # 先尝试连续15位
    for m in re.finditer(r'(?<!\d)(\d{15})(?!\d)', text):
        candidate = m.group(1)
        if _luhn_check(candidate):
            return candidate
    # 再尝试带空格格式
    for m in re.finditer(r'(\d[\d ]{13,20}\d)', text):
        candidate = re.sub(r'\s+', '', m.group(1))
        if len(candidate) == 15 and _luhn_check(candidate):
            return candidate
    return None


def _extract_serial(text: str) -> Optional[str]:
    """提取苹果序列号
    苹果序列号规则：
    - 新款（2021年后随机序列号）：固定 12 位
    - 旧款：固定 10 位
    - 字符集：A-Z + 0-9，排除 I、O（苹果官方不使用）
    - 必须同时含字母和数字（排除纯数字、纯字母）
    - 用户输入的 o/O 自动替换为 0
    """
    APPLE_CHARS = r'[A-HJ-NP-Z0-9]'  # 排除 I、O
    normalized = text.replace('o', '0').replace('O', '0').upper()
    # 苹果序列号固定 10 位或 12 位
    for length in (12, 10):
        for m in re.finditer(
            rf'(?<![A-Z0-9])({APPLE_CHARS}{{{length}}})(?![A-Z0-9])', normalized
        ):
            val = m.group(1)
            if re.search(r'[A-Z]', val):
                return val
    return None


def _extract_coupon(text: str) -> Optional[str]:
    """提取拼多多卡券号（10-14位纯数字，支持带空格格式，排除15位IMEI）"""
    # 先尝试连续数字（10-14位）
    for m in re.finditer(r'(?<!\d)(\d{10,14})(?!\d)', text):
        return m.group(1)
    # 再尝试带空格格式，合并后10-14位
    for m in re.finditer(r'(\d[\d ]{8,18}\d)', text):
        candidate = re.sub(r'\s+', '', m.group(1))
        if 10 <= len(candidate) <= 14:
            return candidate
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
            goods_id, spec, report_data, device_type = gsx_params
            if device_type == "apple":
                await self._query_lingsu_and_send(sn, goods_id, spec, report_data, order_id, shop_id, user_id, from_uid, result_text)
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
            return f"验券失败：{preview.get('error_msg', '未知错误')}", None

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
            from Channel.pinduoduo.utils.API.lingsu_gsx_api import query_lingsu
            zx_result = query_lingsu(serial, zhenxin_sid)
            if not zx_result["success"]:
                self.logger.warning(
                    f"[核销] 凌速查询失败，序列号可能有误 sn={serial}: {zx_result.get('error_msg')}"
                )
                self._clear_order_sn(order_id)
                return (
                    f"序列号/IMEI 查询失败，可能有误：{zx_result.get('error_msg', '未知错误')}\n"
                    f"请重新提供正确的序列号或IMEI码"
                ), None
            report_data = zx_result["result"]  # 纯文本
        elif server_id:
            from Channel.pinduoduo.utils.API.gsx_api import query_gsx
            gsx_result = query_gsx(serial, server_id)
            if not gsx_result["success"]:
                self.logger.warning(
                    f"[核销] GSX查询失败，序列号可能有误 sn={serial} key={server_id}: {gsx_result.get('error_msg')}"
                )
                self._clear_order_sn(order_id)
                return (
                    f"序列号/IMEI 查询失败，可能有误：{gsx_result.get('error_msg', '未知错误')}\n"
                    f"请重新提供正确的序列号或IMEI码"
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
            return f"核销失败：{result.get('error_msg', '未知错误')}\n卡券号：{voucher_code}", None

    def _get_query_config(self, goods_id: str, spec: str) -> dict:
        """从 GoodsCommit 查询验机配置"""
        try:
            from database import db_manager
            from database.models import GoodsCommit
            with db_manager.session_scope() as session:
                gc = session.query(GoodsCommit).filter_by(
                    goods_id=goods_id, spec_name=spec
                ).first()
                if not gc:
                    return {"server_id": None, "zhenxin_server_id": None, "device_type": None}
                return {
                    "server_id":         str(gc.server_id) if gc.server_id else None,
                    "zhenxin_server_id": str(gc.zhenxin_server_id) if gc.zhenxin_server_id else None,
                    "device_type":       gc.device_type,
                }
        except Exception as e:
            self.logger.error(f"[核销] 查询验机配置失败: {e}")
            return {"server_id": None, "zhenxin_server_id": None, "device_type": None}

    def _clear_order_sn(self, order_id: Optional[int]) -> None:
        """清空 Order 的 sn_imei，让用户可以重新提供序列号"""
        if not order_id:
            return
        try:
            from database import db_manager
            from database.models import Order
            with db_manager.session_scope() as session:
                order = session.query(Order).filter_by(id=order_id).first()
                if order:
                    order.sn_imei = ""
        except Exception as e:
            self.logger.error(f"[核销] 清空 sn_imei 失败 order_id={order_id}: {e}")

    async def _query_lingsu_and_send(
        self, serial: str, goods_id: str, spec: str, report_text: Optional[str],
        order_id: int, shop_id: str, user_id: str, from_uid: str,
        voucher_success_text: str = ""
    ) -> None:
        """苹果凌速验机报告发送流程
        发送顺序：验机报告 → AI分析 → 核销成功 → 五星好评
        """
        try:
            from database import db_manager
            from database.models import Order

            if not report_text:
                self.logger.warning(f"[核销] 无凌速验机数据，跳过报告发送 sn={serial}")
                if voucher_success_text:
                    await self._send(shop_id, user_id, from_uid, voucher_success_text)
                return

            query_cfg = self._get_query_config(goods_id, spec)
            with db_manager.session_scope() as session:
                order = session.query(Order).filter_by(id=order_id).first()
                if order:
                    order.sku_id = query_cfg.get("zhenxin_server_id") or ""
                    order.gsx_result = report_text

            fixed = "亲，五行好平+关注+收藏+截图+文字好平赏个。以后有关于这部机器的验机问题，都可以随时联系美高技术员，技术员将为您解答！非常感谢您选择我们,祝你购机愉快~"
            session_id = f"pinduoduo{from_uid}"

            # 1. 发送验机报告原文
            await self._send(shop_id, user_id, from_uid, report_text)

            # 2. LLM 分析
            analysis = await self._llm_analyze_lingsu(report_text, session_id)
            if analysis:
                await self._send(shop_id, user_id, from_uid, analysis)

            # 3. 核销成功消息
            if voucher_success_text:
                await self._send(shop_id, user_id, from_uid, voucher_success_text)

            # 4. 固定结语
            await self._send(shop_id, user_id, from_uid, fixed)

        except Exception as e:
            self.logger.opt(exception=True).error(f"[核销] 凌速报告发送失败: {e}")

    async def _llm_analyze_lingsu(self, report_text: str, session_id: str) -> str:
        """苹果凌速验机报告 LLM 分析"""
        try:
            from Agent.CustomerAgent.custom.llm_client import LLMClient
            from Agent.CustomerAgent.custom.agent_config import AgentConfig
            from Agent.CustomerAgent.custom.session_manager import SessionManager

            agent_config = AgentConfig.load_from_config()
            if not agent_config.validate():
                self.logger.warning("[核销] LLM 配置无效，跳过凌速分析")
                return ""

            llm_client = LLMClient(
                api_key=agent_config.api_key,
                api_base=agent_config.api_base,
                model_name=agent_config.model_name,
                temperature=agent_config.temperature,
            )
            await llm_client.initialize()

            prompt = (
                "你是一名专业的苹果验机顾问。根据以下验机报告，"
                "直接输出【整体状况总结与购买建议】，不要重复报告原文，不要逐项解释。\n"
                "重要规则：\n"
                "- 报告中若出现维修记录相关字段，必须如实在风险中说明，不得省略或美化。\n"
                "- 只有苹果官方/授权服务商的维修才可标注为官方维修，私下维修必须明确指出。\n"
                "- 报告中未出现维修相关字段时，不要主动提及维修情况。\n"
                "格式要求：\n"
                "✅ 优点：2-3条简短说明\n"
                "⚠️ 风险：1-5条简短说明（有维修记录时必须列出）\n"
                "🏆 建议：1-3句购买建议\n"
                "总字数控制在230字以内，如有疑问可继续询问。\n\n"
                f"验机报告：\n{report_text}"
            )

            response = await llm_client.chat(
                messages=[{"role": "user", "content": prompt}],
                tool_choice="none",
            )
            analysis = (response.content or "").strip()

            session_content = f"[已完成凌速验机查询]\n验机报告：\n{report_text}"
            if analysis:
                self.logger.info(f"[核销] 凌速分析完成，长度={len(analysis)}")
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
            self.logger.opt(exception=True).warning(f"[核销] LLM 分析凌速报告失败: {e}")
            return ""

    async def _query_gsx_and_send(
        self, serial: str, goods_id: str, spec: str, gsx_data: Optional[dict],
        order_id: int, shop_id: str, user_id: str, from_uid: str,
        voucher_success_text: str = ""
    ) -> None:
        """发送 GSX 报告（数据已在 _query_and_verify 中获取）
        发送顺序：GSX报告 → AI分析 → 核销成功 → 五星好评
        """
        try:
            from database import db_manager
            from database.models import Order

            if not gsx_data:
                self.logger.warning(f"[核销] 无 GSX 数据，跳过报告发送 sn={serial}")
                if voucher_success_text:
                    await self._send(shop_id, user_id, from_uid, voucher_success_text)
                return

            query_cfg = self._get_query_config(goods_id, spec)
            with db_manager.session_scope() as session:
                order = session.query(Order).filter_by(id=order_id).first()
                if order:
                    order.sku_id = query_cfg.get("server_id") or ""
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
                "你是一名专业的苹果验机顾问。根据以下 GSX 验机报告，"
                "直接输出【整体状况总结与购买建议】，不要重复报告原文，不要逐项解释。\n"
                "重要规则：\n"
                "- 报告中若出现维修记录相关字段，必须如实在风险中说明，不得省略或美化。\n"
                "- 只有苹果官方/授权服务商的维修才可标注为官方维修，私下维修必须明确指出。\n"
                "- 报告中未出现维修相关字段时，不要主动提及维修情况。\n"
                "格式要求：\n"
                "✅ 优点：2-3条简短说明\n"
                "⚠️ 风险：1-2条简短说明（有维修记录时必须列出）\n"
                "🏆 建议：1-2句购买建议\n"
                "总字数控制在200字以内，如有疑问可继续询问。\n\n"
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
