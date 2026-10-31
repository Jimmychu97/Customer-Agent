"""
拼多多账号异步登录认证
"""
import os
# 必须在导入 playwright 之前设置浏览器路径
from pathlib import Path
from utils.path_utils import get_app_dir
from utils.logger_loguru import get_logger

# 设置 Playwright 浏览器路径
app_dir = get_app_dir()
browsers_path = app_dir / ".browsers"
if browsers_path.exists():
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers_path)
    logger_temp = get_logger("Pdd_login_init")
    logger_temp.info(f"设置 Playwright 浏览器路径: {browsers_path}")
else:
    # 回退到用户目录
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = os.path.join(os.getenv("LOCALAPPDATA", ""), "ms-playwright")

def _ensure_writable_temp_dir() -> None:
    """确保 Playwright 的临时目录可写（登录前置条件）

    应用可能被受限令牌 / 沙箱环境启动，这时 %TEMP% 不可写。Playwright 的 Node 驱动
    在启动浏览器时会 mkdtemp 建 playwright-artifacts-XXXXXX，失败即抛
    "EPERM: operation not permitted, mkdtemp ..."，表现为「登录失败，未能获取cookies」。
    这里先探测系统临时目录，不可写就切到项目内的可写目录（Playwright 会把
    os.environ 完整传给驱动，所以改 TMP/TEMP 即可生效）。
    """
    import tempfile

    # 不探测系统临时目录：受限 ACL 下 mkdir 可能长时间阻塞而不是立即报错，
    # 导入期卡死比“临时目录不可写”严重得多。
    # 项目目录本身一定是可写的（日志和数据库都写在这里），直接用文件探测它。
    fallback = app_dir / "temp" / "playwright"
    try:
        fallback.mkdir(parents=True, exist_ok=True)
        probe = os.path.join(str(fallback), f".write_probe_{os.getpid()}")
        with open(probe, "wb") as fh:
            fh.write(b"1")
        os.remove(probe)
    except Exception as e:
        get_logger("Pdd_login_init").error(
            f"项目内 Playwright 临时目录不可用（登录可能失败）: {e}")
        return

    global _PW_TEMP_DIR
    _PW_TEMP_DIR = str(fallback)
    get_logger("Pdd_login_init").info(f"Playwright 临时目录: {_PW_TEMP_DIR}")


_PW_TEMP_DIR = None
_ensure_writable_temp_dir()


class _PwTempEnv:
    """仅在启动 Playwright 驱动的那一刻把 TMPDIR/TEMP/TMP 指向可写目录

    Playwright 的 Node 驱动启动时会拷贝 os.environ（get_driver_env），
    之后浏览器进程继承驱动环境，所以这里临时改、立刻还原，
    不会污染应用进程里其它组件对临时目录的使用。
    """

    _KEYS = ("TMPDIR", "TEMP", "TMP")

    def __enter__(self):
        self._saved = {k: os.environ.get(k) for k in self._KEYS}
        if _PW_TEMP_DIR:
            for k in self._KEYS:
                os.environ[k] = _PW_TEMP_DIR
        return self

    def __exit__(self, *exc):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return False


async def _start_playwright():
    """启动 Playwright 驱动（使用可写的临时目录）

    注意必须在这里 await：start() 返回协程，驱动是在 await 时才 spawn 的，
    若把协程带出 with 块，环境变量已被还原，覆盖就失效了。
    """
    with _PwTempEnv():
        return await async_playwright().start()


from http import cookies
import requests
import json
import hashlib
import asyncio
from typing import Optional, Dict, Any, Tuple
import sys
from database import db_manager
from playwright.async_api import async_playwright
from Channel.pinduoduo.utils.API.get_shop_info import GetShopInfo
from Channel.pinduoduo.utils.API.get_user_info import GetUserInfo

def _parse_proxy_for_playwright(proxy: str) -> dict:
    """
    将代理 URL 转换为 Playwright 代理配置格式。

    输入: http://user:pass@ip:port 或 http://ip:port
    输出: {"server": "http://ip:port", "username": "user", "password": "pass"}
    """
    from urllib.parse import urlparse
    parsed = urlparse(proxy)
    server = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
    result = {"server": server}
    if parsed.username:
        result["username"] = parsed.username
    if parsed.password:
        result["password"] = parsed.password
    return result


