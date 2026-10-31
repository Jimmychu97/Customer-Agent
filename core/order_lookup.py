"""
待发货订单查询（给服务模式判定与自动发货提供依据）

拼多多订单列表接口（recentOrderList）没有买家UID字段，只有脱敏昵称，
因此无法按客户精确反查；这里只在「全店仅一笔待发货订单」时给出唯一候选：
确认不了就返回 None，宁可不自动发货，也不发错单。

接口有频率限制（40002 操作太过频繁）：
- 限流/冷却/退避**按店铺**隔离，一个店铺被限流不影响别的店铺；
- 同店铺并发请求做「单飞」合并：N 个客户同时查，只真打一次接口；
- 缓存 300s、最小请求间隔 30s；客户刚下单可 force_refresh 强制刷新（每店铺 30s 一次）；
- 订单元数据（规格）优先从待发货列表拿，详情接口只做兜底并带负缓存。
"""
import os
import threading
import time
from typing import Any, Dict, List, Optional, Set

from utils.logger_loguru import get_logger

logger = get_logger("OrderLookup")

_CACHE_TTL = 300.0          # 待发货列表缓存时长（秒）
_MIN_INTERVAL = 30.0        # 两次真实请求的最小间隔（秒，按店铺）
_FORCE_MIN_INTERVAL = 10.0  # 客户主动触发（发序列号/问订单）的最小间隔（秒，按店铺）
_BACKOFF_BASE = 30.0        # 命中限流的首次冷却（秒）
_BACKOFF_MAX = 300.0        # 冷却上限（秒）
_SINGLEFLIGHT_WAIT = 15.0   # 等别人拉完结果的最长时间（秒）
_DETAIL_RETRY_DELAYS = (2.0, 4.0)  # 订单详情命中限流后的重试间隔（秒）
_DETAIL_COOLDOWN_ON_LIMIT = 60.0   # 订单详情重试仍被限流时的冷却时长（秒）
_DETAIL_FAIL_TTL = 60.0            # 详情失败的负缓存（秒）
_BROWSER_MIN_INTERVAL = 60.0        # 浏览器兜底的最小间隔（秒，按店铺）
_RELOGIN_MIN_INTERVAL = 1800.0      # 自动重新登录的最小间隔（秒，按店铺）
_HTTP_BLOCK_SECONDS = 900.0         # HTTP 订单接口被风控后的熔断时长（秒，期间只走浏览器）
_BROWSER_CACHE_TTL = 600.0          # 浏览器兜底结果缓存时长（秒，浏览器慢，缓存久一点）

_lock = threading.Lock()
_last_call_ts: Dict[str, float] = {}    # shop -> 上次真实请求时间
_force_ts: Dict[str, float] = {}        # shop -> 上次强制刷新时间
_cooldown_until: Dict[str, float] = {}  # shop -> 冷却截止时间
_limit_streak: Dict[str, int] = {}      # shop -> 连续限流次数（指数退避）
_cache: Dict[str, tuple] = {}           # shop -> (ts, orders)
_detail_cache: Dict[str, tuple] = {}    # order_sn -> (ts, detail|None)
_detail_fail: Dict[str, float] = {}     # order_sn -> 失败时间（负缓存）
_last_fetch_ok: Dict[str, bool] = {}    # shop -> 上次是否真拿到数据
_inflight: Dict[str, threading.Event] = {}  # shop -> 正在请求的标记（单飞）
_stats: Dict[str, Dict[str, int]] = {}  # shop -> 计数
_browser_ts: Dict[str, float] = {}      # shop -> 上次浏览器兜底时间
_relogin_ts: Dict[str, float] = {}      # shop -> 上次自动重新登录时间
_http_blocked_until: Dict[str, float] = {}  # shop -> HTTP 熔断截止时间
_cache_ttl: Dict[str, float] = {}       # shop -> 该店铺缓存实际 TTL


