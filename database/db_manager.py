import os
import json
from datetime import datetime, timedelta
from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.exc import SQLAlchemyError
from typing import List, Dict, Any, Optional, Union, Generator
from utils.logger_loguru import get_logger
from database.models import Base, Channel, Shop, Account, Keyword, MessageRecord

# 客服侧（direction='out'）消息的内容级去重窗口（秒）：
# 覆盖"程序自己发出的消息被平台回推"的情况（例如 AI 回复以 mall_cs 角色回推），
# 这种回推的 msg_id 与本地记录不同，只能靠内容 + 时间窗口兜底去重。
OUTGOING_DEDUP_SECONDS = 120


class DatabaseManager:
    """数据库管理类，提供数据库操作的封装

    单例管理：通过 DI 容器注册为单例（推荐方式）。
    也支持通过 get_db_manager() 函数获取单例实例。
    """

    def __init__(self, db_path: str = './temp/channel_shop.db'):
        """初始化数据库连接

        Args:
            db_path: 数据库文件路径
        """
        # 确保数据库目录存在
        os.makedirs(os.path.dirname(db_path), exist_ok=True)

        # 创建数据库引擎
        self.engine = create_engine(f'sqlite:///{db_path}')
        self.Session = sessionmaker(bind=self.engine)

        # 创建表结构
        Base.metadata.create_all(self.engine)

        self.logger = get_logger()

        # 迁移：为旧数据库补充 proxy 列
        self._migrate_add_proxy_column()

        # 迁移：为旧数据库补充服务模式相关列
        self._migrate_add_service_mode_columns()

        self.init_db()

    def _migrate_add_proxy_column(self):
        """为旧数据库的 accounts 表补充 proxy 列（幂等）"""
        try:
            with self.engine.connect() as conn:
                cols = [row[1] for row in conn.execute(
                    __import__('sqlalchemy').text("PRAGMA table_info(accounts)")
                )]
                if 'proxy' not in cols:
                    conn.execute(__import__('sqlalchemy').text(
                        "ALTER TABLE accounts ADD COLUMN proxy VARCHAR(255)"
                    ))
                    conn.commit()
        except Exception:
            pass

    def _migrate_add_service_mode_columns(self):
        """为旧数据库补充服务模式相关列（幂等）

        分别检查 product_knowledge.service_mode、orders.order_sn、
        orders.service_mode 是否存在，缺失才执行 ALTER TABLE ADD COLUMN。
        SQLite 对新增可空列是安全的，不会重写既有数据。
        """
        try:
            from sqlalchemy import text

            targets = [
                ('product_knowledge', 'service_mode', 'INTEGER NOT NULL DEFAULT 0'),
                ('orders', 'order_sn', 'VARCHAR(100)'),
                ('orders', 'service_mode', 'INTEGER'),
            ]
            with self.engine.connect() as conn:
                for table, column, ddl in targets:
                    cols = [row[1] for row in conn.execute(
                        text(f"PRAGMA table_info({table})")
                    )]
                    if column not in cols:
                        conn.execute(text(
                            f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"
                        ))
                        conn.commit()
                        self.logger.info(f"迁移：{table} 表已添加列 {column}")
        except Exception as e:
            self.logger.warning(f"服务模式列迁移失败（忽略）: {e}")

    def init_db(self):
        """初始化渠道信息"""
        channel_name = "pinduoduo"
        description = "拼多多"
        self.add_channel(channel_name, description)


    def get_session(self):
        """获取数据库会话"""
        return self.Session()

    @contextmanager
    def session_scope(self) -> Generator[Session, None, None]:
        """数据库会话上下文管理器，自动处理 commit/rollback/close"""
        session = self.Session()
        try:
            yield session
            session.commit()
        except SQLAlchemyError as e:
            session.rollback()
            self.logger.error(f"数据库操作失败: {str(e)}")
            raise
        finally:
            session.close()

    # ==================== 私有辅助方法 ====================
    def _get_channel(self, session: Session, channel_name: str) -> Optional[Channel]:
        """获取渠道对象"""
        return session.query(Channel).filter(Channel.channel_name == channel_name).first()

    def _get_shop(self, session: Session, channel: Channel, shop_id: str) -> Optional[Shop]:
        """获取店铺对象"""
        return session.query(Shop).filter(
            Shop.channel_id == channel.id,
            Shop.shop_id == shop_id
        ).first()

    def _get_account_by_user_id(self, session: Session, shop: Shop, user_id: str) -> Optional[Account]:
        """通过user_id获取账号对象"""
        return session.query(Account).filter(
            Account.shop_id == shop.id,
            Account.user_id == user_id
        ).first()

    def _get_account_by_username(self, session: Session, shop: Shop, username: str) -> Optional[Account]:
        """通过username获取账号对象"""
        return session.query(Account).filter(
            Account.shop_id == shop.id,
            Account.username == username
        ).first()

    # ==================== 渠道相关操作 ====================
    def add_channel(self, channel_name: str, description: str = None) -> bool:
        """添加渠道"""
        with self.session_scope() as session:
            existing = session.query(Channel).filter(Channel.channel_name == channel_name).first()
            if existing:
                return True
            channel = Channel(channel_name=channel_name, description=description)
            session.add(channel)
            return True

    def get_channel(self, channel_name: str) -> Optional[Dict[str, Any]]:
        """获取渠道信息"""
        with self.session_scope() as session:
            channel = session.query(Channel).filter(Channel.channel_name == channel_name).first()
            if not channel:
                return None
            return {
                'id': channel.id,
                'channel_name': channel.channel_name,
                'description': channel.description
            }

    def get_all_channels(self) -> List[Dict[str, Any]]:
        """获取所有渠道"""
        with self.session_scope() as session:
            channels = session.query(Channel).all()
            return [
                {
                    'id': channel.id,
                    'channel_name': channel.channel_name,
                    'description': channel.description
                }
                for channel in channels
            ]

    def delete_channel(self, channel_name: str) -> bool:
        """删除渠道"""
        with self.session_scope() as session:
            channel = session.query(Channel).filter(Channel.channel_name == channel_name).first()
            if not channel:
                self.logger.warning(f"渠道 {channel_name} 不存在")
                return False
            session.delete(channel)
            self.logger.info(f"成功删除渠道: {channel_name}")
            return True
    
    # 店铺相关操作
    def add_shop(self, channel_name: str, shop_id: str, shop_name: str, shop_logo: str, description: str = None) -> bool:
        """添加店铺"""
        with self.session_scope() as session:
            channel = session.query(Channel).filter(Channel.channel_name == channel_name).first()
            if not channel:
                self.logger.error(f"添加店铺失败: 渠道 {channel_name} 不存在")
                return False
            existing = session.query(Shop).filter(
                Shop.channel_id == channel.id,
                Shop.shop_id == shop_id
            ).first()
            if existing:
                self.logger.warning(f"店铺 {shop_id} 已存在于渠道 {channel_name}")
                return False
            shop = Shop(
                channel_id=channel.id,
                shop_id=shop_id,
                shop_name=shop_name,
                shop_logo=shop_logo,
                description=description
            )
            session.add(shop)
            self.logger.info(f"成功添加店铺: {shop_name}({shop_id}) 到渠道 {channel_name}")
            return True

    def get_shop(self, channel_name: str, shop_id: str) -> Optional[Dict[str, Any]]:
        """获取店铺信息"""
        with self.session_scope() as session:
            channel = self._get_channel(session, channel_name)
            if not channel:
                return None
            shop = self._get_shop(session, channel, shop_id)
            if not shop:
                return None
            return {
                'id': shop.id,
                'channel_id': shop.channel_id,
                'channel_name': channel_name,
                'shop_id': shop.shop_id,
                'shop_name': shop.shop_name,
                'shop_logo': shop.shop_logo,
                'description': shop.description,
            }

    def get_shops_by_channel(self, channel_name: str) -> List[Dict[str, Any]]:
        """获取指定渠道下的所有店铺"""
        with self.session_scope() as session:
            channel = self._get_channel(session, channel_name)
            if not channel:
                return []
            shops = session.query(Shop).filter(Shop.channel_id == channel.id).all()
            return [
                {
                    'id': shop.id,
                    'channel_id': shop.channel_id,
                    'channel_name': channel_name,
                    'shop_id': shop.shop_id,
                    'shop_name': shop.shop_name,
                    'shop_logo': shop.shop_logo,
                    'description': shop.description
                }
                for shop in shops
            ]

    def update_shop_info(self, channel_name: str, shop_id: str, shop_name: str = None, shop_logo: str = None, description: str = None) -> bool:
        """更新店铺信息"""
        with self.session_scope() as session:
            channel = self._get_channel(session, channel_name)
            if not channel:
                return False
            shop = self._get_shop(session, channel, shop_id)
            if not shop:
                return False
            if shop_name is not None:
                shop.shop_name = shop_name
            if shop_logo is not None:
                shop.shop_logo = shop_logo
            if description is not None:
                shop.description = description
            return True

    def delete_shop(self, channel_name: str, shop_id: str) -> bool:
        """删除店铺"""
        with self.session_scope() as session:
            channel = self._get_channel(session, channel_name)
            if not channel:
                return False
            shop = self._get_shop(session, channel, shop_id)
            if not shop:
                return False
            session.delete(shop)
            return True

    # ==================== 账号相关操作 ====================
    def add_account(self, channel_name: str, shop_id: str, user_id: str, username: str, password: str, cookies: str = None, proxy: str = None) -> bool:
        """添加账号"""
        with self.session_scope() as session:
            channel = self._get_channel(session, channel_name)
            if not channel:
                self.logger.error(f"添加账号失败: 渠道 {channel_name} 不存在")
                return False
            shop = self._get_shop(session, channel, shop_id)
            if not shop:
                self.logger.error(f"添加账号失败: 店铺 {shop_id} 不存在")
                return False
            existing = self._get_account_by_username(session, shop, username)
            if existing:
                self.logger.warning(f"账号 {username} 已存在于店铺 {shop_id}")
                return False
            account = Account(
                shop_id=shop.id,
                user_id=user_id,
                username=username,
                password=password,
                cookies=cookies,
                proxy=proxy,
                status=None
            )
            session.add(account)
            self.logger.info(f"成功添加账号: {username} 到店铺 {shop_id}")
            return True

    def get_account(self, channel_name: str, shop_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        """获取账号信息"""
        with self.session_scope() as session:
            channel = self._get_channel(session, channel_name)
            if not channel:
                self.logger.warning(f"未找到渠道: {channel_name}")
                return None
            shop = self._get_shop(session, channel, shop_id)
            if not shop:
                self.logger.warning(f"未找到店铺: {shop_id} (渠道: {channel_name})")
                return None
            account = self._get_account_by_user_id(session, shop, user_id)
            if not account:
                self.logger.warning(f"未找到账户: {user_id} (店铺 ID: {shop_id})")
                return None
            return {
                'id': account.id,
                'shop_id': account.shop_id,
                'user_id': account.user_id,
                'username': account.username,
                'password': account.password,
                'cookies': account.cookies,
                'proxy': account.proxy,
                'status': account.status
            }

    _UNSET = object()

    def update_account_info(self, channel_name: str, shop_id: str, user_id: str, username: Optional[str] = None, password: Optional[str] = None, cookies: Optional[str] = None, proxy: object = _UNSET, status: Optional[int] = None) -> bool:
        """更新账号信息"""
        with self.session_scope() as session:
            channel = self._get_channel(session, channel_name)
            if not channel:
                self.logger.error(f"更新账号失败: 渠道 {channel_name} 不存在")
                return False
            shop = self._get_shop(session, channel, shop_id)
            if not shop:
                self.logger.error(f"更新账号失败: 店铺 {shop_id} 不存在于渠道 {channel_name}")
                return False
            account = self._get_account_by_user_id(session, shop, user_id)
            if not account:
                self.logger.error(f"更新账号失败: 账号 {user_id} 不存在于店铺 {shop_id}")
                return False
            if username is not None:
                account.username = username
            if password is not None:
                account.password = password
            if cookies is not None:
                account.cookies = cookies
            if proxy is not DatabaseManager._UNSET:
                account.proxy = proxy or None
            if status is not None:
                account.status = status
            self.logger.info(f"成功更新账号信息: {username} (用户ID: {user_id})")
            return True

    def get_accounts_by_shop(self, channel_name: str, shop_id: str) -> List[Dict[str, Any]]:
        """获取指定店铺下的所有账号"""
        with self.session_scope() as session:
            channel = self._get_channel(session, channel_name)
            if not channel:
                return []
            shop = self._get_shop(session, channel, shop_id)
            if not shop:
                return []
            accounts = session.query(Account).filter(Account.shop_id == shop.id).all()
            return [
                {
                    'id': account.id,
                    'shop_id': account.shop_id,
                    'user_id': account.user_id,
                    'username': account.username,
                    'password': account.password,
                    'cookies': account.cookies,
                    'proxy': account.proxy,
                    'status': account.status
                }
                for account in accounts
            ]

    def get_all_accounts_with_details(self) -> List[Dict[str, Any]]:
        """
        批量获取所有账号及其关联的店铺和渠道信息（减少N+1查询）
        """
        with self.session_scope() as session:
            results = (
                session.query(Account, Shop, Channel)
                .join(Shop, Account.shop_id == Shop.id)
                .join(Channel, Shop.channel_id == Channel.id)
                .all()
            )
            return [
                {
                    'channel_name': channel.channel_name,
                    'shop_id': shop.shop_id,
                    'shop_name': shop.shop_name,
                    'shop_logo': shop.shop_logo,
                    'username': account.username,
                    'password': account.password,
                    'status': account.status,
                    'user_id': account.user_id,
                    'cookies': account.cookies,
                    'proxy': account.proxy,
                }
                for account, shop, channel in results
            ]

    def update_account_status(self, channel_name: str, shop_id: str, user_id: str, status: int) -> bool:
        """更新账号状态"""
        with self.session_scope() as session:
            channel = self._get_channel(session, channel_name)
            if not channel:
                return False
            shop = self._get_shop(session, channel, shop_id)
            if not shop:
                return False
            account = self._get_account_by_user_id(session, shop, user_id)
            if not account:
                return False
            account.status = status
            return True

    def update_account_cookies(self, channel_name: str, shop_id: str, user_id: str, cookies: str) -> bool:
        """更新账号cookies"""
        with self.session_scope() as session:
            channel = self._get_channel(session, channel_name)
            if not channel:
                return False
            shop = self._get_shop(session, channel, shop_id)
            if not shop:
                return False
            account = self._get_account_by_user_id(session, shop, user_id)
            if not account:
                return False
            account.cookies = cookies
            return True

    def delete_account(self, channel_name: str, shop_id: str, user_id: str) -> bool:
        """删除账号"""
        with self.session_scope() as session:
            channel = self._get_channel(session, channel_name)
            if not channel:
                return False
            shop = self._get_shop(session, channel, shop_id)
            if not shop:
                return False
            account = self._get_account_by_user_id(session, shop, user_id)
            if not account:
                return False
            session.delete(account)
            return True

    # 关键词相关操作
    def add_keyword(self, keyword: str) -> bool:
        """添加关键词"""
        with self.session_scope() as session:
            existing = session.query(Keyword).filter(Keyword.keyword == keyword).first()
            if existing:
                self.logger.warning(f"关键词 {keyword} 已存在")
                return False
            keyword_obj = Keyword(keyword=keyword)
            session.add(keyword_obj)
            self.logger.info(f"成功添加关键词: {keyword}")
            return True

    def get_keyword(self, keyword: str) -> Optional[Dict[str, Any]]:
        """获取关键词信息"""
        with self.session_scope() as session:
            keyword_obj = session.query(Keyword).filter(Keyword.keyword == keyword).first()
            if not keyword_obj:
                return None
            return {
                'id': keyword_obj.id,
                'keyword': keyword_obj.keyword
            }

    def get_all_keywords(self) -> List[Dict[str, Any]]:
        """获取所有关键词"""
        with self.session_scope() as session:
            keywords = session.query(Keyword).all()
            return [
                {
                    'id': keyword.id,
                    'keyword': keyword.keyword
                }
                for keyword in keywords
            ]

    def update_keyword(self, old_keyword: str, new_keyword: str) -> bool:
        """更新关键词"""
        with self.session_scope() as session:
            keyword_obj = session.query(Keyword).filter(Keyword.keyword == old_keyword).first()
            if not keyword_obj:
                self.logger.warning(f"关键词 {old_keyword} 不存在")
                return False
            if old_keyword != new_keyword:
                existing = session.query(Keyword).filter(Keyword.keyword == new_keyword).first()
                if existing:
                    self.logger.warning(f"关键词 {new_keyword} 已存在")
                    return False
            keyword_obj.keyword = new_keyword
            self.logger.info(f"成功更新关键词: {old_keyword} -> {new_keyword}")
            return True

    def delete_keyword(self, keyword: str) -> bool:
        """删除关键词"""
        with self.session_scope() as session:
            keyword_obj = session.query(Keyword).filter(Keyword.keyword == keyword).first()
            if not keyword_obj:
                self.logger.warning(f"关键词 {keyword} 不存在")
                return False
            session.delete(keyword_obj)
            self.logger.info(f"成功删除关键词: {keyword}")
            return True

    # ==================== 消息记录相关操作 ====================
    def save_message(self, channel: str = "pinduoduo", shop_id: str = None, account_id: str = None,
                     from_uid: str = None, nickname: str = None, msg_id: str = None,
                     direction: str = "in", msg_type: str = None, content: str = None,
                     raw_data: str = None) -> bool:
        """保存一条聊天消息记录（客户发送或客服回复）

        去重规则（返回 True 表示"已入库或已存在"）：
        1. 同 shop_id + from_uid + msg_id 已存在 -> 跳过（平台重复推送）
        2. direction='out' 且内容完全相同、且发生在 OUTGOING_DEDUP_SECONDS 秒内 -> 跳过
           （覆盖"程序自己发出的消息被平台回推"，此时 msg_id 不同）
        """
        try:
            with self.session_scope() as session:
                if self._is_duplicate_message(
                    session,
                    shop_id=shop_id,
                    from_uid=from_uid,
                    direction=direction,
                    msg_id=msg_id,
                    content=content,
                ):
                    self.logger.info(
                        f"[消息记录] 重复消息已跳过 direction={direction} msg_type={msg_type} "
                        f"from_uid={from_uid} msg_id={msg_id}"
                    )
                    return True
                record = MessageRecord(
                    channel=channel or "pinduoduo",
                    shop_id=str(shop_id) if shop_id is not None else "",
                    account_id=str(account_id) if account_id is not None else None,
                    from_uid=str(from_uid) if from_uid is not None else "",
                    nickname=str(nickname) if nickname is not None else None,
                    msg_id=str(msg_id) if msg_id is not None else None,
                    direction=direction or "in",
                    msg_type=str(msg_type) if msg_type is not None else None,
                    content=content,
                    raw_data=raw_data,
                )
                session.add(record)
            return True
        except Exception as e:
            self.logger.error(f"保存消息记录失败: {e}")
            return False

    def _is_duplicate_message(self, session, shop_id: str = None, from_uid: str = None,
                              direction: str = "in", msg_id: str = None,
                              content: str = None) -> bool:
        """判断一条消息是否已经在库（需传入已打开的 session）"""
        try:
            query = session.query(MessageRecord.id).filter(
                MessageRecord.shop_id == (str(shop_id) if shop_id is not None else ""),
                MessageRecord.from_uid == (str(from_uid) if from_uid is not None else ""),
            )
            if msg_id:
                if query.filter(MessageRecord.msg_id == str(msg_id)).first():
                    return True
            if direction == "out" and content:
                since = datetime.now() - timedelta(seconds=OUTGOING_DEDUP_SECONDS)
                if query.filter(
                    MessageRecord.direction == "out",
                    MessageRecord.content == content,
                    MessageRecord.created_at >= since,
                ).first():
                    return True
            return False
        except Exception as e:
            self.logger.debug(f"消息去重检查失败: {e}")
            return False

    def message_exists(self, shop_id: str = None, from_uid: str = None, direction: str = None,
                       msg_id: str = None, content: str = None,
                       within_seconds: int = None) -> bool:
        """只读查询：是否存在符合条件的消息记录"""
        with self.session_scope() as session:
            query = session.query(MessageRecord.id)
            if shop_id is not None:
                query = query.filter(MessageRecord.shop_id == str(shop_id))
            if from_uid is not None:
                query = query.filter(MessageRecord.from_uid == str(from_uid))
            if direction:
                query = query.filter(MessageRecord.direction == direction)
            if msg_id:
                query = query.filter(MessageRecord.msg_id == str(msg_id))
            if content is not None:
                query = query.filter(MessageRecord.content == content)
            if within_seconds:
                since = datetime.now() - timedelta(seconds=int(within_seconds))
                query = query.filter(MessageRecord.created_at >= since)
            return query.first() is not None

    def get_recent_messages(self, shop_id: str, from_uid: str, limit: int = 50,
                            direction: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取指定店铺下某客户最近的聊天消息（按时间升序返回）"""
        with self.session_scope() as session:
            query = session.query(MessageRecord).filter(
                MessageRecord.shop_id == str(shop_id),
                MessageRecord.from_uid == str(from_uid),
            )
            if direction:
                query = query.filter(MessageRecord.direction == direction)
            records = query.order_by(MessageRecord.created_at.desc(), MessageRecord.id.desc()).limit(limit).all()
            records = list(reversed(records))
            return [
                {
                    'id': r.id,
                    'channel': r.channel,
                    'shop_id': r.shop_id,
                    'account_id': r.account_id,
                    'from_uid': r.from_uid,
                    'nickname': r.nickname,
                    'msg_id': r.msg_id,
                    'direction': r.direction,
                    'msg_type': r.msg_type,
                    'content': r.content,
                    'created_at': r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else None,
                }
                for r in records
            ]

    def count_messages(self, shop_id: str = None, from_uid: str = None) -> int:
        """统计消息记录条数（可按店铺/客户过滤）"""
        with self.session_scope() as session:
            query = session.query(MessageRecord)
            if shop_id:
                query = query.filter(MessageRecord.shop_id == str(shop_id))
            if from_uid:
                query = query.filter(MessageRecord.from_uid == str(from_uid))
            return query.count()

_db_instance: Optional["DatabaseManager"] = None

def get_db_manager() -> "DatabaseManager":
    """获取 DatabaseManager 单例。

    优先从 DI 容器获取；若 DI 尚未注册（启动早期），回退到本地单例，
    以确保全应用始终共用同一个实例，避免出现多个 db 文件分裂。
    """
    global _db_instance
    try:
        from core.di_container import container
        if container.is_registered(DatabaseManager):
            return container.get(DatabaseManager)
    except ImportError:
        pass

    if _db_instance is None:
        _db_instance = DatabaseManager()
    return _db_instance

class _LazyDBProxy:
    """延迟代理，用于兼容旧代码的全局 db_manager 实例，底层共用 DI 容器。"""

    def __getattr__(self, name: str):
        return getattr(get_db_manager(), name)

db_manager = _LazyDBProxy()
