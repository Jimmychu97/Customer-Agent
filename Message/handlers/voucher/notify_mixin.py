"""给客户发消息 + 落库（会话历史、消息记录）。"""
import asyncio


class NotifyMixin:
    async def _send(self, shop_id: str, user_id: str, from_uid: str, text: str) -> bool:
        try:
            from Channel.pinduoduo.utils.API.send_message import SendMessage
            result = await asyncio.to_thread(
                lambda: SendMessage(shop_id, user_id).send_text(from_uid, text)
            )
            success = isinstance(result, dict) and result.get("success", False)
            if success:
                self.logger.info(f"[核销] 发送成功 from_uid={from_uid} text={text[:30]!r}")
                self._record_outgoing(shop_id, user_id, from_uid, text)
            else:
                self.logger.warning(f"[核销] 发送失败 from_uid={from_uid} result={result}")
            return success
        except Exception as e:
            self.logger.opt(exception=True).error(f"[核销] 发送回复失败 from_uid={from_uid}: {e}")
            return False

    def _record_message(self, session_id: str, role: str, content: str) -> None:
        """将消息写入 agent_messages 会话历史

        source 标记来源：客户消息记 customer，核销流程发出的话记 voucher，
        便于在库里区分"人工客服"与"程序/AI 发出"的内容。
        """
        try:
            from Agent.CustomerAgent.custom.agent_config import AgentConfig
            from Agent.CustomerAgent.custom.session_manager import SessionManager
            agent_config = AgentConfig.load_from_config()
            SessionManager(
                db_path=agent_config.db_path,
                token_window=agent_config.token_window,
                compress_ratio=agent_config.compress_ratio,
                retain_count=agent_config.retain_count,
                model_name=agent_config.model_name,
            ).add_message(
                session_id=session_id,
                role=role,
                content=content,
                source="customer" if role == "user" else "voucher",
            )
        except Exception as e:
            self.logger.warning(f"[核销] 写入会话历史失败: {e}")

    def _record_outgoing(self, shop_id: str, user_id: str, from_uid: str, text: str) -> None:
        """将核销等场景下客服发送的回复写入消息记录表"""
        try:
            from Message.core.persistence import save_outgoing_raw
            save_outgoing_raw(shop_id=shop_id, user_id=user_id, from_uid=from_uid, content=text)
        except Exception as e:
            self.logger.warning(f"[核销] 记录客服回复失败 from_uid={from_uid}: {e}")

    async def _send_updates(self, updates, shop_id: str, user_id: str, from_uid: str,
                            session_id: str) -> None:
        """把"已更新您的序列号/IMEI"等变更提示发给客户"""
        if not updates:
            return
        note = "\n".join(updates)
        await self._send(shop_id, user_id, from_uid, note)
        self._record_message(session_id, "assistant", note)