class PDDLogin():
    def __init__(self,name,password):
        self.logger = get_logger("Pdd_login")
        self.channel_name = "pinduoduo"  # 渠道名称固定为"pinduoduo"
        self.base_url = "https://mms.pinduoduo.com/login"
        self.name = name
        self.password = password
    async def _dismiss_modal(self, page) -> None:
        """关闭拼多多登录页可能出现的弹窗（如协议确认、活动弹窗等）"""
        try:
            # 尝试点击弹窗中常见的确认/关闭按钮，不存在时静默跳过
            modal = page.locator("[data-testid='beast-core-modal-container']")
            if await modal.count() > 0:
                # 优先点击「确认」「我知道了」「关闭」类按钮
                for btn_text in ["确认", "我知道了", "关闭", "取消"]:
                    btn = modal.locator(f"button:has-text('{btn_text}')")
                    if await btn.count() > 0:
                        await btn.first.click()
                        self.logger.debug(f"关闭弹窗: 点击了 '{btn_text}'")
                        await page.wait_for_timeout(300)
                        return
                # 无匹配按钮时按 Escape 关闭
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(300)
        except Exception as e:
            self.logger.debug(f"关闭弹窗失败（忽略）: {e}")

    async def login(self, headless=False, proxy: str = None):
        """使用账号密码登录

        Args:
            headless: 是否使用无头模式
            proxy: 代理地址，格式 http://user:pass@ip:port（可选）
        """
        playwright = None
        context = None
        page = None
        try:
            self.logger.info(f"[登录] 启动浏览器驱动 name={self.name} headless={headless}")
            playwright = await _start_playwright()

            user_data_dir = str(app_dir / "user_data" / self.name)
            self.logger.debug(f"使用用户数据目录: {user_data_dir}")

            launch_kwargs = dict(
                headless=headless,
                args=[
                    '--disable-gpu',
                    '--no-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-blink-features=AutomationControlled',
                    '--disable-notifications',
                    '--disable-web-security',
                    '--disable-features=VizDisplayCompositor'
                ]
            )
            if proxy:
                launch_kwargs["proxy"] = _parse_proxy_for_playwright(proxy)
                self.logger.info(f"登录使用代理: {proxy}")

            context = await playwright.chromium.launch_persistent_context(
                user_data_dir, **launch_kwargs
            )
            self.logger.info("[登录] 浏览器已打开，正在打开发布页")

            page = await context.new_page()

            await page.goto(self.base_url)

            # 关闭可能遮挡「账号登录」按钮的弹窗
            await self._dismiss_modal(page)

            # 点击账号密码登录（使用 force=True 跳过可见性检查，并在超时时再次尝试关闭弹窗）
            try:
                await page.click("div.Common_item__3diIn:has-text('账号登录')", timeout=10000)
            except Exception:
                self.logger.debug("首次点击账号登录失败，再次尝试关闭弹窗后重试")
                await self._dismiss_modal(page)
                await page.click("div.Common_item__3diIn:has-text('账号登录')", timeout=15000)

            await page.wait_for_selector("input[type='text']")

            await page.fill("input[type='text']", self.name)
            await page.fill("input[type='password']", self.password)

            await page.click("button:has-text('登录')")
            self.logger.info("[登录] 已提交账号密码，等待后台跳转（最长 30 秒）")

            await page.wait_for_function(
                "() => document.title === '拼多多 商家后台' || document.title === '首页' || document.title === '订单查询'",
                timeout=30000
            )

            cookies_list = await context.cookies()
            cookies_dict = {cookie.get('name', ''): cookie.get('value', '') for cookie in cookies_list if cookie.get('name')}
            cookies_json = json.dumps(cookies_dict)
            self.logger.info(f"[登录] 登录成功 name={self.name}，已取得 {len(cookies_dict)} 个 cookie")
            return cookies_json

        except Exception as e:
            # 登录是浏览器自动化：失败必须带出「当时停在哪」，否则只剩一句无用的「登录失败」
            where = ""
            if page is not None:
                try:
                    where = f" url={page.url!r} title={(await page.title())!r}"
                except Exception:
                    where = " url=取不到"
            self.logger.opt(exception=True).error(f"[登录] 失败 name={self.name}: {e}{where}")
            return False
        finally:
            if context:
                try:
                    await context.close()
                except Exception as e:
                    self.logger.debug(f"关闭浏览器上下文失败: {e}")
            if playwright:
                try:
                    await playwright.stop()
                except Exception as e:
                    self.logger.debug(f"停止Playwright失败: {e}")
        
    async def refresh_cookies(self, proxy: str = None):
        """重新获取cookies，使用已保存的用户数据，无需再次登录

        Args:
            proxy: 代理地址，格式 http://user:pass@ip:port（可选）

        Returns:
            str: cookies的JSON字符串，如果失败返回False
        """
        playwright = None
        context = None
        try:
            playwright = await _start_playwright()

            user_data_dir = str(app_dir / "user_data" / self.name)
            self.logger.debug(f"使用用户数据目录刷新cookies: {user_data_dir}")

            if not os.path.exists(user_data_dir):
                self.logger.error(f"用户数据目录不存在: {user_data_dir}，请先登录")
                return False

            launch_kwargs = dict(
                headless=True,
                args=[
                    '--disable-gpu',
                    '--no-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-blink-features=AutomationControlled',
                    '--disable-notifications',
                    '--disable-web-security',
                    '--disable-features=VizDisplayCompositor'
                ]
            )
            if proxy:
                launch_kwargs["proxy"] = _parse_proxy_for_playwright(proxy)
                self.logger.info(f"刷新Cookie使用代理: {proxy}")

            context = await playwright.chromium.launch_persistent_context(
                user_data_dir, **launch_kwargs
            )

            page = await context.new_page()

            # 访问商家后台首页，验证登录状态
            await page.goto("https://mms.pinduoduo.com/home/")

            # 同时等待两种结果：到达首页（有效）或跳转登录页（失效）
            try:
                await page.wait_for_url(
                    lambda url: "/home" in url or "/login" in url,
                    timeout=15000
                )
            except asyncio.TimeoutError:
                self.logger.warning("页面加载超时，无法判断登录状态")
                return False

            current_url = page.url
            if "/login" in current_url:
                self.logger.warning("登录状态已失效，需要重新登录")
                return False

            # 获取最新的cookies
            cookies_list = await context.cookies()
            cookies_dict = {cookie.get('name', ''): cookie.get('value', '') for cookie in cookies_list if cookie.get('name')}
            cookies_json = json.dumps(cookies_dict)

            self.logger.info(f"成功刷新账号 '{self.name}' 的cookies")
            return cookies_json

        except Exception as e:
            self.logger.error(f"刷新cookies失败: {str(e)}")
            return False
        finally:
            if context:
                await context.close()
            if playwright:
                try:
                    await playwright.stop()
                except Exception:
                    pass

    def Set_user_info(self,cookies_json):
        user_info = GetUserInfo(cookies_json)
        result = user_info.get_user_info()
        if result is False:
            self.logger.error("获取用户信息失败")
            return None, None, None
        user_id, user_name, mall_id = result
        return user_id, user_name, mall_id

    def Set_shop_info(self,cookies_json):
        shop_info = GetShopInfo(cookies_json)
        result = shop_info.get_shop_info()
        if result is False:
            self.logger.error("获取店铺信息失败")
            return None, None, None
        shop_id, shop_name, mallLogo = result
        return shop_id, shop_name, mallLogo
    
