"""
订单/发货这类平台 IO 的独立线程池。

不占事件循环（一个客户等 40002 重试不会卡住其他客户），也限制最大并发避免打爆平台。
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor

# 订单/发货这类平台 IO 统一走独立线程池：
# 不占事件循环（一个客户等 40002 重试不会卡住其他客户），也限制最大并发避免打爆平台。
_IO_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="order-io")

async def _io(fn, *args, **kwargs):
    """在线程池里跑阻塞的平台调用"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_IO_EXECUTOR, lambda: fn(*args, **kwargs))
