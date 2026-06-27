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

    async def login(self, headless=False):
        """使用账号密码登录

        Args:
            headless: 是否使用无头模式（自动回退时使用 True，避免弹出浏览器窗口）
        """
        playwright = None
        context = None
        try:
            playwright = await async_playwright().start()

            user_data_dir = str(app_dir / "user_data" / self.name)
            self.logger.debug(f"使用用户数据目录: {user_data_dir}")

            context = await playwright.chromium.launch_persistent_context(
                user_data_dir,
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

            await page.wait_for_function(
                "() => document.title === '拼多多 商家后台' || document.title === '首页' || document.title === '订单查询'",
                timeout=30000
            )

            cookies_list = await context.cookies()
            cookies_dict = {cookie.get('name', ''): cookie.get('value', '') for cookie in cookies_list if cookie.get('name')}
            cookies_json = json.dumps(cookies_dict)
            return cookies_json

        except Exception as e:
            self.logger.error(f"登录失败: {str(e)}")
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
        
    async def refresh_cookies(self):
        """重新获取cookies，使用已保存的用户数据，无需再次登录

        Returns:
            str: cookies的JSON字符串，如果失败返回False
        """
        playwright = None
        context = None
        try:
            # 启动Playwright
            playwright = await async_playwright().start()

            # 使用相同的用户数据目录（与login保持一致）
            user_data_dir = str(app_dir / "user_data" / self.name)
            self.logger.debug(f"使用用户数据目录刷新cookies: {user_data_dir}")

            # 检查用户数据目录是否存在
            if not os.path.exists(user_data_dir):
                self.logger.error(f"用户数据目录不存在: {user_data_dir}，请先登录")
                return False

            # 使用持久化上下文，自动加载用户数据
            context = await playwright.chromium.launch_persistent_context(
                user_data_dir,
                headless=True,  # 刷新cookies时可以使用无头模式
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
    
async def login_pdd(name, password, headless=False):
    """
    使用账号密码登录并返回账号、店铺信息，不直接操作数据库。
    如果登录成功，返回包含详细信息的字典。
    如果登录失败，返回 False。

    :param name: 用户名
    :param password: 密码
    :param headless: 是否使用无头模式（默认 False，自动回退时传 True）
    :return: dict or bool
    """
    pdd_login = PDDLogin(name=name, password=password)
    cookies_json = await pdd_login.login(headless=headless)
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

async def refresh_pdd_cookies(name, password=None):
    """
    刷新拼多多账号的cookies，使用已保存的用户数据，无需再次输入账号密码。
    如果刷新成功，返回包含最新cookies的字典。
    如果刷新失败（如登录状态已失效），返回 False。

    :param name: 用户名
    :param password: 密码（可选，仅用于创建PDDLogin实例）
    :return: dict or bool
    """
    pdd_login = PDDLogin(name=name, password=password or "")
    cookies_json = await pdd_login.refresh_cookies()
    
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