def _fetch_orders_via_browser(account_user_id: str) -> List[Dict[str, Any]]:
    """浏览器兜底：用已登录的浏览器抓订单列表（单独函数，方便测试替换）"""
    try:
        from Channel.pinduoduo.utils.API.order_browser import (
            fetch_pending_orders_via_browser,
        )

        return fetch_pending_orders_via_browser(str(account_user_id)) or []
    except Exception as e:
        logger.warning(f"[订单查询] 浏览器兜底异常: {e}")
        return []


def _browser_login_expired() -> bool:
    """上一次浏览器兜底是不是因为「被踢到登录页」失败的"""
    try:
        from Channel.pinduoduo.utils.API import order_browser

        return getattr(order_browser, "LAST_FAILURE", "") == "login_expired"
    except Exception:
        return False


def _maybe_auto_relogin(shop_key: str, shop_platform_id: Any, account_user_id: Any) -> bool:
    """登录过期时用保存的账号密码自动重新登录（每店铺 30 分钟最多一次）"""
    if os.environ.get("ORDER_AUTO_RELOGIN", "1") == "0":
        return False
    now = time.time()
    with _lock:
        last = _relogin_ts.get(shop_key, 0.0)
        if now - last < _RELOGIN_MIN_INTERVAL:
            return False
        _relogin_ts[shop_key] = now
    try:
        from Channel.pinduoduo.utils.API.account_repair import relogin_account_sync

        logger.warning(f"[订单查询] shop={shop_key} 登录态过期，尝试自动重新登录…")
        ok = relogin_account_sync(str(shop_platform_id), str(account_user_id))
        logger.info(f"[订单查询] shop={shop_key} 自动重新登录{'成功' if ok else '失败'}")
        return bool(ok)
    except Exception as e:
        logger.warning(f"[订单查询] shop={shop_key} 自动重新登录异常: {e}")
        return False


def _shop_key(shop_platform_id: Any) -> str:
    return str(shop_platform_id)


def _bump(shop_key: str, name: str) -> None:
    with _lock:
        bucket = _stats.setdefault(shop_key, {})
        bucket[name] = bucket.get(name, 0) + 1


def stats(shop_platform_id: Any = None) -> Dict[str, Any]:
    """查询统计（排查用）：api_call / cache_hit / singleflight_wait / limited / detail_api_call"""
    with _lock:
        if shop_platform_id is None:
            return {shop: dict(v) for shop, v in _stats.items()}
        return dict(_stats.get(_shop_key(shop_platform_id), {}))


def _backoff_seconds(streak: int) -> float:
    """连续限流的指数退避：30 → 60 → 120 → 240 → 300（上限）"""
    if streak <= 0:
        return 0.0
    return min(_BACKOFF_BASE * (2 ** (streak - 1)), _BACKOFF_MAX)


def _is_rate_limited(error_msg: str) -> bool:
    """平台把风控/限流伪装成「操作太过频繁」（error_code=40002）"""
    text = str(error_msg or "")
    return "40002" in text or "太过频繁" in text


def is_cooling_down(shop_platform_id: Any = None) -> bool:
    """是否处于限流冷却期（给了 shop 就只看该店铺）"""
    now = time.time()
    with _lock:
        if shop_platform_id is not None:
            return now < _cooldown_until.get(_shop_key(shop_platform_id), 0.0)
        return any(now < until for until in _cooldown_until.values())


def _read_cache(shop_key: str) -> Optional[List[Dict[str, Any]]]:
    with _lock:
        cached = _cache.get(shop_key)
        ttl = _cache_ttl.get(shop_key, _CACHE_TTL)
        if cached and time.time() - cached[0] < ttl:
            return cached[1]
    return None


