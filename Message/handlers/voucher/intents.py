"""
客户意图判定：确认 / 重发报告 / 问自己的订单。
"""
import re

# 序列号/IMEI 必须客户确认后才查验机（文本打的和截图识别的一视同仁），
# 这里记住「等确认的那一条」和「已经确认过的序列号」。
_SN_CONFIRM_WORDS = frozenset({
    "确认", "确定", "正确", "没错", "对", "对的", "是", "是的", "嗯", "可以", "好", "好的",
    "ok", "okay", "yes",
})

def _is_confirm_text(text: str) -> bool:
    """是否是一条「就确认这一件事」的短回复（整句匹配，避免把长句误判成确认）"""
    cleaned = re.sub(r"[\s。.!！~～,，、:：;；\"'“”‘’]+", "", str(text or "")).lower()
    return cleaned in _SN_CONFIRM_WORDS

_RESEND_WORDS = frozenset({
    "重发", "再发", "再发一次", "没收到", "没看到", "重发一次",
    "报告", "查报告", "看报告", "要报告", "发报告", "验机报告",
})

def _is_resend_text(text: str) -> bool:
    """客户是不是要求把报告再发一次"""
    cleaned = re.sub(r"[\s。.!！~～,，、:：;；\"'“”‘’]+", "", str(text or ""))
    return cleaned in _RESEND_WORDS

# 客户在问「我这笔单怎么样了 / 待发货 / 查一下我的订单」：这类必须去后台查，不能让 AI 瞎答
_ORDER_ASK_WORDS = (
    "我的订单", "订单呢", "待发货", "没发货", "是否发货", "发货了吗", "还没发",
    "查订单", "订单状态", "我的单", "哪一单", "哪笔单", "我买的", "我拍的",
)

def _is_order_ask_text(text: str) -> bool:
    """客户是不是在问自己的订单/待发货：这类必须去后台查，不能让 AI 瞎答"""
    raw = str(text or "")
    if not raw or len(raw) > 40:
        return False
    return any(word in raw for word in _ORDER_ASK_WORDS)
