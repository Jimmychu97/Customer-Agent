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

# 验机报告关键词，用于检测历史中是否已有报告
_REPORT_KEYWORDS = ("验机报告", "GSX", "凌速", "[已完成 GSX 验机查询]", "[已完成凌速验机查询]")


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
        self.system_prompt = self._compose_system_prompt(self.instructions)

    def _compose_system_prompt(self, instructions: List[str]) -> str:
        """按给定指令列表拼装系统 Prompt（仅发货模式下会传入过滤后的指令）"""
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

        if instructions:
            parts.append("---\n" + "\n".join(f"- {i}" for i in instructions))

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
- 【重要例外】：如果对话历史中已有验机报告（assistant消息含"验机报告"、"GSX"、"凌速"等内容），用户针对该报告提问时（如问电池健康、激活日期、保修、锁机状态等），直接基于报告内容回答，禁止调用任何工具！
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

        return "\n\n".join(parts) if parts else "你是一个电商客服。"

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

    @staticmethod
    def _find_report_in_history(history: List[Dict[str, Any]]) -> Optional[str]:
        """
        检测历史消息中是否已有验机报告

        Args:
            history: 历史消息列表

        Returns:
            报告内容字符串，未找到返回 None
        """
        for msg in reversed(history):
            if msg.get("role") != "assistant":
                continue
            content = msg.get("content") or ""
            if isinstance(content, list):
                content = " ".join(
                    part.get("text", "") for part in content if isinstance(part, dict)
                )
            if any(kw in content for kw in _REPORT_KEYWORDS):
                return content
        return None

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

        # 服务模式由「客户下单的商品」决定，不再默认索要卡券：
        # 只有商品确实配置成卡券核销(0)时才保留带"卡券"的指令（如"回复结尾必须加上…
        # 商品卡券…"）；仅发货(1)、二选一兼容(2)、未配置(None) 一律去掉并追加对应规则
        service_mode = dependencies.get("service_mode") if dependencies else None
        coupon_mode = service_mode == 0
        ship_mode = service_mode == 1
        unknown_mode = service_mode is None
        system_prompt = self.system_prompt
        if not coupon_mode:
            system_prompt = self._compose_system_prompt(
                [i for i in self.instructions if "卡券" not in i]
            )

        # System prompt（占位符替换）
        if system_prompt:
            content = system_prompt
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
                session_info += (
                    f"- 服务模式: {service_mode if service_mode is not None else '未确定'}"
                    f"（依据 {dependencies.get('service_mode_evidence', 'none')}）\n"
                )
                session_info += "\n【重要】调用工具时，shop_id、user_id 等参数必须使用上面【当前会话信息】中给出的值！"
                if ship_mode:
                    session_info += (
                        "\n\n【服务模式：仅发货】该商品拍下后只需「序列号/IMEI」即可完成发货与查询，"
                        "不要向客户索要商品卡券/卡券号，也不要引导客户去「待收货-查看卡券」。"
                    )
                elif service_mode == 2:
                    session_info += (
                        "\n\n【服务模式：二选一兼容】不要主动索要卡券：客户给卡券号就走核销，"
                        "只给序列号/IMEI 或拼多多订单号就走发货。"
                    )
                elif unknown_mode:
                    session_info += (
                        "\n\n【服务模式：未确定】严禁主动提「卡券」「卡券号」「查看卡券」，"
                        "也不要让客户在两种服务之间选；按二选一回答："
                        "客户给的是卡券号就按核销流程答，给的是序列号/IMEI 就按发货流程答，"
                        "再按下面「客户待发货订单」核对订单。"
                    )

                # 客户待发货订单：让 AI 自己知道这单是什么，别让客户发订单卡片/抄订单号
                order_hint = str(dependencies.get("pending_order_hint") or "").strip()
                if order_hint:
                    session_info += (
                        f"\n\n【客户待发货订单】{order_hint}。"
                        "客户问订单/发货/什么时候发时，直接用这条回答并请他发序列号/IMEI；"
                        "严禁再让他发订单卡片或抄订单号。"
                    )
                elif ship_mode or unknown_mode:
                    session_info += (
                        "\n\n【客户待发货订单】后台暂时没查到（可能刚下单）。"
                        "若客户问订单，让他点聊天框右下角「+」→ 选择「订单」→ 发送订单卡片；"
                        "不要让他抄订单号。"
                    )
                content += session_info

            messages.append({"role": "system", "content": content})

        # 检测历史中是否有验机报告，有则注入提示让 AI 优先基于报告回答
        report_content = self._find_report_in_history(history)
        if report_content:
            hint = (
                "【验机报告提示】对话历史中已包含该用户的验机报告。"
                "用户后续关于该设备的任何问题（电池、激活、保修、锁机、维修记录等），"
                "必须直接基于报告内容回答，禁止调用工具查询！"
            )
            messages.append({"role": "system", "content": hint})
            logger.debug("[MessageBuilder] 检测到历史验机报告，已注入提示")

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