def _fetch_pending_orders(
    shop_platform_id: str, account_user_id: str, force_refresh: bool = False
) -> List[Dict[str, Any]]:
    """取待发货订单（缓存 + 按店铺限流 + 单飞），失败返回空列表"""
    key = _shop_key(shop_platform_id)
    now = time.time()

    with _lock:
        cached = _cache.get(key)
        # force_refresh 时不吃 TTL 缓存（客户刚下单，缓存里还没有），但要受强制刷新间隔约束
        ttl = _cache_ttl.get(key, _CACHE_TTL)
        if not force_refresh and cached and now - cached[0] < ttl:
            _stats.setdefault(key, {})
            _stats[key]["cache_hit"] = _stats[key].get("cache_hit", 0) + 1
            return cached[1]
        waiter = _inflight.get(key)
        if waiter is None:
            # 同店铺并发合并：已经有请求在跑就等它的结果，不再打接口
            if force_refresh:
                # 客户主动发序列号/问订单：这条路**不受冷却和最小间隔限制**（否则一被限流
                # 就要干等几十秒到几分钟），只按店铺做高频保护
                if now - _force_ts.get(key, 0.0) < _FORCE_MIN_INTERVAL:
                    return cached[1] if cached else []
            else:
                if now < _cooldown_until.get(key, 0.0):
                    _stats.setdefault(key, {})
                    _stats[key]["cooldown_skip"] = _stats[key].get("cooldown_skip", 0) + 1
                    return cached[1] if cached else []
                if now - _last_call_ts.get(key, 0.0) < _MIN_INTERVAL:
                    return cached[1] if cached else []
            waiter = threading.Event()
            _inflight[key] = waiter
            _last_call_ts[key] = now
            if force_refresh:
                _force_ts[key] = now
            _stats.setdefault(key, {})
            _stats[key]["api_call"] = _stats[key].get("api_call", 0) + 1
            own_request = True
        else:
            _stats.setdefault(key, {})
            _stats[key]["singleflight_wait"] = _stats[key].get("singleflight_wait", 0) + 1
            own_request = False

    if not own_request:
        # 等别人拉完，直接读结果（不持有锁，避免互相卡死）
        waiter.wait(timeout=_SINGLEFLIGHT_WAIT)
        return _read_cache(key) or []

    orders: List[Dict[str, Any]] = []
    ok = False
    list_ok = False
    error_msg = ""
    try:
        from Channel.pinduoduo.utils.API.order_api import MmsOrderAPI

        try:
            from core.session_health import ensure_started as _ensure_heartbeat

            _ensure_heartbeat()
        except Exception as e:
            logger.debug(f"[订单查询] 启动会话心跳失败: {e}")

        api = MmsOrderAPI(str(shop_platform_id), str(account_user_id))

        # 熔断：recentOrderList 一旦被判定风控（40002），继续硬打只会加重风控，
        # 所以本店铺在 _HTTP_BLOCK_SECONDS 内直接跳过 HTTP，走浏览器兜底。
        with _lock:
            http_blocked_until = _http_blocked_until.get(key, 0.0)
        http_allowed = now >= http_blocked_until

        # 主口径：商家后台订单列表（recentOrderList），按「未发货+已支付」筛出待发货单。
        # 合并发货接口（mergeShippingMap）只含「可合并发货」的单，普通单在里面是空的，
        # 以前只认它就会把「有单」误判成「没有待发货订单」。
        if not http_allowed:
            error_msg = "HTTP 订单接口风控熔断中"
            logger.info(
                f"[订单查询] shop={key} HTTP 熔断中（还剩 {int(http_blocked_until - now)}s），"
                f"直接走浏览器兜底")
            result = {"success": False, "orders": [], "error_msg": error_msg}
        else:
            result = api.list_orders(page_num=1, page_size=50, order_type=0)
        if result.get("success"):
            raw_orders = result.get("orders") or []
            orders = [o for o in raw_orders if o.get("pending_shipment")]
            ok = True
            list_ok = True
            logger.info(
                f"[订单查询] shop={key} 订单列表命中待发货 {len(orders)} 笔"
                f"（原始 {len(raw_orders)} 笔）"
            )
        else:
            error_msg = str(result.get("error_msg") or "")
            logger.warning(f"[订单查询] shop={key} 订单列表查询失败: {error_msg}")
            if http_allowed and _is_rate_limited(error_msg):
                with _lock:
                    _http_blocked_until[key] = now + _HTTP_BLOCK_SECONDS
                    _stats.setdefault(key, {})
                    _stats[key]["http_blocked"] = _stats[key].get("http_blocked", 0) + 1
                logger.warning(
                    f"[订单查询] shop={key} HTTP 接口被风控，熔断 "
                    f"{int(_HTTP_BLOCK_SECONDS)}s（期间改走浏览器兜底）")

        if not ok and http_allowed:
            # 兜底 1：合并发货页（该接口固定只返回待发货单）
            merged = api.list_pending_shipment_orders(page_num=1, page_size=50)
            if merged.get("success"):
                orders = merged.get("orders") or []
                ok = True
                logger.info(
                    f"[订单查询] shop={key} 合并发货页兜底命中待发货 {len(orders)} 笔")
            else:
                error_msg = str(merged.get("error_msg") or error_msg)

        # 兜底 2：recentOrderList 被风控（40002，多半是 anti-content 过期）时，
        # 用已登录的浏览器打开订单页，抓页面自己发出的那次请求（签名由页面现算）。
        if not list_ok and os.environ.get("ORDER_BROWSER_FALLBACK", "1") != "0":
            with _lock:
                allow = now - _browser_ts.get(key, 0.0) >= _BROWSER_MIN_INTERVAL
                if allow:
                    _browser_ts[key] = time.time()
            if allow:
                browser_orders = _fetch_orders_via_browser(str(account_user_id))
                if not browser_orders:
                    # 浏览器也被踢到登录页：登录态过期。用保存的账号密码自动重新登录一次
                    # （每账号 30 分钟最多一次），成功后再抓一次。
                    if _browser_login_expired() and _maybe_auto_relogin(
                            key, shop_platform_id, account_user_id):
                        browser_orders = _fetch_orders_via_browser(str(account_user_id))
                if browser_orders:
                    orders = browser_orders
                    ok = True
                    with _lock:
                        _cache_ttl[key] = _BROWSER_CACHE_TTL
                    logger.info(
                        f"[订单查询] shop={key} 浏览器兜底命中待发货 {len(orders)} 笔")
    except Exception as e:
        error_msg = f"异常: {e}"
        logger.warning(f"[订单查询] shop={key} 待发货订单查询异常: {e}")
    finally:
        with _lock:
            _inflight.pop(key, None)
        waiter.set()

    with _lock:
        if not ok and _is_rate_limited(error_msg):
            streak = _limit_streak.get(key, 0) + 1
            _limit_streak[key] = streak
            cooldown = _backoff_seconds(streak)
            _cooldown_until[key] = time.time() + cooldown
            _stats.setdefault(key, {})
            _stats[key]["limited"] = _stats[key].get("limited", 0) + 1
            logger.warning(
                f"[订单查询] shop={key} 被限流（{error_msg}）第 {streak} 次，"
                f"冷却 {int(cooldown)}s（只影响本店铺）")
        elif ok:
            _limit_streak.pop(key, None)
            _cooldown_until.pop(key, None)
        _last_fetch_ok[key] = ok
        _cache[key] = (time.time(), orders)
    return orders


