# 生命周期管理模块
import asyncio
import time
import websockets
from websockets import exceptions as ws_exceptions
from typing import Optional, Any
from utils.logger_loguru import get_logger
from Channel.pinduoduo.utils.API.get_token import GetToken
from config import config


class LifecycleMixin:
    """生命周期管理 Mixin"""

    async def start_account(self, shop_id: str, user_id: str, on_success: callable, on_failure: callable):
        """启动指定店铺下账号"""
        account_info = db_manager.get_account(self.channel_name, shop_id, user_id)
        if not account_info:
            error_msg = f"账号 {user_id} 在数据库中不存在"
            self.logger.error(error_msg)
            on_failure(error_msg)
            return

        username = account_info.get("username", user_id)
        connection_key = f"{shop_id}_{user_id}"

        self.status_manager.update_status(shop_id, user_id, username, ConnectionState.CONNECTING)

        if connection_key in self._reconnect_tasks:
            self._reconnect_tasks[connection_key].cancel()
            del self._reconnect_tasks[connection_key]

        # 重置 stop_event，保证每次启动都是干净状态
        self._stop_event = None

        if self.reconnect_config.enable_auto_reconnect:
            connect_task = asyncio.create_task(
                self._connect_with_retry(shop_id, user_id, username, on_success, on_failure)
            )
        else:
            connect_task = asyncio.create_task(
                self._connect_single_attempt(shop_id, user_id, username, on_success, on_failure)
            )

        self._reconnect_tasks[connection_key] = connect_task

    async def stop_account(self, shop_id: str, user_id: str):
        """停止指定店铺下账号"""
        try:
            account_info = db_manager.get_account(self.channel_name, shop_id, user_id)
            if not account_info:
                self.logger.warning(f"账号 {user_id} 不存在，无法停止")
                return

            username = account_info.get("username", user_id)
            connection_key = f"{shop_id}_{user_id}"

            self.logger.info(f"正在停止店铺 {shop_id} 账号 {username}")

            if self._stop_event:
                self._stop_event.set()

            if connection_key in self._reconnect_tasks:
                task = self._reconnect_tasks[connection_key]
                if not task.done():
                    task.cancel()
                    try:
                        await asyncio.wait_for(task, timeout=5.0)
                    except asyncio.CancelledError:
                        self.logger.debug(f"重连任务已被取消: {connection_key}")
                    except asyncio.TimeoutError:
                        self.logger.warning(f"重连任务取消超时: {connection_key}")
                    except Exception as task_error:
                        self.logger.error(f"等待重连任务完成时出错: {task_error}")
                del self._reconnect_tasks[connection_key]
                self.logger.debug(f"已清理重连任务: {connection_key}")

            if connection_key in self._heartbeat_tasks:
                task = self._heartbeat_tasks[connection_key]
                if not task.done():
                    task.cancel()
                    try:
                        await asyncio.wait_for(task, timeout=3.0)
                    except asyncio.CancelledError:
                        self.logger.debug(f"心跳任务已被取消: {connection_key}")
                    except asyncio.TimeoutError:
                        self.logger.warning(f"心跳任务取消超时: {connection_key}")
                    except Exception as task_error:
                        self.logger.error(f"等待心跳任务完成时出错: {task_error}")
                del self._heartbeat_tasks[connection_key]
                self.logger.debug(f"已清理心跳任务: {connection_key}")

            if connection_key in self._health_tasks:
                task = self._health_tasks[connection_key]
                if not task.done():
                    task.cancel()
                    try:
                        await asyncio.wait_for(task, timeout=3.0)
                    except asyncio.CancelledError:
                        self.logger.debug(f"Cookie健康检查任务已被取消: {connection_key}")
                    except asyncio.TimeoutError:
                        self.logger.warning(f"Cookie健康检查任务取消超时: {connection_key}")
                    except Exception as task_error:
                        self.logger.error(f"等待Cookie健康检查任务完成时出错: {task_error}")
                del self._health_tasks[connection_key]
                self.logger.debug(f"已清理Cookie健康检查任务: {connection_key}")

            self.status_manager.update_status(shop_id, user_id, username, ConnectionState.DISCONNECTED)

            if self.ws:
                await self._safe_close_websocket(self.ws)
                self.ws = None
                self.logger.info(f"已关闭店铺 {shop_id} 账号 {username} 的WebSocket连接")

            await self.cleanup_processing_tasks()
            await self.resource_manager.cleanup_all()

            # 保底：确保消费者被停止（如果 init() 的清理路径未执行到）
            queue_name = f"pdd_{shop_id}"
            try:
                from Message import message_consumer_manager
                await message_consumer_manager.stop_consumer(queue_name)
            except Exception as e:
                self.logger.debug(f"保底 stop_consumer 完成（可能已停止）: {queue_name}, {e}")

            self.logger.info(f"成功停止店铺 {shop_id} 账号 {username}")

        except Exception as e:
            self.logger.error(f"停止店铺 {shop_id} 账号 {user_id} 时发生错误: {str(e)}")

    async def init(self, shop_id: str, user_id: str, username: str, on_success: callable, on_failure: callable) -> bool:
        """初始化WebSocket连接和消息处理系统。

        Returns:
            True  = 收到 stop_event 主动停止，上层不重连
            False = 意外断线，上层应重连
        """
        try:
            # stop_event 由调用方管理：
            # - 首次连接：start_account 启动时 _stop_event=None，这里初始化
            # - 重连：_connect_with_retry 在入循环时已重建，这里不再重建
            if self._stop_event is None:
                self._stop_event = asyncio.Event()

            token = GetToken(shop_id, user_id)
            access_token = token.get_token()
            if not access_token:
                error_msg = f"账号 {username} 获取 token 失败（cookie 可能已过期），请重新登录后再启动"
                self.logger.error(error_msg)
                on_failure(error_msg)
                return True  # token 失败不应重连，返回 True 停止重连循环

            queue_name = f"pdd_{shop_id}"
            # 重连时复用消费者，只在首次连接时创建
            await self._setup_message_consumer(queue_name, reuse=True)

            params = {
                "access_token": access_token,
                "role": "mall_cs",
                "client": "web",
                "version": self.API_VERSION
            }
            query = "&".join([f"{k}={v}" for k, v in params.items()])
            full_url = f"{self.base_url}?{query}"

            self.logger.debug(f"正在连接到拼多多 WebSocket: {shop_id}-{username}")

            # 读取账号代理配置
            account_info = db_manager.get_account(self.channel_name, shop_id, user_id)
            proxy = account_info.get('proxy') if account_info else None
            proxy = proxy.strip() if proxy and proxy.strip() else None

            ws_kwargs = dict(
                ping_interval=None,
                max_size=10**7,
                compression=None,
                close_timeout=10,
            )
            if proxy:
                from python_socks.async_.asyncio import Proxy as SocksProxy
                import ssl
                from urllib.parse import urlparse

                parsed_ws = urlparse(full_url)
                ws_host = parsed_ws.hostname
                ws_port = parsed_ws.port or 443

                proxy_obj = SocksProxy.from_url(proxy)
                raw_sock = await proxy_obj.connect(dest_host=ws_host, dest_port=ws_port)

                ssl_ctx = ssl.create_default_context()
                ws_kwargs['sock'] = raw_sock
                ws_kwargs['ssl'] = ssl_ctx
                ws_kwargs['server_hostname'] = ws_host
                self.logger.info(f"账号 {username} WebSocket 接收使用代理: {proxy}")
            else:
                self.logger.info(f"账号 {username} WebSocket 接收未使用代理（直连）")

            async with websockets.connect(full_url, **ws_kwargs) as websocket:
                self.ws = websocket
                self.resource_manager.register_websocket(
                    websocket,
                    f"PDD WebSocket ({shop_id}-{username})"
                )
                self.logger.debug(f"WebSocket连接已建立: {shop_id}-{username}")
                self.status_manager.update_status(shop_id, user_id, username, ConnectionState.CONNECTED)
                on_success()

                connection_key = f"{shop_id}_{user_id}"
                heartbeat_task = None
                if self.heartbeat_config.enable_heartbeat:
                    heartbeat_task = asyncio.create_task(
                        self._heartbeat_loop(websocket, shop_id, user_id, username)
                    )
                    self._heartbeat_tasks[connection_key] = heartbeat_task

                health_task = None
                if self.heartbeat_config.enable_cookie_health_check:
                    health_task = asyncio.create_task(
                        self._cookie_health_loop(shop_id, user_id, username)
                    )
                    self._health_tasks[connection_key] = health_task

                message_task = asyncio.create_task(
                    self._message_loop(websocket, shop_id, user_id, username, queue_name)
                )
                stop_task = asyncio.create_task(self._stop_event.wait())

                try:
                    tasks = [message_task, stop_task]
                    if heartbeat_task:
                        tasks.append(heartbeat_task)
                    if health_task:
                        tasks.append(health_task)

                    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

                    # 判断是主动停止还是意外断线
                    is_stopped = stop_task in done or (self._stop_event and self._stop_event.is_set())
                    if not is_stopped:
                        self.logger.warning(f"连接意外断开，准备重连: {shop_id}-{username}")
                    else:
                        self.logger.debug(f"收到停止信号: {shop_id}-{username}")

                    for task in pending:
                        task.cancel()
                        try:
                            await asyncio.wait_for(task, timeout=3.0)
                        except (asyncio.CancelledError, asyncio.TimeoutError, asyncio.InvalidStateError):
                            pass
                        except Exception as ex:
                            self.logger.debug(f"等待任务取消时出错: {ex}")

                    # 重连时只清理 WS 资源，不停止消费者；主动停止时才完整清理
                    await self._cleanup_ws_resources(queue_name, stop_consumer=is_stopped)
                    return is_stopped

                except asyncio.CancelledError:
                    self.logger.debug(f"WebSocket任务被取消: {shop_id}-{username}")
                    for t in [message_task, heartbeat_task, health_task]:
                        if t:
                            t.cancel()
                            try:
                                await asyncio.wait_for(t, timeout=3.0)
                            except (asyncio.CancelledError, asyncio.TimeoutError, asyncio.InvalidStateError):
                                pass
                    await self._cleanup_ws_resources(queue_name, stop_consumer=True)
                    return True

        except ws_exceptions.ConnectionClosed as e:
            self.status_manager.update_status(shop_id, user_id, username, ConnectionState.ERROR, str(e))
            self.logger.warning(f"WebSocket连接关闭: {shop_id}-{username}, code={getattr(e, 'code', '?')}, 将重连")
            try:
                await self._cleanup_ws_resources(f"pdd_{shop_id}", stop_consumer=False)
            except RuntimeError:
                pass
            return False
        except Exception as e:
            self.status_manager.update_status(shop_id, user_id, username, ConnectionState.ERROR, str(e))
            self.logger.error(f"WebSocket连接错误: {shop_id}-{username}, 错误: {str(e)}")
            try:
                await self._cleanup_ws_resources(f"pdd_{shop_id}", stop_consumer=False)
            except RuntimeError:
                pass
            return False

    def request_stop(self):
        """请求停止WebSocket连接"""
        if self._stop_event:
            self._stop_event.set()

    async def stop_all_connections(self):
        """停止所有连接并清理所有任务"""
        try:
            self.logger.info("正在停止所有连接...")

            if self._stop_event:
                self._stop_event.set()

            for connection_key, task in list(self._reconnect_tasks.items()):
                if not task.done():
                    task.cancel()
                    try:
                        await asyncio.wait_for(task, timeout=5.0)
                    except (asyncio.CancelledError, asyncio.TimeoutError):
                        self.logger.debug(f"任务已取消或超时: {connection_key}")
                    except Exception as e:
                        self.logger.error(f"停止任务时出错: {connection_key}, {e}")
                del self._reconnect_tasks[connection_key]

            for connection_key, task in list(self._heartbeat_tasks.items()):
                if not task.done():
                    task.cancel()
                    try:
                        await asyncio.wait_for(task, timeout=3.0)
                    except (asyncio.CancelledError, asyncio.TimeoutError):
                        self.logger.debug(f"心跳任务已取消或超时: {connection_key}")
                    except Exception as e:
                        self.logger.error(f"停止心跳任务时出错: {connection_key}, {e}")
                del self._heartbeat_tasks[connection_key]

            for connection_key, task in list(self._health_tasks.items()):
                if not task.done():
                    task.cancel()
                    try:
                        await asyncio.wait_for(task, timeout=3.0)
                    except (asyncio.CancelledError, asyncio.TimeoutError):
                        self.logger.debug(f"Cookie健康检查任务已取消或超时: {connection_key}")
                    except Exception as e:
                        self.logger.error(f"停止Cookie健康检查任务时出错: {connection_key}, {e}")
                del self._health_tasks[connection_key]

            if self.ws:
                await self._safe_close_websocket(self.ws)
                self.ws = None

            self.logger.info("所有连接已停止")

        except Exception as e:
            self.logger.error(f"停止所有连接时发生错误: {e}")

    async def _heartbeat_loop(self, websocket, shop_id: str, user_id: str, username: str):
        """心跳检查循环"""
        connection_key = f"{shop_id}_{user_id}"
        consecutive_failures = 0

        try:
            while not (self._stop_event and self._stop_event.is_set()):
                try:
                    start_time = time.time()
                    # 加超时防止 ping 永久挂起（服务端假死时 pong 永远不回）
                    await asyncio.wait_for(
                        websocket.ping(),
                        timeout=self.heartbeat_config.heartbeat_timeout
                    )
                    response_time = time.time() - start_time
                    consecutive_failures = 0
                    self.logger.debug(f"心跳成功: {shop_id}-{username}, 响应时间: {response_time:.3f}s")
                    await asyncio.sleep(self.heartbeat_config.heartbeat_interval)

                except asyncio.TimeoutError:
                    consecutive_failures += 1
                    self.logger.warning(f"心跳超时: {shop_id}-{username}, 连续失败: {consecutive_failures}")
                    if consecutive_failures >= self.heartbeat_config.max_heartbeat_failures:
                        self.logger.error(f"心跳连续超时，主动关闭连接触发重连: {shop_id}-{username}")
                        self.status_manager.update_status(shop_id, user_id, username, ConnectionState.ERROR,
                                                          f"心跳超时: 连续{consecutive_failures}次")
                        await self._safe_close_websocket(websocket)
                        break
                    await asyncio.sleep(self.heartbeat_config.heartbeat_timeout)

                except Exception as e:
                    consecutive_failures += 1
                    self.logger.warning(f"心跳失败: {shop_id}-{username}, 错误: {str(e)}, 连续失败: {consecutive_failures}")
                    if consecutive_failures >= self.heartbeat_config.max_heartbeat_failures:
                        self.logger.error(f"心跳连续失败，主动关闭连接触发重连: {shop_id}-{username}")
                        self.status_manager.update_status(shop_id, user_id, username, ConnectionState.ERROR,
                                                          f"心跳失败: 连续{consecutive_failures}次")
                        await self._safe_close_websocket(websocket)
                        break
                    await asyncio.sleep(self.heartbeat_config.heartbeat_timeout)

        except asyncio.CancelledError:
            self.logger.debug(f"心跳循环被取消: {shop_id}-{username}")
        except Exception as e:
            self.logger.error(f"心跳循环异常: {shop_id}-{username}, 错误: {str(e)}")
        finally:
            if connection_key in self._heartbeat_tasks:
                del self._heartbeat_tasks[connection_key]
            self.logger.debug(f"心跳循环已结束: {shop_id}-{username}")

    async def _cookie_health_loop(self, shop_id: str, user_id: str, username: str):
        """Cookie 健康检查循环，定期验证 cookie 有效性并主动刷新"""
        from Channel.pinduoduo.cookie_utils import check_cookies_valid, perform_relogin
        from Channel.pinduoduo.cookie_cache import cookie_cache

        connection_key = f"{shop_id}_{user_id}"

        try:
            while not (self._stop_event and self._stop_event.is_set()):
                await asyncio.sleep(self.heartbeat_config.cookie_health_check_interval)

                if self._stop_event and self._stop_event.is_set():
                    break

                # 从共享缓存或 DB 加载当前 cookie
                cookies = cookie_cache.get("pinduoduo", shop_id, user_id)
                if not cookies:
                    account_info = db_manager.get_account("pinduoduo", shop_id, user_id)
                    if account_info:
                        cookies_data = account_info.get('cookies')
                        if isinstance(cookies_data, str):
                            import json
                            try:
                                cookies = json.loads(cookies_data)
                            except json.JSONDecodeError:
                                cookies = None
                        elif isinstance(cookies_data, dict):
                            cookies = cookies_data

                if not cookies:
                    self.logger.debug(f"无 cookie 可检查: {shop_id}-{username}")
                    continue

                # 轻量级 HTTP 验证
                is_valid = await asyncio.to_thread(
                    check_cookies_valid,
                    "pinduoduo", shop_id, user_id, cookies,
                    self.heartbeat_config.cookie_health_check_timeout,
                )

                if not is_valid:
                    self.logger.warning(
                        f"Cookie 健康检查失败: {shop_id}-{username}，触发主动重登"
                    )
                    account_info = db_manager.get_account("pinduoduo", shop_id, user_id)
                    if account_info:
                        success = await asyncio.to_thread(
                            perform_relogin,
                            "pinduoduo", shop_id, user_id,
                            account_info.get('username', username),
                            account_info.get('password', ''),
                            True,
                        )
                        if success:
                            self.logger.info(f"主动重登成功: {shop_id}-{username}")
                        else:
                            self.logger.error(f"主动重登失败: {shop_id}-{username}")
                else:
                    self.logger.debug(f"Cookie 健康检查通过: {shop_id}-{username}")

        except asyncio.CancelledError:
            self.logger.debug(f"Cookie 健康检查循环被取消: {shop_id}-{username}")
        except Exception as e:
            self.logger.error(f"Cookie 健康检查循环异常: {shop_id}-{username}, {e}")
        finally:
            if connection_key in self._health_tasks:
                del self._health_tasks[connection_key]
            self.logger.debug(f"Cookie 健康检查循环已结束: {shop_id}-{username}")

    async def _message_loop(self, websocket, shop_id: str, user_id: str, username: str, queue_name: str):
        """消息接收循环"""
        try:
            self.logger.info(f"消息循环开始: {shop_id}-{username}")

            async for message in websocket:
                if self._stop_event and self._stop_event.is_set():
                    self.logger.info(f"停止事件已设置，退出消息循环: {shop_id}-{username}")
                    break
                task = asyncio.create_task(
                    self._process_websocket_message_concurrent(
                        message, shop_id, user_id, username, queue_name
                    )
                )

                self.processing_tasks.add(task)
                task.add_done_callback(self.processing_tasks.discard)

        except ws_exceptions.ConnectionClosed as cc:
            # ConnectionClosedError 是 ConnectionClosed 的子类，统一在这里处理
            code = getattr(cc, 'code', None)
            if code == 1000 or (self._stop_event and self._stop_event.is_set()):
                self.logger.info(f"WebSocket正常关闭: {shop_id}-{username}, code={code}")
            else:
                self.logger.warning(f"WebSocket意外关闭: {shop_id}-{username}, code={code}")
        except Exception as e:
            self.logger.error(f"消息循环错误: {shop_id}-{username}, 错误: {str(e)}")

    async def _process_websocket_message_concurrent(self, message: str, shop_id: str, user_id: str, username: str, queue_name: str):
        """并发处理WebSocket消息"""
        async with self.message_semaphore:
            try:
                await self._process_websocket_message(message, shop_id, user_id, username, queue_name)
            except Exception as e:
                self.logger.error(f"并发处理消息失败: {e}")

    async def cleanup_processing_tasks(self):
        """清理所有处理任务"""
        if not self.processing_tasks:
            return

        self.logger.info(f"清理 {len(self.processing_tasks)} 个处理任务")
        for task in self.processing_tasks:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    self.logger.error(f"清理任务失败: {e}")

        self.processing_tasks.clear()

    async def _cleanup_reconnect_tasks(self):
        """清理所有重连任务"""
        try:
            for connection_key, task in list(self._reconnect_tasks.items()):
                if not task.done():
                    task.cancel()
                    try:
                        await asyncio.wait_for(task, timeout=5.0)
                    except (asyncio.CancelledError, asyncio.TimeoutError, asyncio.InvalidStateError):
                        pass
                    except RuntimeError as e:
                        if "event loop" in str(e).lower():
                            self.logger.debug(f"重连任务事件循环已关闭: {connection_key}")
                        else:
                            self.logger.error(f"清理重连任务失败: {connection_key}, {e}")
                    except Exception as e:
                        self.logger.error(f"清理重连任务失败: {connection_key}, {e}")
            self._reconnect_tasks.clear()
        except RuntimeError as e:
            if "event loop" in str(e).lower():
                self.logger.debug("清理重连任务时事件循环已关闭")
                self._reconnect_tasks.clear()
            else:
                self.logger.error(f"清理重连任务列表失败: {e}")

    async def _cleanup_heartbeat_tasks(self):
        """清理所有心跳任务"""
        try:
            for connection_key, task in list(self._heartbeat_tasks.items()):
                if not task.done():
                    task.cancel()
                    try:
                        await asyncio.wait_for(task, timeout=3.0)
                    except (asyncio.CancelledError, asyncio.TimeoutError, asyncio.InvalidStateError):
                        pass
                    except RuntimeError as e:
                        if "event loop" in str(e).lower():
                            self.logger.debug(f"心跳任务事件循环已关闭: {connection_key}")
                        else:
                            self.logger.error(f"清理心跳任务失败: {connection_key}, {e}")
                    except Exception as e:
                        self.logger.error(f"清理心跳任务失败: {connection_key}, {e}")
            self._heartbeat_tasks.clear()
        except RuntimeError as e:
            if "event loop" in str(e).lower():
                self.logger.debug("清理心跳任务时事件循环已关闭")
                self._heartbeat_tasks.clear()
            else:
                self.logger.error(f"清理心跳任务列表失败: {e}")

    async def _cleanup_health_tasks(self):
        """清理所有 Cookie 健康检查任务"""
        try:
            for connection_key, task in list(self._health_tasks.items()):
                if not task.done():
                    task.cancel()
                    try:
                        await asyncio.wait_for(task, timeout=3.0)
                    except (asyncio.CancelledError, asyncio.TimeoutError, asyncio.InvalidStateError):
                        pass
                    except RuntimeError as e:
                        if "event loop" in str(e).lower():
                            self.logger.debug(f"Cookie健康检查任务事件循环已关闭: {connection_key}")
                        else:
                            self.logger.error(f"清理Cookie健康检查任务失败: {connection_key}, {e}")
                    except Exception as e:
                        self.logger.error(f"清理Cookie健康检查任务失败: {connection_key}, {e}")
            self._health_tasks.clear()
        except RuntimeError as e:
            if "event loop" in str(e).lower():
                self.logger.debug("清理Cookie健康检查任务时事件循环已关闭")
                self._health_tasks.clear()
            else:
                self.logger.error(f"清理Cookie健康检查任务列表失败: {e}")

    async def _cleanup_ws_resources(self, queue_name: str, stop_consumer: bool = True):
        """清理WS相关资源。

        Args:
            queue_name: 消息队列名
            stop_consumer: 是否停止消息消费者（重连时=False，真正停止时=True）
        """
        from Message import message_consumer_manager

        try:
            await self.cleanup_processing_tasks()
            await self._cleanup_heartbeat_tasks()
            await self._cleanup_health_tasks()
            await self.resource_manager.cleanup_all()

            if stop_consumer:
                # 注意：不在这里清 _reconnect_tasks，避免与 stop_account 的 dict 操作冲突
                try:
                    await message_consumer_manager.stop_consumer(queue_name)
                    self.logger.debug(f"已停止消息消费者: {queue_name}")
                except (asyncio.InvalidStateError, RuntimeError) as e:
                    self.logger.debug(f"停止消息消费者时异常（忽略）: {queue_name}, {e}")
                except Exception as e:
                    self.logger.warning(f"停止消息消费者失败: {queue_name}, {e}")

            self.ws = None

        except RuntimeError as e:
            if "event loop" in str(e).lower():
                self.logger.debug(f"清理WS资源时事件循环已关闭（忽略）: {queue_name}")
                self.ws = None
            else:
                self.logger.error(f"清理WS资源失败: {e}")

    async def _cleanup_resources(self, queue_name: str):
        """完整清理所有资源（停止时调用）"""
        await self._cleanup_ws_resources(queue_name, stop_consumer=True)


# 延迟导入避免循环依赖
from database import db_manager
from core.connection_status import ConnectionState

__all__ = ['LifecycleMixin']
