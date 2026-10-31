"""验机报告：GSX / 凌速 / 解锁查询与 LLM 简析。"""
from .report import _format_gsx_result
from .texts import _FIXED_REVIEW_TEXT
from .texts import _REPORT_ALREADY_SENT_TEXT
from .texts import _SHIP_ERROR
from .texts import _SHIP_FAILED
from .texts import _SHIP_SUCCESS
from typing import Optional
from typing import Tuple
import asyncio
import json


class ReportMixin:
    async def _query_report_only(self, sn: str, goods_id, spec: str, shop_id: str,
                                 user_id: str, from_uid: str,
                                 order_id: Optional[int]) -> Tuple[Optional[str], str]:
        """只查询并发送验机报告（不发货）

        客户只发序列号、还没给订单号时走这里：验机报告只依赖「序列号 + 商品/规格」，
        不依赖订单号，所以可以先出报告，再引导补订单号发货，不让客户干等。

        Returns:
            (reply, status)；reply 为 None 表示报告链已经把消息全部发完
        """
        cfg = self._resolve_query_config(goods_id, spec)
        server_id = cfg.get("server_id")
        zhenxin_sid = cfg.get("zhenxin_server_id")
        device_type = cfg.get("device_type")
        spec_text = spec or ""
        goods_text = str(goods_id or "")
        success_text = (
            f"✅ 已收到序列号/IMEI：{sn}\n"
            f"报告已发出；如需发货，把拼多多订单号发我"
            f"（点击聊天框右下角「+」→ 选择「订单」→ 发送订单卡片）"
        )

        if not (server_id or zhenxin_sid):
            note, _matched = self._pending_ship_note(shop_id, user_id)
            self.logger.warning(
                f"[发货] 仅查询模式：未匹配到验机服务，跳过查询，改要订单核对 "
                f"goods_id={goods_text} spec={spec_text!r}")
            return (
                f"✅ 已收到序列号/IMEI：{sn}\n\n"
                + note
                + "\n麻烦把订单卡片发我（点击聊天框右下角「+」→ 选择「订单」→ 发送订单卡片），"
                  "我按订单核对后马上帮您查～"
            ), _SHIP_ERROR

        # 序列号/IMEI 必须客户确认后才查验机
        if self._serial_needs_confirm(shop_id, from_uid, sn):
            await self._ask_serial_confirm(
                sn, goods_id, spec_text, order_id, "", shop_id, user_id, from_uid)
            return None, _SHIP_SUCCESS

        # 刚发过这份报告就别再查一遍（重复推消息不重复烧接口、不重复刷屏）
        if self._report_recently_sent(shop_id, from_uid, sn):
            self.logger.info(f"[发货] 报告刚发过，跳过重复查询 sn={sn}")
            await self._send(shop_id, user_id, from_uid, _REPORT_ALREADY_SENT_TEXT)
            return None, _SHIP_SUCCESS

        try:
            if device_type == "apple" and zhenxin_sid:
                from Channel.pinduoduo.utils.API.gsxunlocking_api import query_gsxunlocking

                zx = await asyncio.to_thread(query_gsxunlocking, sn, zhenxin_sid)
                if not zx.get("success"):
                    self.logger.warning(
                        f"[发货] 仅查询模式 GsxUnlocking 失败 sn={sn}: {zx.get('error_msg')}")
                    self._clear_order_sn(order_id)
                    return (
                        f"❌ 序列号/IMEI 有误：{zx.get('error_msg', '未知错误')}\n"
                        f"请核对后重新发我（拨号输入 *#06# 可查 IMEI）"
                    ), _SHIP_FAILED
                await self._query_gsxunlocking_and_send(
                    sn, goods_text, spec_text, zx.get("result"), order_id,
                    shop_id, user_id, from_uid, success_text)
            else:
                from Channel.pinduoduo.utils.API.gsx_api import query_gsx

                gsx = await asyncio.to_thread(query_gsx, sn, server_id)
                if not gsx.get("success"):
                    self.logger.warning(
                        f"[发货] 仅查询模式 GSX 失败 sn={sn}: {gsx.get('error_msg')}")
                    self._clear_order_sn(order_id)
                    return (
                        f"❌ 序列号/IMEI 有误：{gsx.get('error_msg', '未知错误')}\n"
                        f"请核对后重新发我（拨号输入 *#06# 可查 IMEI）"
                    ), _SHIP_FAILED
                await self._query_gsx_and_send(
                    sn, goods_text, spec_text, gsx.get("data"), order_id,
                    shop_id, user_id, from_uid, success_text)
            return None, _SHIP_SUCCESS
        except Exception as e:
            self.logger.opt(exception=True).error(f"[发货] 仅查询模式异常 sn={sn}: {e}")
            return f"❌ 查询报告失败：{e}\n请稍后重试或联系人工", _SHIP_ERROR

    async def _query_gsxunlocking_and_send(
        self, serial: str, goods_id: str, spec: str, report_data,
        order_id: int, shop_id: str, user_id: str, from_uid: str,
        voucher_success_text: str = ""
    ) -> None:
        """发送 GsxUnlocking 报告（苹果设备）
        发送顺序：验机报告 → AI分析 → 核销成功 → 五星好评
        """
        try:
            from database import db_manager
            from database.models import Order

            if not report_data:
                self.logger.warning(f"[核销] 无 GsxUnlocking 数据，跳过报告发送 sn={serial}")
                if voucher_success_text:
                    await self._send(shop_id, user_id, from_uid, voucher_success_text)
                return

            query_cfg = self._get_query_config(goods_id, spec)
            with db_manager.session_scope() as session:
                order = session.query(Order).filter_by(id=order_id).first()
                if order:
                    order.sku_id = query_cfg.get("zhenxin_server_id") or ""
                    order.gsx_result = str(report_data)

            # format=text 时 result 直接是纯文本字符串；万一是 dict/list，
            # 也按「键：值」排版，绝不把 Python 字面量原样发给客户
            report_text = (report_data if isinstance(report_data, str)
                           else _format_gsx_result(report_data))

            fixed = _FIXED_REVIEW_TEXT
            session_id = f"pinduoduo{from_uid}"

            # 1. 发送验机报告
            await self._send(shop_id, user_id, from_uid, report_text)
            self._mark_report_sent(shop_id, from_uid, serial)

            # 2. LLM 分析（复用 GSX 分析逻辑）
            analysis = await self._llm_analyze_gsx(report_text, session_id)
            if analysis:
                await self._send(shop_id, user_id, from_uid, analysis)

            # 3. 核销成功消息
            if voucher_success_text:
                await self._send(shop_id, user_id, from_uid, voucher_success_text)

            # 4. 固定结语
            await self._send(shop_id, user_id, from_uid, fixed)

        except Exception as e:
            self.logger.opt(exception=True).error(f"[核销] GsxUnlocking报告发送失败: {e}")

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

            fixed = _FIXED_REVIEW_TEXT
            session_id = f"pinduoduo{from_uid}"

            # 1. 发送验机报告原文
            await self._send(shop_id, user_id, from_uid, report_text)
            self._mark_report_sent(shop_id, from_uid, serial)

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

            self.logger.info(f"[核销] _query_gsx_and_send gsx_data={type(gsx_data).__name__} empty={not gsx_data}")

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
            self.logger.info(f"[核销] gsx_text 长度={len(gsx_text)} 内容预览={gsx_text[:80]!r}")
            fixed = _FIXED_REVIEW_TEXT
            session_id = f"pinduoduo{from_uid}"

            # 1. 发送 GSX 原始报告
            await self._send(shop_id, user_id, from_uid, gsx_text)
            self._mark_report_sent(shop_id, from_uid, serial)

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

    async def _llm_analyze_lingsu(self, report_text: str, session_id: str) -> str:
        """苹果凌速验机报告 LLM 分析"""
        try:
            from Agent.CustomerAgent.custom.llm_client import LLMClient
            from Agent.CustomerAgent.custom.agent_config import AgentConfig
            agent_config = AgentConfig.load_from_config()
            llm_client = LLMClient(
                model_name=agent_config.model_name,
                api_key=agent_config.api_key,
                api_base=agent_config.api_base,
                temperature=agent_config.temperature,
            )
            await llm_client.initialize()
            self.logger.info(f"[凌速分析] 使用 LLM ({llm_client.model_name})")

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
            self._record_message(session_id, "assistant", session_content)
            return analysis

        except Exception as e:
            self.logger.opt(exception=True).warning(f"[核销] LLM 分析凌速报告失败: {e}")
            return ""

    async def _llm_analyze_gsx(self, gsx_text: str, session_id: str) -> str:
        """用 LLM 分析 GSX 报告，并将结果写入会话历史"""
        try:
            from Agent.CustomerAgent.custom.llm_client import LLMClient
            from Agent.CustomerAgent.custom.agent_config import AgentConfig
            agent_config = AgentConfig.load_from_config()
            llm_client = LLMClient(
                model_name=agent_config.model_name,
                api_key=agent_config.api_key,
                api_base=agent_config.api_base,
                temperature=agent_config.temperature,
            )
            await llm_client.initialize()
            self.logger.info(f"[GSX分析] 使用 LLM ({llm_client.model_name})")

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
                f"验机���告：\n{gsx_text}"
            )

            response = await llm_client.chat(
                messages=[{"role": "user", "content": prompt}],
                tool_choice="none",
            )
            analysis = (response.content or "").strip()

            session_content = f"[已完成 GSX 验机查询]\n验机报告：\n{gsx_text}"
            if analysis:
                self.logger.info(f"[核销] GSX分析完成，长度={len(analysis)}")
                session_content += f"\n\n分析：\n{analysis}"
            self._record_message(session_id, "assistant", session_content)
            return analysis

        except Exception as e:
            self.logger.opt(exception=True).warning(f"[核销] LLM 分析 GSX 报告失败: {e}")
            return ""