def pending_orders_summary(
    shop_platform_id: str, account_user_id: str, force_refresh: bool = False
) -> Dict[str, Any]:
    """待发货订单概览（给客服话术用：直接告诉客户名下有没有待发货的单）

    订单接口没有买家 UID，只能看「本店待发货单」，因此这里只描述店铺侧事实：
        unique      - 有单且只有一笔，可直接用它的订单号/商品/规格
        common      - 多笔，但商品+规格一致（知道查什么，但不确定是哪一笔）
        mixed       - 多笔且商品不同，必须让客户指明订单号
        empty       - 确认没有待发货单
        unavailable - 接口没返回（限流/异常），不能下结论

    Args:
        force_refresh: 客户刚下单、缓存里还没有时，允许强制刷新一次（每店铺 30s 一次）

    Returns:
        {"status": 上述字符串, "orders": [...]}
    """
    key = _shop_key(shop_platform_id)
    orders = _fetch_pending_orders(shop_platform_id, account_user_id, force_refresh)
    with _lock:
        fetch_ok = _last_fetch_ok.get(key, True)
    if not fetch_ok:
        return {"status": "unavailable", "orders": []}
    if not orders:
        return {"status": "empty", "orders": []}
    if len(orders) == 1:
        return {"status": "unique", "orders": orders}
    keys = {
        (str(o.get("goods_id")), str(o.get("spec") or ""))
        for o in orders
        if o.get("goods_id")
    }
    return {"status": "common" if len(keys) == 1 else "mixed", "orders": orders}