async def login_pdd(name, password, headless=False, proxy: str = None):
    """
    使用账号密码登录并返回账号、店铺信息，不直接操作数据库。

    :param name: 用户名
    :param password: 密码
    :param headless: 是否使用无头模式
    :param proxy: 代理地址，格式 http://user:pass@ip:port（可选）
    :return: dict or bool
    """
    pdd_login = PDDLogin(name=name, password=password)
    cookies_json = await pdd_login.login(headless=headless, proxy=proxy)
    if not cookies_json:
        pdd_login.logger.error(f"账号 '{name}' 登录失败，未能获取cookies")
        return False

    try:
        # 获取用户信息和店铺信息
        user_id, user_name, mall_id = pdd_login.Set_user_info(cookies_json)
        shop_id, shop_name, mallLogo = pdd_login.Set_shop_info(cookies_json)
        
        # 检查是否成功获取到必要信息
        if user_id is None or shop_id is None:
            pdd_login.logger.error(f"账号 '{name}' 登录成功，但获取用户信息或店铺信息失败")
            return False

        pdd_login.logger.info(f"账号 '{name}' 登录成功，获取到店铺: {shop_name}({shop_id})")

        # 登录成功，返回包含所有信息的字典
        return {
            "channel_name": pdd_login.channel_name,
            "shop_id": shop_id,
            "shop_name": shop_name,
            "shop_logo": mallLogo,
            "user_id": user_id,
            "username": name,  # 使用传入的登录名
            "password": password, # 使用传入的密码
            "cookies": cookies_json,
        }
    except Exception as e:
        pdd_login.logger.error(f"账号 '{name}' 登录成功，但在处理后续信息时出错: {e}")
        return False

async def refresh_pdd_cookies(name, password=None, proxy: str = None):
    """
    刷新拼多多账号的cookies，使用已保存的用户数据，无需再次输入账号密码。

    :param name: 用户名
    :param password: 密码（可选）
    :param proxy: 代理地址，格式 http://user:pass@ip:port（可选）
    :return: dict or bool
    """
    pdd_login = PDDLogin(name=name, password=password or "")
    cookies_json = await pdd_login.refresh_cookies(proxy=proxy)
    
    if not cookies_json:
        pdd_login.logger.error(f"账号 '{name}' cookies刷新失败")
        return False

    try:
        # 获取用户信息和店铺信息
        user_id, user_name, mall_id = pdd_login.Set_user_info(cookies_json)
        shop_id, shop_name, mallLogo = pdd_login.Set_shop_info(cookies_json)
        
        # 检查是否成功获取到必要信息
        if user_id is None or shop_id is None:
            pdd_login.logger.error(f"账号 '{name}' cookies刷新成功，但获取用户信息或店铺信息失败")
            return False

        pdd_login.logger.info(f"账号 '{name}' cookies刷新成功，店铺: {shop_name}({shop_id})")

        # 刷新成功，返回包含最新信息的字典
        return {
            "channel_name": pdd_login.channel_name,
            "shop_id": shop_id,
            "shop_name": shop_name,
            "shop_logo": mallLogo,
            "user_id": user_id,
            "username": name,
            "password": password or "",
            "cookies": cookies_json,
        }
    except Exception as e:
        pdd_login.logger.error(f"账号 '{name}' cookies刷新成功，但在处理后续信息时出错: {e}")
        return False

