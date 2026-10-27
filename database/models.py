from sqlalchemy import Column, Integer, String, Text, ForeignKey, DateTime, Boolean, UniqueConstraint, Numeric, Index
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker
from datetime import datetime
import json

Base = declarative_base()

class Channel(Base):
    """渠道表，存储电商渠道基本信息"""
    __tablename__ = 'channels'

    id = Column(Integer, primary_key=True, autoincrement=True)
    channel_name = Column(String(50), unique=True, nullable=False, comment='渠道名称')
    description = Column(String(255), comment='渠道描述')

    # 关联关系 - 一个渠道可以有多个店铺
    shops = relationship('Shop', back_populates='channel', cascade='all, delete-orphan')

    def __repr__(self):
        return f"<Channel(channel_name='{self.channel_name}')>"


class Shop(Base):
    """店铺表，存储店铺基本信息"""
    __tablename__ = 'shops'

    id = Column(Integer, primary_key=True, autoincrement=True)
    channel_id = Column(Integer, ForeignKey('channels.id'), nullable=False)
    shop_id = Column(String(100), nullable=False, comment='店铺ID')
    shop_name = Column(String(100), nullable=False, comment='店铺名称')
    shop_logo = Column(String(255), nullable=True, comment='店铺logo')
    description = Column(String(255), comment='店铺描述')

    # 关联关系 - 多个店铺属于一个渠道，一个店铺可以有多个账号
    channel = relationship('Channel', back_populates='shops')
    accounts = relationship('Account', back_populates='shop', cascade='all, delete-orphan')

    def __repr__(self):
        return f"<Shop(shop_id='{self.shop_id}', shop_name='{self.shop_name}', channel='{self.channel.channel_name if self.channel else None}')>"


class Account(Base):
    """账号表，存储店铺账号信息"""
    __tablename__ = 'accounts'

    id = Column(Integer, primary_key=True, autoincrement=True)
    shop_id = Column(Integer, ForeignKey('shops.id'), nullable=False)
    user_id = Column(String(100), nullable=False, comment='用户ID')
    username = Column(String(100), nullable=False, comment='登录用户名')
    password = Column(String(255), nullable=False, comment='登录密码')
    cookies = Column(Text, comment='存储登录cookies信息的JSON字符串')
    proxy = Column(String(255), nullable=True, comment='代理地址，格式：http://user:pass@ip:port')
    status = Column(Integer, default=None, comment='账号状态: None-未验证, 0-休息,1-在线, 3-离线')

    # 关联关系 - 多个账号属于一个店铺
    shop = relationship('Shop', back_populates='accounts')

    def __repr__(self):
        return f"<Account(username='{self.username}', shop='{self.shop.shop_name if self.shop else None}')>"


class Keyword(Base):
    """关键词表，存储关键词信息"""
    __tablename__ = 'keywords'

    id = Column(Integer, primary_key=True, autoincrement=True)
    keyword = Column(String(100), nullable=False, comment='关键词')

    def __repr__(self):
        return f"<Keyword(keyword='{self.keyword}')>"


class ProductKnowledge(Base):
    """产品知识表，存储LLM提取的商品详细知识"""
    __tablename__ = 'product_knowledge'

    id = Column(Integer, primary_key=True, autoincrement=True)
    shop_id = Column(Integer, ForeignKey('shops.id', ondelete='CASCADE'), nullable=False, comment='店铺ID')

    __table_args__ = (
        UniqueConstraint('shop_id', 'goods_id', name='uix_product_knowledge_shop_goods'),
    )
    goods_id = Column(Integer, nullable=False, comment='商品ID')
    goods_name = Column(String(255), nullable=False, comment='商品名称')
    price = Column(String(50), nullable=True, comment='价格范围（文本格式）')
    price_min = Column(Integer, nullable=True, comment='最低价（分）')
    price_max = Column(Integer, nullable=True, comment='最高价（分）')
    sold_quantity = Column(Integer, nullable=True, comment='已售数量')
    thumb_url = Column(String(500), nullable=True, comment='商品缩略图URL')
    specifications = Column(Text, nullable=True, comment='规格信息（JSON格式）')
    extracted_content = Column(Text, nullable=True, comment='LLM提取的详细产品知识')
    created_at = Column(DateTime, default=datetime.now, comment='创建时间')
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment='更新时间')
    last_extracted_at = Column(DateTime, default=datetime.now, comment='上次提取时间')

    # 关联关系
    shop = relationship('Shop', backref='product_knowledge')

    def __repr__(self):
        return f"<ProductKnowledge(goods_id='{self.goods_id}', goods_name='{self.goods_name}')>"