def pick_unique_pending_ship_order(
    shop_platform_id: str,
    account_user_id: str,
    goods_id: Any = None,
    exclude_order_sns: Optional[Set[str]] = None,
) -> Optional[Dict[str, Any]]:
    """在待发货订单里挑「唯一候选」

    Args:
        shop_platform_id: 平台店铺 ID
        account_user_id: 客服账号 ID（取 cookies）
        goods_id: 已知商品 ID，给了就只在该商品的单里挑
        exclude_order_sns: 本地已处理过的订单号（不重复发货）

    Returns:
        唯一候选订单字典；0 笔或多笔都返回 None
    """
    orders = _fetch_pending_orders(shop_platform_id, account_user_id)
    if not orders:
        return None

    exclude = {str(s) for s in (exclude_order_sns or set()) if s}
    candidates = [
        o for o in orders if o.get("order_sn") and str(o.get("order_sn")) not in exclude
    ]
    if goods_id not in (None, "", 0, "0"):
        matched = [o for o in candidates if str(o.get("goods_id")) == str(goods_id)]
        if not matched:
            return None
        candidates = matched

    if len(candidates) != 1:
        if len(candidates) > 1:
            logger.info(
                f"[订单查询] 待发货候选 {len(candidates)} 笔，无法确定客户对应哪一笔，不自动发货"
            )
        return None

    order = candidates[0]
    logger.info(
        f"[订单查询] 命中唯一待发货订单 order_sn={order.get('order_sn')} "
        f"goods_id={order.get('goods_id')} 商品={str(order.get('goods_name'))[:20]}"
    )
    return order


def common_pending_goods(
    shop_platform_id: str, account_user_id: str
) -> Optional[Dict[str, Any]]:
    """所有待发货订单的「商品 + 规格」一致时，返回 {goods_id, spec, goods_name}

    订单接口没有买家UID，定位不到具体某一笔；但同一商品同一规格时，
    验机服务（server_id / zhenxin_server_id）是唯一的，据此就能核对出该查哪个服务。
    """
    orders = _fetch_pending_orders(shop_platform_id, account_user_id)
    if not orders:
        return None

    keys = {
        (str(o.get("goods_id")), str(o.get("spec") or ""))
        for o in orders
        if o.get("goods_id")
    }
    if len(keys) != 1:
        if len(keys) > 1:
            logger.info(f"[订单查询] 待发货订单的商品/规格不一致 {sorted(keys)}，不做商品核对")
        return None

    goods_id, spec = keys.pop()
    first = next((o for o in orders if str(o.get("goods_id")) == goods_id), {})
    logger.info(
        f"[订单查询] 待发货订单商品一致 goods_id={goods_id} spec={spec!r} "
        f"商品={str(first.get('goods_name'))[:20]}"
    )
    return {"goods_id": goods_id, "spec": spec, "goods_name": first.get("goods_name") or ""}


def _query_order_once(
    shop_platform_id: str, account_user_id: str, order_sn: str
) -> tuple:
    """单次按订单号查询，返回 (order|None, error_msg)"""
    try:
        from Channel.pinduoduo.utils.API.order_api import MmsOrderAPI

        api = MmsOrderAPI(str(shop_platform_id), str(account_user_id))
        result = api.get_order_by_sn(order_sn)
        if result.get("success"):
            return result.get("order"), ""
        return None, str(result.get("error_msg") or "")
    except Exception as e:
        return None, f"异常: {e}"


