"""跨消息状态的薄封装（等确认 / 报告去重 / 发货确认）。"""
from .state import _REPORT_DEDUP_TTL
from .state import _SHIP_CONFIRMED_TTL
from .state import _SN_CONFIRMED_TTL
from .state import _SN_CONFIRM_TTL
from .state import _report_sent
from .state import _report_sent_guard
from .state import _serial_confirm_guard
from .state import _serial_confirm_pending
from .state import _serial_confirmed
from .state import _ship_confirm_guard
from .state import _ship_confirmed
from .state import _sn_key
from typing import Any
from typing import Dict
from typing import Optional
import time


class StateMixin:
    def _serial_needs_confirm(self, shop_id: str, from_uid: str, sn: str) -> bool:
        """这个序列号是不是还没经过客户确认（确认过/TTL 内不重复问）"""
        if not sn:
            return False
        key = _sn_key(shop_id, from_uid)
        now = time.time()
        with _serial_confirm_guard:
            record = _serial_confirmed.get(key)
            if record and record.get("sn") == sn:
                if now - float(record.get("ts") or 0) <= _SN_CONFIRMED_TTL:
                    return False
                _serial_confirmed.pop(key, None)
        return True

    def _mark_serial_confirmed(self, shop_id: str, from_uid: str, sn: str) -> None:
        with _serial_confirm_guard:
            _serial_confirmed[_sn_key(shop_id, from_uid)] = {"sn": sn, "ts": time.time()}

    def _report_recently_sent(self, shop_id: str, from_uid: str, sn: str) -> bool:
        """这台机器的报告是不是刚发过（TTL 内不再重复发）"""
        if not sn:
            return False
        key = f"{shop_id}:{from_uid}:{sn}"
        now = time.time()
        with _report_sent_guard:
            record = _report_sent.get(key)
            if not record:
                return False
            if now - float(record.get("ts") or 0) > _REPORT_DEDUP_TTL:
                _report_sent.pop(key, None)
                return False
            return True

    def _mark_report_sent(self, shop_id: str, from_uid: str, sn: str) -> None:
        if not sn:
            return
        with _report_sent_guard:
            _report_sent[f"{shop_id}:{from_uid}:{sn}"] = {"ts": time.time()}

    def _clear_report_sent(self, shop_id: str, from_uid: str, sn: str) -> None:
        with _report_sent_guard:
            _report_sent.pop(f"{shop_id}:{from_uid}:{sn}", None)

    def _set_confirm_pending(self, shop_id: str, from_uid: str, **info: Any) -> None:
        record = dict(info)
        record["ts"] = time.time()
        with _serial_confirm_guard:
            _serial_confirm_pending[_sn_key(shop_id, from_uid)] = record

    def _pop_confirm_pending(self, shop_id: str, from_uid: str) -> Optional[Dict[str, Any]]:
        key = _sn_key(shop_id, from_uid)
        with _serial_confirm_guard:
            record = _serial_confirm_pending.pop(key, None)
        if not record:
            return None
        if time.time() - float(record.get("ts") or 0) > _SN_CONFIRM_TTL:
            return None
        return record

    def _peek_confirm_pending(self, shop_id: str, from_uid: str) -> Optional[Dict[str, Any]]:
        key = _sn_key(shop_id, from_uid)
        with _serial_confirm_guard:
            record = _serial_confirm_pending.get(key)
            if not record:
                return None
            if time.time() - float(record.get("ts") or 0) > _SN_CONFIRM_TTL:
                _serial_confirm_pending.pop(key, None)
                return None
            return dict(record)

    def _mark_ship_confirmed(self, shop_id: str, from_uid: str, sn: str, order_sn: str) -> None:
        with _ship_confirm_guard:
            _ship_confirmed[f"{shop_id}:{from_uid}:{sn}:{order_sn}"] = {"ts": time.time()}

    def _ship_needs_confirm(self, shop_id: str, from_uid: str, sn: str, order_sn: str) -> bool:
        """这次发货是不是还没经客户确认（同一序列号+订单确认一次即可，TTL 内不再问）"""
        if not sn or not order_sn:
            return True
        key = f"{shop_id}:{from_uid}:{sn}:{order_sn}"
        now = time.time()
        with _ship_confirm_guard:
            record = _ship_confirmed.get(key)
            if not record:
                return True
            if now - float(record.get("ts") or 0) > _SHIP_CONFIRMED_TTL:
                _ship_confirmed.pop(key, None)
                return True
            return False