class CustomerServiceKnowledge(Base):
    """客服知识表，存储人工添加的客服话术和规则知识"""
    __tablename__ = 'customer_service_knowledge'

    id = Column(Integer, primary_key=True, autoincrement=True)
    shop_id = Column(Integer, ForeignKey('shops.id', ondelete='CASCADE'), nullable=False, comment='店铺ID')
    title = Column(String(255), nullable=False, comment='知识标题')
    content = Column(Text, nullable=False, comment='知识内容')
    tags = Column(String(255), nullable=True, comment='标签（逗号分隔）')
    enabled = Column(Boolean, default=True, nullable=False, comment='是否启用')
    created_at = Column(DateTime, default=datetime.now, comment='创建时间')
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment='更新时间')

    # 关联关系
    shop = relationship('Shop', backref='customer_service_knowledge')

    def __repr__(self):
        return f"<CustomerServiceKnowledge(title='{self.title}', enabled={self.enabled})>"


class Coupon(Base):
    """优惠券表，存储优惠券信息"""
    __tablename__ = 'coupons'

    id = Column(Integer, primary_key=True, autoincrement=True)
    shop_id = Column(Integer, ForeignKey('shops.id', ondelete='CASCADE'), nullable=False, comment='店铺ID')
    coupon_id = Column(String(100), nullable=False, comment='优惠券ID')
    coupon_name = Column(String(255), nullable=False, comment='优惠券名称')
    coupon_type = Column(Integer, nullable=False, comment='优惠券类型：1-满减券，2-折扣券，3-免费券')
    discount_amount = Column(Numeric(10, 2), nullable=True, comment='优惠金额（分）')
    discount_rate = Column(Numeric(5, 4), nullable=True, comment='折扣率（0-1）')
    min_order_amount = Column(Numeric(10, 2), nullable=True, comment='最低消费金额（分）')
    total_quantity = Column(Integer, nullable=False, comment='发放总数量')
    used_quantity = Column(Integer, default=0, comment='已使用数量')
    status = Column(Integer, default=1, comment='状态：0-停用，1-启用，2-已结束')
    start_time = Column(DateTime, nullable=False, comment='开始时间')
    end_time = Column(DateTime, nullable=False, comment='结束时间')
    created_at = Column(DateTime, default=datetime.now, comment='创建时间')
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment='更新时间')

    __table_args__ = (
        UniqueConstraint('shop_id', 'coupon_id', name='uix_coupon_shop_coupon'),
    )

    # 关联关系
    shop = relationship('Shop', backref='coupons')
    vouchers = relationship('Voucher', back_populates='coupon', cascade='all, delete-orphan')

    def __repr__(self):
        return f"<Coupon(coupon_id='{self.coupon_id}', coupon_name='{self.coupon_name}')>"


class Voucher(Base):
    """核销券表，存储具体的核销券实例"""
    __tablename__ = 'vouchers'

    id = Column(Integer, primary_key=True, autoincrement=True)
    coupon_id = Column(Integer, ForeignKey('coupons.id', ondelete='CASCADE'), nullable=False, comment='优惠券ID')
    voucher_code = Column(String(100), unique=True, nullable=False, comment='核销码')
    user_id = Column(String(100), nullable=True, comment='用户ID（领取后填入）')
    status = Column(Integer, default=0, comment='状态：0-未领取，1-已领取未使用，2-已使用，3-已过期')
    received_at = Column(DateTime, nullable=True, comment='领取时间')
    used_at = Column(DateTime, nullable=True, comment='使用时间')
    order_id = Column(String(100), nullable=True, comment='关联订单ID')
    created_at = Column(DateTime, default=datetime.now, comment='创建时间')
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment='更新时间')

    # 关联关系
    coupon = relationship('Coupon', back_populates='vouchers')

    def __repr__(self):
        return f"<Voucher(voucher_code='{self.voucher_code}', status={self.status})>"


class VoucherLog(Base):
    """核销记录表，存储核销操作日志"""
    __tablename__ = 'voucher_logs'

    id = Column(Integer, primary_key=True, autoincrement=True)
    voucher_id = Column(Integer, ForeignKey('vouchers.id', ondelete='CASCADE'), nullable=False, comment='核销券ID')
    operation = Column(String(50), nullable=False, comment='操作类型：create-创建，receive-领取，use-使用，expire-过期')
    operator = Column(String(100), nullable=True, comment='操作者（用户ID或系统）')
    operation_time = Column(DateTime, default=datetime.now, comment='操作时间')
    remarks = Column(String(500), nullable=True, comment='备注')

    # 关联关系
    voucher = relationship('Voucher', backref='logs')

    def __repr__(self):
        return f"<VoucherLog(voucher_id={self.voucher_id}, operation='{self.operation}')>"


