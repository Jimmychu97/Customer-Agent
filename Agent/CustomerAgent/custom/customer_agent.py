"""
自定义 CustomerAgent 实现

完全自主实现，不依赖 Agno 框架。

本模块已重构，职责分离为：
- agent_config.py: 配置管理
- llm_client.py: LLM 客户端封装
- message_builder.py: 消息和 Prompt 构建
- tool_executor.py: 工具执行器
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from Agent.bot import Bot

# 导入工具模块，触发 @agent_tool 装饰器注册
from Agent.CustomerAgent.tools import (
    send_goods_link,                  # noqa: F401  — 注册 send_goods_link 工具
    move_conversation,                 # noqa: F401  — 注册 transfer_conversation 工具
    get_product_list,                 # noqa: F401  — 注册 get_shop_products 工具
    get_product_knowledge,             # noqa: F401  — 注册 get_product_knowledge 工具
    search_customer_service_knowledge,  # noqa: F401  — 注册 search_customer_service_knowledge 工具
    verify_voucher,                   # noqa: F401  — 注册 verify_voucher 工具
    use_voucher,                      # noqa: F401  — 注册 use_voucher 工具
    coupon_query,                     # noqa: F401  — 注册 get_coupon_list / get_voucher_stats 工具
    get_voucher_code,                 # noqa: F401  — 注册 get_voucher_code 工具
)
from bridge.context import Context
from bridge.reply import Reply, ReplyType
from Agent.CustomerAgent.custom.session_manager import SessionManager
from Agent.CustomerAgent.custom.tool_decorator import get_tools_for_llm
from utils.logger_loguru import get_logger

# 导入重构后的模块
from Agent.CustomerAgent.custom.agent_config import (
    AgentConfig,
    DEFAULT_DB_PATH,
    DEFAULT_TOKEN_WINDOW,
    DEFAULT_COMPRESS_RATIO,
    DEFAULT_RETAIN_COUNT,
    DEFAULT_MAX_LOOPS,
    DEFAULT_TEMPERATURE,
)
from Agent.CustomerAgent.custom.llm_client import LLMClient, LLMResponse
from Agent.CustomerAgent.custom.message_builder import MessageBuilder
from Agent.CustomerAgent.custom.tool_executor import ToolExecutor, ToolResult

logger = get_logger("CustomerAgent")


class CustomerAgent(Bot):
    """
    自定义客服 Agent

    核心循环：
    1. 加载历史消息
    2. 检查上下文压缩
    3. 构建 messages 列表
    4. 调用 LLM → 解析 tool_calls
    5. 并行执行工具 → 回传结果
    6. 循环直到无工具调用
    7. 返回最终回复

    职责已分离到子模块：
    - AgentConfig: 配置管理
    - LLMClient: LLM API 调用
    - MessageBuilder: 消息和 Prompt 构建
    - ToolExecutor: 工具执行
    - SessionManager: 会话管理（已有独立模块）
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        token_window: int = DEFAULT_TOKEN_WINDOW,
        compress_ratio: float = DEFAULT_COMPRESS_RATIO,
        retain_count: int = DEFAULT_RETAIN_COUNT,
        max_loops: int = DEFAULT_MAX_LOOPS,
        temperature: float = DEFAULT_TEMPERATURE,
    ):
        super().__init__()
        self._is_initialized = False

        # 配置参数
        self._config = AgentConfig(
            db_path=db_path or DEFAULT_DB_PATH,
            token_window=token_window,
            compress_ratio=compress_ratio,
            retain_count=retain_count,
            max_loops=max_loops,
            temperature=temperature,
        )

        # 子组件（延迟初始化）
        self._llm_client: Optional[LLMClient] = None  # 文本对话客户端
        self._vision_client: Optional[LLMClient] = None  # 图片识别客户端
        self._message_builder: Optional[MessageBuilder] = None
        self._tool_executor: Optional[ToolExecutor] = None
        self._session_manager: Optional[SessionManager] = None
        self._tools: List[Dict[str, Any]] = []

        logger.info("CustomerAgent 实例创建成功")

    async def initialize_async(self) -> bool:
        """异步初始化 Agent"""
        if self._is_initialized:
            return True

        try:
            # 1. 从配置文件加载配置
            self._config = AgentConfig.load_from_config()

            # 2. 验证配置
            if not self._config.validate():
                return False

            # 3. 初始化文本对话 LLM 客户端
            self._llm_client = LLMClient(
                api_key=self._config.api_key,
                api_base=self._config.api_base,
                model_name=self._config.model_name,
                temperature=self._config.temperature,
            )
            await self._llm_client.initialize()

            # 4. 初始化图片识别 LLM 客户端（如果配置了）
            if self._config.vision_api_key and self._config.vision_model_name:
                self._vision_client = LLMClient(
                    api_key=self._config.vision_api_key,
                    api_base=self._config.vision_api_base,
                    model_name=self._config.vision_model_name,
                    temperature=0.1,  # 图片识别使用较低温度
                )
                await self._vision_client.initialize()
                logger.info(f"视觉LLM客户端初始化成功: model={self._config.vision_model_name}")
            else:
                self._vision_client = None

            # 5. 初始化会话管理器
            self._session_manager = SessionManager(
                db_path=self._config.db_path,
                token_window=self._config.token_window,
                compress_ratio=self._config.compress_ratio,
                retain_count=self._config.retain_count,
                model_name=self._config.model_name,
            )

            # 6. 初始化消息构建器
            self._message_builder = MessageBuilder(
                instructions=self._config.instructions,
            )

            # 7. 初始化工具执行器
            self._tool_executor = ToolExecutor()

            # 8. 加载工具列表
            self._tools = get_tools_for_llm()
            self._llm_client.tools = self._tools
            tool_names = [t.get("function", {}).get("name", "unknown") for t in self._tools]
            logger.info(f"已加载 {len(self._tools)} 个工具: {tool_names}")

            self._is_initialized = True
            logger.info(f"CustomerAgent 初始化成功")
            return True

        except Exception as e:
            logger.error(f"CustomerAgent 初始化失败: {e}")
            return False

    async def async_reply(self, query: str, context: Context = None) -> Reply:
        """
        异步回复接口

        自动判断消息类型：
        - 图片消息 -> 使用视觉LLM（如已配置）
        - 文本消息 -> 使用文本LLM
        """
        # 延迟初始化
        if not self._is_initialized:
            if not await self.initialize_async():
                return Reply(ReplyType.TEXT, "AI客服初始化失败，请检查配置。")

        try:
            # 检测是否为图片消息
            is_image, image_url = self._detect_image_message(query)

            # 如果是图片消息且有视觉模型，使用视觉LLM
            if is_image and image_url and self._vision_client:
                logger.info(f"[对话] 检测到图片消息，使用视觉LLM ({self._vision_client.model_name})")
                recognized_text = await self.recognize_image(image_url, context)
                if recognized_text:
                    # 将识别结果作为用户消息继续处理
                    query = f"[图片识别结果]\n{recognized_text}"
                else:
                    return Reply(ReplyType.TEXT, "抱歉，图片识别失败，请稍后再试。")
            else:
                logger.info(f"[对话] 文本对话，使用文本LLM ({self._llm_client.model_name})")

            # 构建 session_id 和 dependencies
            from_uid = getattr(context.kwargs, "from_uid", None) if context else None
            if context and context.channel_type and from_uid:
                session_id = f"{context.channel_type.value}{from_uid}"
                dependencies = self._message_builder.build_dependencies(context)
            else:
                # 降级：使用 query hash 作为 session_id
                session_id = f"fallback_{abs(hash(query)) % 100000}"
                dependencies = {}

            # 加载历史并检查压缩
            history = self._session_manager.get_history(session_id)
            if not history and context:
                # 会话历史为空时，回退读取主数据库中的消息记录，保证 AI 不会丢失客户说过的话
                history = self._load_history_from_records(context)
            if self._session_manager.should_compress(session_id):
                logger.info(f"触发上下文压缩: session_id={session_id}")
                await self._compress_with_llm(session_id, history)
                history = self._session_manager.get_history(session_id)  # 压缩后重新拉取

            # 保存用户消息到历史
            self._session_manager.add_message(
                session_id=session_id,
                role="user",
                content=query,
            )

            # 构建 messages
            messages = self._message_builder.build_messages(query, history, dependencies)

            # 执行 Agent 循环（使用文本LLM）
            final_content = await self._run_agent_loop(messages, dependencies)

            # 保存最终回复到历史
            self._session_manager.add_message(
                session_id=session_id,
                role="assistant",
                content=final_content,
            )

            return Reply(ReplyType.TEXT, final_content or "抱歉，我暂时无法回复。")

        except Exception as e:
            logger.error(f"CustomerAgent 回复失败: {e}")
            return Reply(ReplyType.TEXT, "抱歉，我现在无法回复，请稍后再试。")

    def _detect_image_message(self, query: str) -> tuple[bool, Optional[str]]:
        """
        检测消息是否包含图片

        Args:
            query: 用户消息内容

        Returns:
            (是否为图片消息, 图片URL)
        """
        if not query:
            return False, None

        try:
            import json
            # 尝试解析 JSON 格式
            data = json.loads(query) if isinstance(query, str) else query
            if isinstance(data, dict):
                url = data.get("url") or data.get("thumb_url") or data.get("image_url")
                if url and isinstance(url, str):
                    return True, url
            # 检查是否直接是 URL
            if isinstance(query, str) and query.startswith("http") and any(
                ext in query.lower() for ext in [".jpg", ".jpeg", ".png", ".gif", ".webp"]
            ):
                return True, query
        except Exception:
            pass

        return False, None

    def _load_history_from_records(self, context: Context) -> List[Dict[str, Any]]:
        """从主数据库 message_records 表回退加载会话历史

        仅当 agent_messages 中无历史时调用，保证切换数据库/首次运行等场景下，
        AI 仍能知道客户之前说过什么。
        """
        try:
            from database import db_manager
            kwargs = context.kwargs
            shop_id = getattr(kwargs, "shop_id", None)
            from_uid = getattr(kwargs, "from_uid", None)
            if not shop_id or not from_uid:
                return []

            records = db_manager.get_recent_messages(str(shop_id), str(from_uid), limit=20)
            # 当前这条用户消息已经先落库，回退历史时应排除，避免与下面追加的 query 重复
            if records and records[-1].get("direction") == "in":
                records = records[:-1]

            history = []
            for r in records:
                content = r.get("content") or ""
                if not content:
                    continue
                role = "user" if r.get("direction") == "in" else "assistant"
                history.append({"role": role, "content": content, "tool_call_id": None})
            if history:
                logger.info(f"[对话] 已从主数据库回退加载历史 {len(history)} 条: session={shop_id}:{from_uid}")
            return history
        except Exception as e:
            logger.warning(f"[对话] 从主数据库回退加载历史失败: {e}")
            return []

    async def _run_agent_loop(
        self,
        messages: List[Dict[str, Any]],
        dependencies: Dict[str, Any],
    ) -> str:
        """
        Agent 循环核心

        调用 LLM → 检查 tool_calls → 并行执行工具 → 回传结果 → 循环
        """
        loop_count = 0

        while loop_count < self._config.max_loops:
            # 1. 调用 LLM
            try:
                response = await self._llm_client.chat(messages, tool_choice="auto")
            except Exception as e:
                logger.error(f"LLM 调用失败: {e}")
                if loop_count == 0:
                    return f"抱歉，AI 服务暂时不可用：{e}"
                # 已有中间结果，返回已生成的内容
                for msg in reversed(messages):
                    if msg.get("role") == "assistant" and msg.get("content"):
                        return msg["content"]
                return f"抱歉，AI 服务暂时不可用：{e}"

            # 2. 解析响应
            if not response.has_tool_calls:
                # 无工具调用，返回内容
                content = response.content or ""
                messages.append({"role": "assistant", "content": content})
                return content

            # 3. 保存 assistant 消息（包含 tool_calls）
            assistant_msg = {
                "role": "assistant",
                "content": response.content or "",
                "tool_calls": [
                    {
                        "type": "function",
                        "id": tc.id,
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in response.tool_calls
                ],
            }
            messages.append(assistant_msg)

            # 4. 检查循环上限
            if loop_count >= self._config.max_loops - 1:
                logger.warning(f"工具调用达到上限 {self._config.max_loops}，强制结束循环")
                messages.append({
                    "role": "user",
                    "content": "[已达到最大工具调用次数，请基于已有信息给出最终回复。]",
                })
                try:
                    final_response = await self._llm_client.chat(messages)
                    return final_response.content or assistant_msg["content"]
                except Exception:
                    return assistant_msg["content"]

            # 5. 并行执行所有工具调用
            tool_results = await self._tool_executor.execute_parallel(
                response.tool_calls, dependencies
            )

            # 6. 将结果追加到消息列表
            for result in tool_results:
                messages.append(result.to_dict())

            loop_count += 1

        # 兜底
        return messages[-1].get("content", "")

    async def recognize_image(self, image_url: str, context: Context = None) -> Optional[str]:
        """
        专用图片识别接口：不走 Agent 工具循环，不写会话历史

        Args:
            image_url: 图片 URL
            context: 上下文（用于获取 shop_id 等信息）

        Returns:
            LLM 识别出的文字内容，失败返回 None
        """
        if not self._is_initialized:
            if not await self.initialize_async():
                return None

        try:
            # 优先使用视觉模型，未配置则使用文本模型
            client = self._vision_client or self._llm_client
            model_type = "视觉LLM" if self._vision_client else "文本LLM"
            logger.info(f"[图片识别] 使用 {model_type} ({client.model_name})")

            messages = [
                {
                    "role": "system",
                    "content": (
                        "你是一个图片内容识别助手。请识别图片中的所有文字和关键信息，"
                        "重点提取：IMEI码、序列号、卡券号、订单号等数字编码。"
                        "直接输出识别到的内容，不要添加额外解释。"
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": image_url}},
                        {"type": "text", "text": "请识别图片中的所有文字内容，特别是数字编码。"},
                    ],
                },
            ]

            response = await client.chat(messages, tool_choice="none")
            content = (response.content or "").strip() or None
            logger.info(f"[图片识别] 识别成功，内容长度: {len(content) if content else 0}")
            return content

        except Exception as e:
            logger.error(f"[图片识别] LLM 调用失败: {e}")
            return None

    async def _compress_with_llm(
        self,
        session_id: str,
        history: List[Dict[str, Any]],
    ) -> None:
        """使用 LLM 生成摘要并压缩历史"""

        def summary_llm(messages: List[Dict[str, Any]]) -> str:
            """同步调用 LLM 生成摘要"""
            summary_prompt = (
                "请简洁地总结以下对话的要点，保留关键信息和用户意图。\n\n"
                f"对话内容（共 {len(messages)} 条消息）：\n"
                + "\n".join(
                    f"[{msg.get('role', 'unknown')}]: {msg.get('content', '')[:200]}"
                    for msg in messages
                    if msg.get("content")
                )
            )

            # 在线程中使用 asyncio.run 创建新的事件循环执行协程
            try:
                response = asyncio.run(
                    self._llm_client.chat(
                        messages=[
                            {"role": "system", "content": "你是一个对话摘要助手。请简洁地总结对话要点。"},
                            {"role": "user", "content": summary_prompt},
                        ],
                        tool_choice="none",
                    )
                )
                return response.content or "[摘要生成失败]"
            except RuntimeError:
                # 如果当前已有事件循环（某些环境中），降级为同步返回
                return "[对话历史摘要]"
            except Exception:
                return "[摘要生成失败]"

        self._session_manager.compress_history(session_id, summary_llm)
