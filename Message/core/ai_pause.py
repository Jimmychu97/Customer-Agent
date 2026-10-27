"""
按客户维度暂停 AI 自动回复的管理器

规则：
- 客服在某个客户会话里发送 STOP AI 后，只暂停「该客户」的自动回复；
- 默认 3 分钟后自动恢复（过期即恢复，无需后台任务）；
- 收到 START AI 可立即恢复；
- 状态保存在内存中，不落库（3 分钟短时冷却，无需持久化）。
"""
import threading
import time
from typing import Dict, Optional, Tuple

from utils.logger_loguru import get_logger

logger = get_logger("AIPauseManager")

# 默认暂停时长：3 分钟
DEFAULT_PAUSE_SECONDS = 180


class AIPauseManager:
    """客户级 AI 暂停管理器（线程安全）"""

    def __init__(self):
        self._lock = threading.Lock()
        # key: (channel, shop_id, account_id, customer_uid) -> 暂停截止时间戳
        self._paused_until: Dict[Tuple[str, str, str, str], float] = {}

    @staticmethod
    def _key(channel, shop_id, account_id, customer_uid) -> Tuple[str, str, str, str]:
        return (
            str(channel or ""),
            str(shop_id if shop_id is not None else ""),
            str(account_id if account_id is not None else ""),
            str(customer_uid if customer_uid is not None else ""),
        )

    def pause(self, channel, shop_id, account_id, customer_uid,
              seconds: int = DEFAULT_PAUSE_SECONDS) -> Optional[float]:
        """暂停指定客户的自动回复，返回恢复时间戳；customer_uid 为空则不处理"""
        if not customer_uid:
            return None
        key = self._key(channel, shop_id, account_id, customer_uid)
        until = time.time() + max(1, int(seconds))
        with self._lock:
            self._paused_until[key] = until
        logger.info(f"[AI暂停] 客户 {customer_uid} 暂停自动回复 {seconds}s (shop={shop_id}, account={account_id})")
        return until

    def resume(self, channel, shop_id, account_id, customer_uid) -> bool:
        """立即恢复指定客户的自动回复，返回是否确实处于暂停中"""
        key = self._key(channel, shop_id, account_id, customer_uid)
        with self._lock:
            existed = self._paused_until.pop(key, None) is not None
        if existed:
            logger.info(f"[AI暂停] 客户 {customer_uid} 已手动恢复自动回复 (shop={shop_id}, account={account_id})")
        return existed

    def is_paused(self, channel, shop_id, account_id, customer_uid) -> bool:
        """判断指定客户是否仍在暂停期内（过期自动清理）"""
        if not customer_uid:
            return False
        key = self._key(channel, shop_id, account_id, customer_uid)
        now = time.time()
        with self._lock:
            until = self._paused_until.get(key)
            if until is None:
                return False
            if until <= now:
                self._paused_until.pop(key, None)
                logger.info(f"[AI暂停] 客户 {customer_uid} 暂停已到期，恢复自动回复")
                return False
            return True

    def remaining(self, channel, shop_id, account_id, customer_uid) -> int:
        """剩余暂停秒数，0 表示未暂停"""
        key = self._key(channel, shop_id, account_id, customer_uid)
        now = time.time()
        with self._lock:
            until = self._paused_until.get(key)
            if until is None or until <= now:
                return 0
            return int(until - now)

    def clear(self):
        """清空所有暂停状态（测试/停机用）"""
        with self._lock:
            self._paused_until.clear()


# 全局单例
ai_pause_manager = AIPauseManager()