def get_order_detail(
    shop_platform_id: str,
    account_user_id: str,
    order_sn: str,
    ttl: float = _CACHE_TTL,
    retries: int = 0,
) -> Optional[Dict[str, Any]]:
    """按订单号查平台订单详情（含 pending_shipment 待发货判定）

    用途有两个：
    1. 防呆：发货前核对订单是不是「待发货」，不是就绝不调发货接口；
    2. 补全：从订单里拿到 goods_id/spec（查询项目）。

    Args:
        retries: 命中限流后的重试次数。重试是阻塞的（间隔见 _DETAIL_RETRY_DELAYS），
            异步调用方请放到线程里跑。

    Returns:
        订单字典；查不到（订单不存在 / 接口失败 / 被限流）返回 None。
        注意：None 不代表「不能发货」，调用方只应把 None 当「无法核对」处理。
    """
    if not order_sn:
        return None
    shop_key = _shop_key(shop_platform_id)
    key = str(order_sn)
    now = time.time()

    with _lock:
        cached = _detail_cache.get(key)
        if cached and now - cached[0] < ttl:
            return cached[1]
        pending_cached = _cache.get(shop_key)
        cooling = now < _cooldown_until.get(shop_key, 0.0)
        failed_at = _detail_fail.get(key, 0.0)

    # 同一轮里刚拉过待发货列表：直接在里面找，别再打一次订单接口（40002 很敏感）
    if pending_cached and now - pending_cached[0] < _CACHE_TTL:
        for order in pending_cached[1] or []:
            if str(order.get("order_sn")) == key:
                with _lock:
                    _detail_cache[key] = (now, order)
                return order

    if failed_at and now - failed_at < _DETAIL_FAIL_TTL:
        # 负缓存：这个订单刚查失败过，60s 内不重复打
        with _lock:
            _detail_cache[key] = (now, None) if not cached else _detail_cache.get(key)
        return None

    if cooling:
        return cached[1] if cached else None

    _bump(shop_key, "detail_api_call")
    detail: Optional[Dict[str, Any]] = None
    error_msg = ""
    delays = (0.0,) + _DETAIL_RETRY_DELAYS[:max(0, retries)]
    for attempt, delay in enumerate(delays, start=1):
        if delay:
            time.sleep(delay)
        detail, error_msg = _query_order_once(shop_platform_id, account_user_id, key)
        if detail is not None or not _is_rate_limited(error_msg):
            break
        logger.warning(
            f"[订单查询] 按订单号查询被限流（{error_msg}）order_sn={key} "
            f"第 {attempt}/{len(delays)} 次未成功")

    rate_limited = _is_rate_limited(error_msg)
    if detail is not None:
        logger.info(
            f"[订单查询] 订单详情 order_sn={key} 待发货={detail.get('pending_shipment')} "
            f"状态={detail.get('order_status_str')} 规格={detail.get('spec')!r}")
        with _lock:
            _detail_fail.pop(key, None)
    elif rate_limited:
        with _lock:
            _detail_fail[key] = time.time()
            _cooldown_until[shop_key] = max(
                _cooldown_until.get(shop_key, 0.0),
                time.time() + _DETAIL_COOLDOWN_ON_LIMIT)
        logger.warning(
            f"[订单查询] 按订单号查询被限流（{error_msg}），重试后仍失败，暂停 "
            f"{int(_DETAIL_COOLDOWN_ON_LIMIT)}s（店铺 {shop_key}）")
    else:
        logger.info(f"[订单查询] 未查到订单 order_sn={key}: {error_msg}")
        with _lock:
            _detail_fail[key] = time.time()

    if detail is not None or not rate_limited:
        # 限流期间的 None 不落缓存：那是「查不了」而不是「没有这单」，冷却后还要重试
        with _lock:
            _detail_cache[key] = (time.time(), detail)
    return detail