class GoodsCommit(Base):
    """商品SKU表，每行对应一个SKU，存储goods_id/sku_id/规格关联关系"""
    __tablename__ = 'goods_commit'

    id = Column(Integer, primary_key=True, autoincrement=True)
    shop_id = Column(String(100), nullable=False, index=True, comment='店铺ID')
    goods_id = Column(String(100), nullable=False, index=True, comment='商品ID')
    sku_id = Column(String(100), nullable=False, comment='SKU ID')
    parent_id = Column(Integer, nullable=True, comment='规格父类ID')
    parent_name = Column(String(100), nullable=True, comment='规格父类名称')
    spec_id = Column(String(100), nullable=True, comment='规格ID')
    spec_name = Column(String(255), nullable=True, comment='规格名称')
    server_id = Column('SeverId', Integer, nullable=True, comment='GSX ServerId（安卓验机接口编码）')
    zhenxin_server_id = Column(Integer, nullable=True, comment='真心验机 ServerId（苹果验机接口编码）')
    device_type = Column(String(20), nullable=True, comment='设备类型：apple=苹果，android=安卓')
    synced_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment='同步时间')

    __table_args__ = (
        UniqueConstraint('shop_id', 'sku_id', name='uix_goods_commit_shop_sku'),
    )

    def __repr__(self):
        return f"<GoodsCommit(goods_id='{self.goods_id}', sku_id='{self.sku_id}', spec_name='{self.spec_name}')>"


class Order(Base):
    """核销订单表，记录用户提交苹果序列号+拼多多卡券后生成的订单"""
    __tablename__ = 'orders'

    id = Column(Integer, primary_key=True, autoincrement=True, comment='主键ID')
    shop_id = Column(String(100), nullable=False, index=True, comment='店铺ID，区分多租户')
    session_id = Column(String(200), nullable=False, index=True, comment='会话ID（from_uid，标识用户会话）')
    sn_imei = Column(String(100), nullable=False, comment='苹果序列号/IMEI')
    coupons = Column(String(100), nullable=False, comment='拼多多卡券号')
    sku_id = Column(String(100), nullable=True, comment='SKU ID（通过goods_id+spec匹配）')
    gsx_result = Column(Text, nullable=True, comment='GSX查询结果（JSON）')
    status = Column(Integer, default=0, nullable=False,
                    comment='状态：0-待核销，1-核销成功，2-核销失败，3-已过期')
    created_at = Column(DateTime, default=datetime.now, nullable=False, comment='创建时间')

    def __repr__(self):
        return f"<Order(id={self.id}, shop_id='{self.shop_id}', session_id='{self.session_id}', sn_imei='{self.sn_imei}', status={self.status})>"


class MessageRecord(Base):
    """聊天消息记录表，保存客户发送和客服回复的完整内容，用于会话回溯与 AI 上下文"""
    __tablename__ = 'message_records'

    id = Column(Integer, primary_key=True, autoincrement=True, comment='主键ID')
    channel = Column(String(50), nullable=False, default='pinduoduo', index=True, comment='渠道')
    shop_id = Column(String(100), nullable=False, index=True, comment='店铺ID')
    account_id = Column(String(100), nullable=True, comment='客服账号ID(user_id)')
    from_uid = Column(String(100), nullable=False, index=True, comment='客户UID')
    nickname = Column(String(100), nullable=True, comment='客户昵称')
    msg_id = Column(String(100), nullable=True, comment='平台消息ID')
    direction = Column(String(10), nullable=False, default='in', index=True, comment='消息方向: in-客户发送, out-客服回复')
    msg_type = Column(String(30), nullable=True, comment='消息类型')
    content = Column(Text, nullable=True, comment='消息内容')
    raw_data = Column(Text, nullable=True, comment='原始消息JSON')
    created_at = Column(DateTime, default=datetime.now, nullable=False, index=True, comment='记录时间')

    __table_args__ = (
        Index('ix_message_records_shop_uid_time', 'shop_id', 'from_uid', 'created_at'),
    )

    def __repr__(self):
        return f"<MessageRecord(id={self.id}, direction='{self.direction}', from_uid='{self.from_uid}', msg_type='{self.msg_type}')>"
