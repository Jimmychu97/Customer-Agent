"""
拼多多核销券管理API

基于拼多多开放平台API实现核销功能：
- 优惠券创建与管理
- 核销码生成与验证
- 核销记录查询
"""
from ..base_request import BaseRequest
from typing import Dict, List, Optional, Any
import json
import secrets
import string
from datetime import datetime, timedelta
from database import db_manager
from database.models import Coupon, Voucher, VoucherLog
from utils.logger_loguru import get_logger


class VoucherManager(BaseRequest):
    """
    拼多多核销券管理器
    提供优惠券创建、核销码生成、核销验证等功能
    """

    def __init__(self, shop_id: str = None, user_id: str = None, cookies=None):
        """
        初始化核销券管理器

        Args:
            shop_id: 店铺ID，用于从数据库获取cookies
            user_id: 用户ID，用于从数据库获取cookies
            cookies: 登录cookies，如果直接传入则不需要从数据库获取
        """
        super().__init__(shop_id=shop_id, user_id=user_id)
        if cookies:
            self.update_cookies(cookies)
        self.logger = get_logger("VoucherManager")

    def create_coupon(self, coupon_name: str, coupon_type: int, total_quantity: int,
                     discount_amount: float = None, discount_rate: float = None,
                     min_order_amount: float = None, valid_days: int = 30) -> Dict[str, Any]:
        """
        创建优惠券

        Args:
            coupon_name: 优惠券名称
            coupon_type: 优惠券类型：1-满减券，2-折扣券，3-免费券
            total_quantity: 发放总数量
            discount_amount: 优惠金额（满减券使用）
            discount_rate: 折扣率（0-1，折扣券使用）
            min_order_amount: 最低消费金额
            valid_days: 有效天数

        Returns:
            dict: 创建结果
        """
        try:
            # 验证参数
            if coupon_type == 1 and not discount_amount:
                return {"success": False, "error_msg": "满减券必须设置优惠金额"}
            if coupon_type == 2 and not discount_rate:
                return {"success": False, "error_msg": "折扣券必须设置折扣率"}

            # 生成优惠券ID
            coupon_id = self._generate_coupon_id()
            
            # 计算有效期
            start_time = datetime.now()
            end_time = start_time + timedelta(days=valid_days)

            # 创建优惠券记录
            with db_manager.get_session() as session:
                # 获取店铺信息
                shop_info = db_manager.get_shop_by_shop_id("pinduoduo", self.shop_id)
                if not shop_info:
                    return {"success": False, "error_msg": "店铺信息不存在"}

                # 创建优惠券
                coupon = Coupon(
                    shop_id=shop_info['id'],
                    coupon_id=coupon_id,
                    coupon_name=coupon_name,
                    coupon_type=coupon_type,
                    discount_amount=discount_amount,
                    discount_rate=discount_rate,
                    min_order_amount=min_order_amount,
                    total_quantity=total_quantity,
                    used_quantity=0,
                    status=1,
                    start_time=start_time,
                    end_time=end_time
                )
                session.add(coupon)
                session.commit()

                # 批量生成核销码
                self._generate_voucher_codes(session, coupon.id, total_quantity)

                self.logger.info(f"优惠券创建成功: {coupon_name}, ID: {coupon_id}")
                return {
                    "success": True,
                    "coupon_id": coupon_id,
                    "coupon_name": coupon_name,
                    "total_quantity": total_quantity
                }

        except Exception as e:
            self.logger.error(f"创建优惠券失败: {str(e)}")
            return {"success": False, "error_msg": str(e)}

    def get_voucher_code(self, coupon_id: str, user_id: str) -> Dict[str, Any]:
        """
        为用户生成核销码

        Args:
            coupon_id: 优惠券ID
            user_id: 用户ID

        Returns:
            dict: 核销码信息
        """
        try:
            with db_manager.get_session() as session:
                # 查找优惠券
                coupon = session.query(Coupon).filter_by(coupon_id=coupon_id).first()
                if not coupon:
                    return {"success": False, "error_msg": "优惠券不存在"}

                # 检查优惠券状态
                if coupon.status != 1:
                    return {"success": False, "error_msg": "优惠券已停用或已结束"}

                # 检查有效期
                now = datetime.now()
                if now < coupon.start_time or now > coupon.end_time:
                    return {"success": False, "error_msg": "优惠券不在有效期内"}

                # 查找未领取的核销码
                available_voucher = session.query(Voucher).filter_by(
                    coupon_id=coupon.id,
                    status=0  # 未领取
                ).first()

                if not available_voucher:
                    return {"success": False, "error_msg": "优惠券已领完"}

                # 分配给用户
                available_voucher.user_id = user_id
                available_voucher.status = 1  # 已领取未使用
                available_voucher.received_at = datetime.now()

                # 记录操作日志
                log = VoucherLog(
                    voucher_id=available_voucher.id,
                    operation="receive",
                    operator=user_id,
                    remarks=f"用户{user_id}领取优惠券"
                )
                session.add(log)
                session.commit()

                self.logger.info(f"用户{user_id}成功获取核销码: {available_voucher.voucher_code}")
                return {
                    "success": True,
                    "voucher_code": available_voucher.voucher_code,
                    "coupon_name": coupon.coupon_name,
                    "discount_amount": float(coupon.discount_amount) if coupon.discount_amount else None,
                    "discount_rate": float(coupon.discount_rate) if coupon.discount_rate else None,
                    "end_time": coupon.end_time.isoformat()
                }

        except Exception as e:
            self.logger.error(f"获取核销码失败: {str(e)}")
            return {"success": False, "error_msg": str(e)}

    def verify_voucher(self, voucher_code: str, order_amount: float = None) -> Dict[str, Any]:
        """
        验证核销码

        Args:
            voucher_code: 核销码
            order_amount: 订单金额（用于满减券验证）

        Returns:
            dict: 验证结果
        """
        try:
            with db_manager.get_session() as session:
                # 查找核销码
                voucher = session.query(Voucher).filter_by(voucher_code=voucher_code).first()
                if not voucher:
                    return {"success": False, "error_msg": "核销码不存在"}

                # 检查状态
                if voucher.status != 1:
                    if voucher.status == 0:
                        return {"success": False, "error_msg": "核销码未领取"}
                    elif voucher.status == 2:
                        return {"success": False, "error_msg": "核销码已使用"}
                    elif voucher.status == 3:
                        return {"success": False, "error_msg": "核销码已过期"}

                # 获取优惠券信息
                coupon = voucher.coupon
                
                # 检查优惠券有效期
                now = datetime.now()
                if now > coupon.end_time:
                    voucher.status = 3  # 标记为过期
                    session.commit()
                    return {"success": False, "error_msg": "优惠券已过期"}

                # 验证订单金额（满减券）
                if coupon.min_order_amount and order_amount:
                    if order_amount < float(coupon.min_order_amount):
                        return {
                            "success": False, 
                            "error_msg": f"订单金额不足，最低需要{float(coupon.min_order_amount)}元"
                        }

                # 计算优惠金额
                discount = self._calculate_discount(coupon, order_amount)

                return {
                    "success": True,
                    "voucher_code": voucher_code,
                    "coupon_name": coupon.coupon_name,
                    "coupon_type": coupon.coupon_type,
                    "discount_amount": discount,
                    "user_id": voucher.user_id,
                    "can_use": True
                }

        except Exception as e:
            self.logger.error(f"验证核销码失败: {str(e)}")
            return {"success": False, "error_msg": str(e)}

    def use_voucher(self, voucher_code: str, order_id: str, order_amount: float = None) -> Dict[str, Any]:
        """
        使用核销码

        Args:
            voucher_code: 核销码
            order_id: 订单ID
            order_amount: 订单金额

        Returns:
            dict: 使用结果
        """
        try:
            with db_manager.get_session() as session:
                # 先验证核销码
                verify_result = self.verify_voucher(voucher_code, order_amount)
                if not verify_result["success"]:
                    return verify_result

                # 查找核销码
                voucher = session.query(Voucher).filter_by(voucher_code=voucher_code).first()
                
                # 标记为已使用
                voucher.status = 2
                voucher.used_at = datetime.now()
                voucher.order_id = order_id

                # 更新优惠券使用统计
                coupon = voucher.coupon
                coupon.used_quantity = (coupon.used_quantity or 0) + 1

                # 记录操作日志
                log = VoucherLog(
                    voucher_id=voucher.id,
                    operation="use",
                    operator=voucher.user_id,
                    remarks=f"订单{order_id}使用优惠券"
                )
                session.add(log)
                session.commit()

                discount = self._calculate_discount(coupon, order_amount)

                self.logger.info(f"核销码使用成功: {voucher_code}, 订单: {order_id}")
                return {
                    "success": True,
                    "voucher_code": voucher_code,
                    "discount_amount": discount,
                    "order_id": order_id
                }

        except Exception as e:
            self.logger.error(f"使用核销码失败: {str(e)}")
            return {"success": False, "error_msg": str(e)}

    def get_coupon_list(self, status: int = None) -> Dict[str, Any]:
        """
        获取优惠券列表

        Args:
            status: 优惠券状态筛选

        Returns:
            dict: 优惠券列表
        """
        try:
            with db_manager.get_session() as session:
                # 获取店铺信息
                shop_info = db_manager.get_shop_by_shop_id("pinduoduo", self.shop_id)
                if not shop_info:
                    return {"success": False, "error_msg": "店铺信息不存在"}

                query = session.query(Coupon).filter_by(shop_id=shop_info['id'])
                if status is not None:
                    query = query.filter_by(status=status)
                
                coupons = query.order_by(Coupon.created_at.desc()).all()

                coupon_list = []
                for coupon in coupons:
                    coupon_data = {
                        "coupon_id": coupon.coupon_id,
                        "coupon_name": coupon.coupon_name,
                        "coupon_type": coupon.coupon_type,
                        "total_quantity": coupon.total_quantity,
                        "used_quantity": coupon.used_quantity,
                        "remaining_quantity": coupon.total_quantity - (coupon.used_quantity or 0),
                        "status": coupon.status,
                        "start_time": coupon.start_time.isoformat(),
                        "end_time": coupon.end_time.isoformat(),
                        "created_at": coupon.created_at.isoformat()
                    }
                    
                    if coupon.discount_amount:
                        coupon_data["discount_amount"] = float(coupon.discount_amount)
                    if coupon.discount_rate:
                        coupon_data["discount_rate"] = float(coupon.discount_rate)
                    if coupon.min_order_amount:
                        coupon_data["min_order_amount"] = float(coupon.min_order_amount)
                    
                    coupon_list.append(coupon_data)

                return {
                    "success": True,
                    "coupons": coupon_list,
                    "total": len(coupon_list)
                }

        except Exception as e:
            self.logger.error(f"获取优惠券列表失败: {str(e)}")
            return {"success": False, "error_msg": str(e)}

    def get_voucher_usage_stats(self, coupon_id: str) -> Dict[str, Any]:
        """
        获取核销券使用统计

        Args:
            coupon_id: 优惠券ID

        Returns:
            dict: 使用统计
        """
        try:
            with db_manager.get_session() as session:
                coupon = session.query(Coupon).filter_by(coupon_id=coupon_id).first()
                if not coupon:
                    return {"success": False, "error_msg": "优惠券不存在"}

                # 统计各状态核销码数量
                voucher_stats = session.query(Voucher.status, session.query(Voucher.id).filter_by(coupon_id=coupon.id).count()).filter_by(coupon_id=coupon.id).group_by(Voucher.status).all()
                
                stats = {
                    "unreceived": 0,  # 未领取
                    "received": 0,    # 已领取未使用
                    "used": 0,        # 已使用
                    "expired": 0      # 已过期
                }
                
                for status, count in voucher_stats:
                    if status == 0:
                        stats["unreceived"] = count
                    elif status == 1:
                        stats["received"] = count
                    elif status == 2:
                        stats["used"] = count
                    elif status == 3:
                        stats["expired"] = count

                return {
                    "success": True,
                    "coupon_id": coupon_id,
                    "coupon_name": coupon.coupon_name,
                    "total_quantity": coupon.total_quantity,
                    "stats": stats
                }

        except Exception as e:
            self.logger.error(f"获取使用统计失败: {str(e)}")
            return {"success": False, "error_msg": str(e)}

    def _generate_coupon_id(self) -> str:
        """生成优惠券ID"""
        timestamp = int(datetime.now().timestamp())
        random_str = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))
        return f"CPN{timestamp}{random_str}"

    def _generate_voucher_code(self) -> str:
        """生成核销码"""
        # 生成12位数字+字母组合的核销码
        return ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(12))

    def _generate_voucher_codes(self, session, coupon_id: int, quantity: int):
        """批量生成核销码"""
        vouchers = []
        for _ in range(quantity):
            voucher_code = self._generate_voucher_code()
            voucher = Voucher(
                coupon_id=coupon_id,
                voucher_code=voucher_code,
                status=0  # 未领取
            )
            vouchers.append(voucher)
            
            # 记录创建日志
            log = VoucherLog(
                voucher_id=None,  # 将在voucher保存后更新
                operation="create",
                operator="system",
                remarks=f"批量生成核销码"
            )
            session.add(voucher)
            session.flush()  # 获取voucher.id
            log.voucher_id = voucher.id
            session.add(log)

        session.commit()

    def _calculate_discount(self, coupon: Coupon, order_amount: float = None) -> float:
        """计算优惠金额"""
        if coupon.coupon_type == 1:  # 满减券
            return float(coupon.discount_amount) if coupon.discount_amount else 0
        elif coupon.coupon_type == 2 and order_amount:  # 折扣券
            discount_rate = float(coupon.discount_rate) if coupon.discount_rate else 1
            return order_amount * (1 - discount_rate)
        elif coupon.coupon_type == 3:  # 免费券
            return order_amount if order_amount else 0
        
        return 0