def _local_orders(shop_platform_id: str, from_uid: str, limit: int = 5):
    """该客户（会话）名下出现过的订单号（任何状态，用来判断订单归属）"""
    try:
        from database import db_manager
        from database.models import Order

        with db_manager.session_scope() as session:
            rows = (
                session.query(Order)
                .filter(
                    Order.shop_id == str(shop_platform_id),
                    Order.session_id == str(from_uid),
                    Order.order_sn.isnot(None),
                    Order.order_sn != "",
                )
                .order_by(Order.id.desc())
                .limit(limit)
                .all()
            )
            return [{"order_sn": r.order_sn or ""} for r in rows if r.order_sn]
    except Exception as e:
        logger.debug(f"[订单查询] 读客户订单记录失败 from_uid={from_uid}: {e}")
        return []


def _local_pending_orders(shop_platform_id: str, from_uid: str, limit: int = 3):
    """该客户（会话）名下还没发货的订单行（只取订单号）"""
    try:
        from database import db_manager
        from database.models import Order

        with db_manager.session_scope() as session:
            rows = (
                session.query(Order)
                .filter(
                    Order.shop_id == str(shop_platform_id),
                    Order.session_id == str(from_uid),
                    Order.status != 1,
                    Order.order_sn.isnot(None),
                    Order.order_sn != "",
                )
                .order_by(Order.id.desc())
                .limit(limit)
                .all()
            )
            return [{"order_sn": r.order_sn or ""} for r in rows if r.order_sn]
    except Exception as e:
        logger.debug(f"[订单查询] 读客户本地订单失败 from_uid={from_uid}: {e}")
        return []


def pending_order_hint(shop_platform_id: str, account_user_id: str, from_uid: str) -> str:
    """给 AI/客服话术用的「这个客户当前待发货订单」一句话（查不到返回空串）

    AI 每次回复都会带着这条，从而不需要再让客户发订单卡片/抄订单号。
    **必须只讲这个客户自己的单**：平台待发货列表是全店口径（无买家UID），
    只有本会话里出现过该订单号（客户发过订单卡片/订单号）才算他的，否则宁可留空。
    """
    try:
        summary = pending_orders_summary(shop_platform_id, account_user_id)
    except Exception:
        summary = {"status": "unavailable", "orders": []}
    status = str(summary.get("status") or "")
    orders = summary.get("orders") or []
    local = _local_orders(shop_platform_id, from_uid)   # 任何状态，用来判断归属
    owned = {o["order_sn"] for o in local}

    def _desc(order):
        desc = " / ".join(
            x for x in (str(order.get("goods_name") or ""), str(order.get("spec") or "")) if x)
        return f"订单号 {order.get('order_sn')}" + (f"（{desc}）" if desc else "")

    if status in ("unique", "common", "mixed") and orders:
        mine = [o for o in orders if str(o.get("order_sn") or "") in owned]
        if len(mine) == 1:
            return _desc(mine[0])
        if len(mine) > 1:
            newest = max(mine, key=lambda o: (int(o.get("order_time") or 0),
                                              str(o.get("order_sn") or "")))
            return _desc(newest) + f"（共 {len(mine)} 笔，按最新一单）"
        return ""      # 平台上这些单都不确定是他的 → 不讲，避免泄漏别人的订单
    if local:
        return f"订单号 {local[0]['order_sn']}（平台状态待核对）"
    return ""


def clear_cache() -> None:
    """清空缓存与限流状态（测试/切换账号用）"""
    with _lock:
        _cache.clear()
        _detail_cache.clear()
        _detail_fail.clear()
        _last_call_ts.clear()
        _force_ts.clear()
        _cooldown_until.clear()
        _limit_streak.clear()
        _last_fetch_ok.clear()
        _stats.clear()
        _browser_ts.clear()
        _relogin_ts.clear()
        _http_blocked_until.clear()
        _cache_ttl.clear()
