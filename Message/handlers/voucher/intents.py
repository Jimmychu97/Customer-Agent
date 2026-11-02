"""
客户意图判定：确认 / 重发报告 / 问自己的订单。
"""
import re

# 序列号/IMEI 必须客户确认后才查验机（文本打的和截图识别的一视同仁），
# 这里记住「等确认的那一条」和「已经确认过的序列号」。

# 客户说的是人话：「确认了」「确认一下」「对的没错」「是这台机器」。
# 以前是整句白名单匹配，这些全被判成「不是确认」，于是「等确认 → 查询 → 反馈」
# 整条链路断掉：不查、也不回（线上 16:28 客户连问三次都没人出报告）。
_CONFIRM_CORE_WORDS = (
    "确认", "确定", "正确", "没错", "对的", "是这台", "是这机器", "是它", "就这台",
)

# 整句只有这些短词时也算确认（必须整句匹配，避免「好了吗」被当成确认）
_CONFIRM_SHORT_WORDS = frozenset({
    "对", "是", "是的", "嗯", "嗯嗯", "可以", "可以了", "好了", "好", "好的", "行",
    "没问题", "ok", "okay", "yes",
})

# 出现这些词一律不算确认：否定 / 催问 / 要求重查
_CONFIRM_DENY_WORDS = (
    "不", "错", "别", "取消", "等一下", "等等", "稍等", "再查", "重查", "重新", "查一下",
    "没有", "无效", "怎么", "什么", "?", "？",
)

_CONFIRM_MAX_LEN = 12

# 兼容旧引用（原来叫 _SN_CONFIRM_WORDS）
_SN_CONFIRM_WORDS = _CONFIRM_SHORT_WORDS


def _clean_text(text: str) -> str:
    """去掉空白与中英文标点，只留正文（？留作否定/催问标记，不删）"""
    return re.sub(r"[\s。.!！~～,，、:：;；\"'“”‘’]+", "", str(text or "")).lower()


def _is_confirm_text(text: str) -> bool:
    """是否是一条「就确认这一件事」的短回复

    规则：长度 ≤ 12 字 + 不含否定/催问词 + 含确认核心词（或整句就是短确认词）。
    """
    cleaned = _clean_text(text)
    if not cleaned or len(cleaned) > _CONFIRM_MAX_LEN:
        return False
    if any(word in cleaned for word in _CONFIRM_DENY_WORDS):
        return False
    if cleaned in _CONFIRM_SHORT_WORDS:
        return True
    return any(word in cleaned for word in _CONFIRM_CORE_WORDS)


def _is_short_text(text: str, limit: int = _CONFIRM_MAX_LEN) -> bool:
    """是不是一句短回复（用于「有等确认时任何短句都归本处理器」）"""
    cleaned = _clean_text(text)
    return bool(cleaned) and len(cleaned) <= limit

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
