"""
消息构建器模块

负责构建系统 Prompt 和 LLM 消息列表。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from bridge.context import Context
from utils.logger_loguru import get_logger
from Agent.CustomerAgent.tools.get_product_list import (
    get_shop_products,
    GetShopProductsParams,
)

logger = get_logger("MessageBuilder")


class MessageBuilder:
    """消息构建器"""

    def __init__(
        self,
        instructions: Optional[List[str]] = None,
    ):
        """
        初始化消息构建器

        Args:
            instructions: 指令列表
        """
        self.instructions = instructions or []
        self.system_prompt = ""

        self._build_system_prompt()

    def _build_system_prompt(self) -> None:
        """构建系统 Prompt"""
        parts = []

        # 硬编码的角色描述（亲切活泼风格）
        description = """你好呀！👋 我是{shop_name}的客服小助手～

当前店铺在售商品列表：{product_list}
 
我的工作风格：
😊 热情亲切，每句都用emoji
💬 统一称呼用户"亲"
✨ 回复不超过50字
🧐 先了解需求再推荐商品
"""
        parts.append(description)

        if self.instructions:
            parts.append("---\n" + "\n".join(f"- {i}" for i in self.instructions))

        # 硬编码的额外上下文（工具介绍+示例）
        additional_context = """---
📦 工具使用说明：

1️⃣ get_product_knowledge（获取商品知识）
- 用途：查商品成分、用法、价格、规格等
- 参数：goods_id（商品ID）、shop_id（店铺ID）
- 示例：用户问"这款面霜含什么成分"→调用此工具

2️⃣ search_customer_service_knowledge（搜索客服知识）
- 用途：查任何客服知识，包括售前、售后、商品功能、使用方法、电池、物流、退换货等一切问题
- 参数：query（关键词）、shop_id（店铺ID）
- 【强制规则】：用户发送任何文字问题，必须第一个调用此工具，不得跳过！
- 示例：用户问"电池能查吗""可以退货吗""怎么用"→均必须先调用此工具

3️⃣ send_goods_link（发送商品卡片）
- 用途：主动向用户发送商品购买链接，促成下单
- 参数：recipient_uid、goods_id、shop_id、user_id
- 【必须主动调用的场景】：
  · 用户询问某商品详情、价格、功效后 → 回答完毕后立即调用发送该商品卡片
  · 用户表示想买、想下单、想了解购买方式 → 立即调用发送对应商品卡片
  · 用户问有什么推荐、有什么产品 → 调用发送最相关的商品卡片
  · 解答完用户疑问且问题与某商品相关 → 主动发送该商品卡片引导下单
- 重要：发卡片无需等用户主动要求，只要对话与某商品相关，就应主动发送！

4️⃣ transfer_conversation（转接人工）
- 用途：用户要求转人工或按指令执行
- 参数：shop_id、user_id、recipient_uid
- 示例：用户说"转人工"→调用此工具
 
"""
        parts.append(additional_context)

        self.system_prompt = "\n\n".join(parts) if parts else "你是一个电商客服。"

    def build_dependencies(self, context: Context) -> Dict[str, Any]:
        """
        从 Context 构建 dependencies 字典

        Args:
            context: 上下文对象

        Returns:
            dependencies 字典
        """
        kwargs = context.kwargs
        from_uid = str(kwargs.from_uid or "")

        # shop_id 保持整数类型，便于工具参数注入
        shop_id = kwargs.shop_id if kwargs.shop_id else 0
        if isinstance(shop_id, str) and shop_id.isdigit():
            shop_id = int(shop_id)

        return {
            "shop_name": str(kwargs.shop_name or ""),
            "channel_type": str(context.channel_type.value if context.channel_type else ""),
            "shop_id": shop_id,
            "user_id": str(kwargs.user_id or ""),
            "from_uid": from_uid,
            "recipient_uid": from_uid,  # 工具参数通常叫 recipient_uid，兼容两种命名
        }

    def _inject_product_list(self, dependencies: Dict[str, Any]) -> None:
        """
        动态获取商品列表并注入到 dependencies

        Args:
            dependencies: 依赖字典（会被修改）
        """
        if not dependencies.get("shop_id") or not dependencies.get("user_id"):
            return

        try:
            params = GetShopProductsParams(
                shop_id=dependencies["shop_id"],
                user_id=dependencies["user_id"]
            )
            product_list_text = get_shop_products(params)
            # 添加说明：仅展示第一页商品
            product_list_text += "\n注：以上仅展示第一页商品，如果用户需要查看更多商品，请调用 get_shop_products 工具获取更多。"
            dependencies["product_list"] = product_list_text
        except Exception as e:
            logger.warning(f"动态获取商品列表失败: {e}")
            dependencies["product_list"] = "获取商品列表失败"

    def build_messages(
        self,
        query: str,
        history: List[Dict[str, Any]],
        dependencies: Dict[str, Any] = None,
    ) -> List[Dict[str, Any]]:
        """
        构建 LLM 消息列表

        Args:
            query: 用户查询
            history: 历史消息
            dependencies: 依赖字典（用于占位符替换）

        Returns:
            LLM 消息列表
        """
        messages = []

        # System prompt（占位符替换）
        if self.system_prompt:
            content = self.system_prompt
            if dependencies:
                # 动态获取商品列表并注入到 dependencies
                self._inject_product_list(dependencies)

                for key, value in dependencies.items():
                    content = content.replace(f"{{{key}}}", str(value))

                # 动态构建会话信息，告诉 LLM 各字段的值
                session_info = "\n\n【当前会话信息】\n"
                session_info += f"- shop_id: {dependencies.get('shop_id', '')}（店铺ID，调用工具时必须使用此值）\n"
                session_info += f"- user_id: {dependencies.get('user_id', '')}（账号ID，调用工具时必须使用此值）\n"
                session_info += f"- recipient_uid: {dependencies.get('recipient_uid', '')}（接收消息的用户UID，发送商品卡片时使用）\n"
                session_info += f"- shop_name: {dependencies.get('shop_name', '')}（店铺名称）\n"
                session_info += f"- channel_type: {dependencies.get('channel_type', '')}（渠道类型）\n"
                session_info += "\n【重要】调用工具时，shop_id、user_id 等参数必须使用上面【当前会话信息】中给出的值！"
                content += session_info

            messages.append({"role": "system", "content": content})

        # 历史消息
        for msg in history:
            role = msg["role"]
            content = msg["content"]
            tool_call_id = msg.get("tool_call_id")

            # 确保 content 是字符串或合法的多模态列表
            if content is not None and not isinstance(content, (str, list)):
                import json
                try:
                    content = json.dumps(content, ensure_ascii=False)
                except Exception:
                    content = str(content)

            if role == "tool":
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": content,
                })
            elif role == "system":
                # system 消息（摘要等）直接追加
                messages.append({"role": "system", "content": content})
            else:
                messages.append({"role": role, "content": content})

        # 当前用户消息
        messages.append({"role": "user", "content": query})
        return messages
