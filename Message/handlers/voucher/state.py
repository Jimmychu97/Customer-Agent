"""
跨消息的进程内状态与锁。

必须独占一个模块：这些字典和锁是全进程单实例，被建单、发货、报告去重共同读写。
拆进 mixin 会按实例各复制一份，防重复发货/防重复建单当场失效。
"""
import threading
from typing import Any, Dict, Set, Tuple

# 按 (shop_id, from_uid) 串行化"查找 + 建单"，避免并发消息把同一张券建成两单
_order_locks: Dict[Tuple[str, str], threading.Lock] = {}

_order_locks_guard = threading.Lock()

# 发货在途占位：同一订单号同时只允许一次发货接口调用。
# 平台会把同一条消息重复推送，两个消费者线程会同时通过数据库的已发货检查，
# 只靠 status 判断挡不住（两次检查都在写库之前），而重复发货不可回滚。
_ship_inflight: Set[str] = set()

_ship_inflight_guard = threading.Lock()

_SN_CONFIRM_TTL = 1800.0          # 等确认的有效期（秒）

_SN_CONFIRMED_TTL = 1800.0        # 确认过的序列号有效期（秒），期间重复发不再问

_serial_confirm_pending: Dict[str, Dict[str, Any]] = {}

_serial_confirmed: Dict[str, Dict[str, Any]] = {}

_serial_confirm_guard = threading.Lock()

# 发货也要客户确认：序列号 + 订单核对无误、客户回「确认」后才真的调发货接口
_SHIP_CONFIRMED_TTL = 900.0

_ship_confirmed: Dict[str, Dict[str, Any]] = {}

_ship_confirm_guard = threading.Lock()

def _sn_key(shop_id: str, from_uid: str) -> str:
    return f"{shop_id}:{from_uid}"

# 报告防重复发送：同一客户、同一序列号的报告在 TTL 内只发一次（客户/平台重复推消息）
_REPORT_DEDUP_TTL = 600.0

_report_sent: Dict[str, Dict[str, Any]] = {}

_report_sent_guard = threading.Lock()

def _get_order_lock(shop_id: str, from_uid: str) -> threading.Lock:
    """获取指定客户的建单锁"""
    with _order_locks_guard:
        lock = _order_locks.get((shop_id, from_uid))
        if lock is None:
            lock = threading.Lock()
            _order_locks[(shop_id, from_uid)] = lock
        return lock
