"""对客文案拼装（确认提示 / 订单行）。"""
from typing import Any
from typing import Dict


class TextsMixin:
    @staticmethod
    def _serial_confirm_text(sn: str, prefix: str = "") -> str:
        """「请核对序列号/IMEI」话术：客户回「确认」才去查验机"""
        head = (prefix.rstrip() + "\n\n") if prefix else ""
        return (
            head
            + "请核对这台机器的序列号/IMEI：\n"
            f"{sn}\n\n"
            "✅ 正确 → 回复「确认」，我马上出验机报告\n"
            "✏️ 不对 → 把正确的序列号/IMEI 发我（设置 → 通用 → 关于本机 可复制）"
        )

    @staticmethod
    def _ship_spec_needed_text(order_sn: str, spec_count: int) -> str:
        """验机商品但没拿到规格时的话术：绝不先发货（发了就再也补不出报告）"""
        head = (f"📦 订单 {order_sn}\n" if order_sn else "")
        what = (f"这个商品有 {spec_count} 个套餐（规格），" if spec_count > 1
                else "这个商品的查询项目还没对上，")
        return (
            head
            + "⚠️ 还没核对清楚要查哪个项目，我先没给您发货（发错不可撤回）\n\n"
            + what
            + "报告字段和价格是对应的，不能猜。\n"
              "麻烦把【订单卡片】发我（点击聊天框右下角「+」→ 选择「订单」→ 发送订单卡片，卡片里带套餐名），"
              "或直接回复您买的套餐名，我核对后马上发货并出验机报告～"
        )

    @staticmethod
    def _serial_confirm_reminder_text(sn: str, order_sn: str = "") -> str:
        """客户没明确回「确认」时的提醒：不允许静默把待确认丢掉（线上就是这样干等的）"""
        line = f"订单号：{order_sn}\n" if order_sn else ""
        return (
            "⏳ 我这边还在等您确认这一条：\n"
            + (f"序列号/IMEI：{sn}\n" if sn else "")
            + line
            + "\n✅ 没问题 → 回复「确认」，我马上出验机报告\n"
              "✏️ 不对 → 把正确的序列号/IMEI 发我（设置 → 通用 → 关于本机 可复制）"
        )

    @staticmethod
    def _ship_confirm_text(sn: str, order_sn: str, goods_name: str = "", spec: str = "",
                           newest: bool = False, assumed: bool = False) -> str:
        project = spec or "（没识别到查询项目，请把订单卡片发我核对）"
        if assumed:
            newest_note = "（店铺查到您刚拍下一单；如果这单不是您的，请把订单卡片发我核对）\n"
        elif newest:
            newest_note = ("（您名下有多笔，这次按【最新一单】处理；要查更早的单，把订单号发我）\n")
        else:
            newest_note = ""
        return (
            "📦 请核对本次查询信息：\n"
            + newest_note
            + f"序列号/IMEI：{sn}\n"
            f"订单号：{order_sn}\n"
            f"查询项目：{project}\n"
            + (f"商品：{goods_name}\n" if goods_name else "")
            + "\n✅ 正确 → 回复「确认」，我马上为您发货并查询出报告\n"
              "✏️ 序列号不对 → 把正确的序列号/IMEI 发我\n"
              "📄 之前查过的报告要看 → 回复「重发」"
        )

    @staticmethod
    def _order_line(index: int, order: Dict[str, Any]) -> str:
        """「1. 订单号 2610…（商品 / 规格）」这种列表行"""
        desc = " / ".join(
            x for x in (str(order.get("goods_name") or ""), str(order.get("spec") or "")) if x)
        return f"{index}. 订单号 {order.get('order_sn') or ''}" + (f"（{desc}）" if desc else "")
