"""
核销自动检测处理器

规则：
- 只有序列号/IMEI → 建 Order 记录，引导用户补充卡券号（截断链，AI不介入）
- 只有卡券号      → 建 Order 记录，引导用户补充序列号/IMEI（截断链，AI不介入）
- 两个都有        → 调用外部 API 验证核销
  - 核销成功      → 发成功回复，放行链（AI可继续回答后续问题）
  - 核销失败/异常 → 发失败回复，截断链

实现已拆分到 Message/handlers/voucher/：
- parsers / intents / state / texts / modes / io_exec / report  无状态叶子模块
- orders_mixin / ship_mixin / verify_mixin / report_mixin / ...  处理器各职责
- handler.py                                                    分派本体
本文件只保留导出，历史 import 路径（含 ai_handler 的导入）不受影响。
"""

from .voucher.io_exec import _IO_EXECUTOR, _io
from .voucher.parsers import (
    _luhn_check, _normalize_number, _extract_imei, _extract_serial, _is_phone,
    _COUPON_MIN_DIGITS, _COUPON_MAX_DIGITS, _COUPON_STRICT_MAX_DIGITS, _ORDER_SN_RE,
    _strip_order_sn, _has_imei_label, _extract_coupon, _extract_coupon_strict,
    _extract_order_sn, _parse_json_content,
)
from .voucher.intents import (
    _SN_CONFIRM_WORDS, _RESEND_WORDS, _ORDER_ASK_WORDS,
    _is_confirm_text, _is_resend_text, _is_order_ask_text,
)
from .voucher.state import (
    _order_locks, _order_locks_guard, _ship_inflight, _ship_inflight_guard,
    _SN_CONFIRM_TTL, _SN_CONFIRMED_TTL, _serial_confirm_pending, _serial_confirmed,
    _serial_confirm_guard, _SHIP_CONFIRMED_TTL, _ship_confirmed, _ship_confirm_guard,
    _REPORT_DEDUP_TTL, _report_sent, _report_sent_guard, _sn_key, _get_order_lock,
)
from .voucher.texts import (
    _VOUCHER_PENDING, _VOUCHER_SUCCESS, _VOUCHER_FAILED, _VOUCHER_ERROR,
    _SHIP_SUCCESS, _SHIP_FAILED, _SHIP_ERROR, _SHIP_SERIAL_GUIDE, _SHIP_NEED_ORDER_TEXT,
    _SHIP_ALREADY_DONE_TEXT, _SHIP_NOT_CONFIGURED_TEXT, _UPSELL_HINT, _SHIP_IN_FLIGHT_TEXT,
    _FIXED_REVIEW_TEXT, _REPORT_ALREADY_SENT_TEXT,
)
from .voucher.modes import _SERVICE_MODE_VOUCHER, _SERVICE_MODE_SHIP, _SERVICE_MODE_AUTO
from .voucher.report import _format_gsx_result, _analyze_gsx_result
from .voucher.orders_mixin import PENDING_WINDOW_MINUTES
from .voucher.handler import VoucherDetectionHandler

__all__ = [
    "VoucherDetectionHandler",
    "PENDING_WINDOW_MINUTES",
]
