"""本地 Order 表的读写：建单 / 绑定 / 选择 / 状态回写。"""
from .modes import _SERVICE_MODE_SHIP
from .state import _get_order_lock
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Set
from typing import Tuple
# 待核销订单的续接窗口（分钟）
PENDING_WINDOW_MINUTES = 120


class OrdersMixin:
    @staticmethod
    def _select_order(session, order_model, shop_id: str, from_uid: str,
                      sn: Optional[str], coupon_no: Optional[str]):
        """在同一 session 内按优先级选出本条消息应归属的订单

        1. 卡券号精确匹配（不限时间）：同一张券永远复用同一行，不新建单号
        2. 本条只给序列号时：优先补齐"窗口内待补序列号"的待核销单
           （客户先发新券、再补序列号是常见节奏；否则会被第 3 条抢走，
           导致新券永远凑不齐、永远核销不了）
        3. 序列号/IMEI 精确匹配（不限时间）：同一个序列号不新建单号，
           但本条带来"不同的卡券号"时不复用（属于第二笔，另建新单）
        4. 窗口内最新的待核销单（status=0/2），且本条消息补齐它缺的那一项；
           若该单已有该项但本条消息给的是**不同的有效值**，视为纠正，复用同一行并改写：
           - 序列号/IMEI：文本和图片识别结果一视同仁（图片里同样是序列号/IMEI）
           - 卡券号：只认合法位数的卡券号（10-14位，见 _COUPON_* 常量），
             已核销成功（status=1）的单永不改写
        """
        from datetime import datetime as dt, timedelta

        base = session.query(order_model).filter(
            order_model.shop_id == shop_id,
            order_model.session_id == from_uid,
        )
        since = dt.now() - timedelta(minutes=PENDING_WINDOW_MINUTES)

        if coupon_no:
            found = base.filter(order_model.coupons == coupon_no).order_by(order_model.id.desc()).first()
            if found is not None:
                return found

        # 本条只有序列号 -> 先看有没有"已经收到卡券、正在等序列号"的待核销单，
        # 有就把序列号补进这一单（否则会被下面的序列号匹配抢走，新券永远核销不了）
        if sn and not coupon_no:
            for waiting in (
                base.filter(order_model.status != 1, order_model.created_at >= since)
                .order_by(order_model.id.desc())
                .limit(5)
                .all()
            ):
                if waiting.coupons and not waiting.sn_imei:
                    return waiting

        if sn:
            found = base.filter(order_model.sn_imei == sn).order_by(order_model.id.desc()).first()
            if found is not None:
                # 同一台机器可以有多笔核销业务（第二张券 / 第二次下单）。
                # 只有本条消息没有带来"不同的卡券号"时才复用这一行；
                # 否则继续往下走：待核销/失败的单按"纠正"复用，已核销成功的单必须另建新单，
                # 绝不能拿旧券去重复核销（旧券已被使用，接口会返回核销失败）。
                if not coupon_no or not found.coupons or found.coupons == coupon_no:
                    return found

        # 待核销(0) 和 核销失败(2) 的单都可以被纠正；已核销成功(1) 的单是最终凭据，永不改写
        candidate = base.filter(
            order_model.status != 1,
            order_model.created_at >= since,
        ).order_by(order_model.id.desc()).first()
        if candidate is None:
            return None

        need_sn = bool(candidate.coupons) and not candidate.sn_imei
        need_coupon = bool(candidate.sn_imei) and not candidate.coupons
        # 消息同时给出"另一个序列号 + 另一张卡券号"= 新的一笔业务，另建单，不做纠正
        if (sn and coupon_no and candidate.sn_imei and candidate.coupons
                and candidate.sn_imei != sn and candidate.coupons != coupon_no):
            return None

        # 补齐缺失项，或给出不同的有效值（客户纠正）→ 都用这一行，不新建单号
        sn_usable = bool(sn) and (
            need_sn or (candidate.sn_imei and candidate.sn_imei != sn)
        )
        coupon_usable = bool(coupon_no) and (
            need_coupon or (candidate.coupons and candidate.coupons != coupon_no)
        )
        if sn_usable or coupon_usable:
            return candidate
        return None

    async def _upsert_order(
        self,
        sn: Optional[str],
        coupon_no: Optional[str],
        shop_id: str,
        from_uid: str,
        order_sn: Optional[str] = None,
        service_mode: Optional[int] = None,
    ) -> Tuple[str, str, Optional[int], list]:
        """建单或复用已有单（同一卡券/序列号永远复用同一行，防重复建单）

        Args:
            sn: 序列号/IMEI
            coupon_no: 卡券号
            shop_id: 店铺 ID
            from_uid: 客户 UID
            order_sn: 拼多多订单号（仅发货流程使用；缺省 None 时行为与既有完全一致）
            service_mode: 本单所属服务模式（1-发货 / 0-核销），用于后续提示语分流

        Returns:
            (final_sn, final_coupon, order_id, updates)
            updates: 需要告知客户的变更提示（如"已更新您的序列号/IMEI"）
        """
        updates: list = []
        lock = _get_order_lock(shop_id, from_uid)
        with lock:
            try:
                from database import db_manager
                from database.models import Order

                with db_manager.session_scope() as session:
                    order = self._select_order(session, Order, shop_id, from_uid, sn, coupon_no)

                    if order is None:
                        order = Order(
                            shop_id=shop_id,
                            session_id=from_uid,
                            sn_imei=sn or "",
                            coupons=coupon_no or "",
                            status=0,
                        )
                        if order_sn:
                            order.order_sn = order_sn
                        if service_mode is not None:
                            order.service_mode = service_mode
                        session.add(order)
                        session.flush()
                        self.logger.info(f"[核销] 新建 Order sn={sn!r} coupon={coupon_no!r}")
                    else:
                        if sn:
                            if not order.sn_imei:
                                order.sn_imei = sn
                            elif order.sn_imei != sn:
                                if order.status == 1:
                                    # 已核销成功 = 最终凭据（GSX 结果是按当时的序列号查的），不允许再改写
                                    self.logger.info(
                                        f"[核销] Order id={order.id} 已核销成功，保留原序列号 "
                                        f"{order.sn_imei!r}，忽略本条 {sn!r}"
                                    )
                                else:
                                    # 客户重新提供了序列号/IMEI（文字纠正或重新拍图识别）→ 更新同一行
                                    self.logger.info(
                                        f"[核销] 纠正 Order id={order.id} sn: "
                                        f"{order.sn_imei!r} -> {sn!r}"
                                    )
                                    order.sn_imei = sn
                                    updates.append(f"✅ 已更新您的序列号/IMEI：{sn}")
                        if coupon_no:
                            if not order.coupons:
                                order.coupons = coupon_no
                            elif order.coupons != coupon_no:
                                if order.status == 1:
                                    # 已核销成功 = 最终凭据（券已被这张单核销掉），不允许再改写
                                    self.logger.info(
                                        f"[核销] Order id={order.id} 已核销成功，保留原卡券号 "
                                        f"{order.coupons!r}，忽略本条 {coupon_no!r}"
                                    )
                                else:
                                    # 客户重新提供了卡券号（打错后纠正 / 重新复制卡券）
                                    self.logger.info(
                                        f"[核销] 纠正 Order id={order.id} coupon: "
                                        f"{order.coupons!r} -> {coupon_no!r}"
                                    )
                                    order.coupons = coupon_no
                                    updates.append(f"✅ 已更新您的卡券号：{coupon_no}")
                        session.flush()
                        self.logger.info(
                            f"[核销] 复用 Order id={order.id} status={order.status} "
                            f"sn={order.sn_imei!r} coupon={order.coupons!r}"
                        )

                    # 订单号只由发货流程提供；缺省 None 时这里完全不动，既有行为不变
                    if order_sn and order.order_sn != order_sn:
                        self.logger.info(
                            f"[发货] Order id={order.id} 记录订单号 {order.order_sn!r} -> {order_sn!r}"
                        )
                        order.order_sn = order_sn
                        session.flush()

                    # 本单实际走的服务模式落库，供 AI 待办提示按模式分流
                    if service_mode is not None and order.service_mode != service_mode:
                        order.service_mode = service_mode
                        session.flush()

                    return order.sn_imei or "", order.coupons or "", order.id, updates
            except Exception as e:
                self.logger.opt(exception=True).error(f"[核销] 建单失败: {e}")
                # 异常时返回空值，避免用未经落库的数据触发核销
                return "", "", None, []

    def _get_pending_order_state(self, shop_id: str, from_uid: str) -> Optional[str]:
        """查询当前可续接/可纠正订单的缺失项

        Returns:
            'need_sn'     - 已有 coupon 缺 sn
            'need_coupon' - 已有 sn 缺 coupon
            None          - 没有可续接的单（没有待核销单，或单已完整）

        统计范围与 _select_order 一致：待核销(0) + 核销失败(2) 的单都算，
        已核销成功(1) 的单是最终凭据，不再续接。
        """
        if not shop_id or not from_uid:
            return None
        try:
            from database import db_manager
            from database.models import Order
            from datetime import datetime as dt, timedelta
            since = dt.now() - timedelta(minutes=PENDING_WINDOW_MINUTES)
            with db_manager.session_scope() as session:
                order = session.query(Order).filter(
                    Order.shop_id == shop_id,
                    Order.session_id == from_uid,
                    Order.status != 1,
                    Order.created_at >= since,
                ).order_by(Order.id.desc()).first()
                if not order:
                    return None
                if order.coupons and not order.sn_imei:
                    return "need_sn"
                if order.sn_imei and not order.coupons:
                    return "need_coupon"
                return None
        except Exception as e:
            self.logger.warning(f"[核销] 查询待核销订单失败: {e}")
            return None

    def _bind_order_sn(self, shop_id: str, from_uid: str, order_sn: str,
                       sn: Optional[str] = None,
                       service_mode: Optional[int] = None) -> Tuple[Optional[int], str]:
        """把订单号绑到该客户「已有序列号」的那张未完成单上

        与 _pend_ship_order 的区别：**不限 120 分钟窗口**。
        客户「先发序列号、隔一会儿再发订单号」是常态，窗口一过就匹配不到，
        会新建一条只有订单号的空单，然后两边互相要求对方重发——死循环就是这么来的。

        Args:
            order_sn: 拼多多订单号
            sn: 本条消息里的序列号（可选，给了就优先按序列号精确匹配）
            service_mode: 要写入的服务模式（可选）

        Returns:
            (order_id, 该单已有的序列号)；找不到可绑定的单时返回 (None, "")
        """
        if not order_sn:
            return None, ""
        try:
            from database import db_manager
            from database.models import Order

            with db_manager.session_scope() as session:
                base = session.query(Order).filter(
                    Order.shop_id == shop_id,
                    Order.session_id == from_uid,
                    Order.status != 1,
                )
                order = None
                if sn:
                    order = base.filter(Order.sn_imei == sn).order_by(Order.id.desc()).first()
                if order is None:
                    # 该客户最近一张「有序列号、还没订单号」的未完成单
                    order = (
                        base.filter(Order.sn_imei != "")
                        .filter((Order.order_sn.is_(None)) | (Order.order_sn == ""))
                        .order_by(Order.id.desc())
                        .first()
                    )
                if order is None:
                    return None, ""

                if order.order_sn != order_sn:
                    self.logger.info(
                        f"[发货] 订单号绑定到已有单 order_id={order.id} "
                        f"sn={order.sn_imei!r} order_sn: {order.order_sn!r} -> {order_sn!r}")
                    order.order_sn = order_sn
                if sn and not order.sn_imei:
                    order.sn_imei = sn
                if service_mode is not None and order.service_mode != service_mode:
                    order.service_mode = service_mode
                session.flush()
                return order.id, order.sn_imei or ""
        except Exception as e:
            self.logger.warning(
                f"[发货] 绑定订单号失败 order_sn={order_sn!r} sn={sn!r}: {e}")
            return None, ""

    def _pend_ship_order(self, shop_id: str, from_uid: str, sn: Optional[str] = None,
                         order_sn: Optional[str] = None) -> Tuple[Optional[int], Optional[str]]:
        """读写「订单卡先到、序列号后到」的那张待发货单（复用同一行，不新建；只认无卡券的单）"""
        try:
            from database import db_manager
            from database.models import Order
            from datetime import datetime as dt, timedelta
            since = dt.now() - timedelta(minutes=PENDING_WINDOW_MINUTES)
            with db_manager.session_scope() as session:
                order = session.query(Order).filter(
                    Order.shop_id == shop_id, Order.session_id == from_uid,
                    Order.status != 1, Order.created_at >= since, Order.coupons == "",
                ).order_by(Order.id.desc()).first()
                if order is None:
                    return None, None
                if sn and not order.sn_imei:
                    order.sn_imei = sn
                if order_sn:
                    order.order_sn = order_sn
                # 标记为发货单：AI 待办提示据此不再索要卡券
                if order.service_mode != _SERVICE_MODE_SHIP:
                    order.service_mode = _SERVICE_MODE_SHIP
                return order.id, order.order_sn or None
        except Exception as e:
            self.logger.warning(f"[发货] 待发货单读写失败 sn={sn!r} order_sn={order_sn!r}: {e}")
            return None, None

    def _order_row(self, order_id: Optional[int]) -> Dict[str, Any]:
        """取本地订单行的关键字段（序列号/订单号/服务模式/状态），查不到返回空字典"""
        if not order_id:
            return {}
        try:
            from database import db_manager
            from database.models import Order

            with db_manager.session_scope() as session:
                order = session.query(Order).filter(Order.id == order_id).first()
                if not order:
                    return {}
                return {
                    "id": order.id,
                    "sn_imei": order.sn_imei or "",
                    "coupons": order.coupons or "",
                    "order_sn": order.order_sn or "",
                    "status": order.status,
                    "service_mode": order.service_mode,
                }
        except Exception as e:
            self.logger.debug(f"[发货] 读取订单行失败 order_id={order_id}: {e}")
            return {}

    def _sn_of_order(self, order_id: Optional[int]) -> str:
        """取指定订单已记录的序列号/IMEI（没有则返回空串）"""
        if not order_id:
            return ""
        try:
            from database import db_manager
            from database.models import Order

            with db_manager.session_scope() as session:
                order = session.query(Order).filter(Order.id == order_id).first()
                return (order.sn_imei or "") if order else ""
        except Exception as e:
            self.logger.debug(f"[发货] 读取订单序列号失败 order_id={order_id}: {e}")
            return ""

    def _order_by_sn(self, shop_id: str, order_sn: str) -> Optional[Dict[str, Any]]:
        """按订单号取本地订单（含已发货/已核销的历史单）"""
        if not order_sn:
            return None
        try:
            from database import db_manager
            from database.models import Order

            with db_manager.session_scope() as session:
                order = session.query(Order).filter(
                    Order.shop_id == str(shop_id),
                    Order.order_sn == str(order_sn),
                ).order_by(Order.id.desc()).first()
                if not order:
                    return None
                return {
                    "order_id": order.id,
                    "order_sn": order.order_sn or "",
                    "status": order.status,
                    "sn_imei": order.sn_imei or "",
                    "coupons": order.coupons or "",
                    "sku_id": order.sku_id or "",
                    "gsx_result": order.gsx_result or "",
                    "service_mode": order.service_mode,
                }
        except Exception as e:
            self.logger.debug(f"[发货] 按订单号查单失败 order_sn={order_sn}: {e}")
            return None

    def _customer_pending_orders(self, shop_id: str, from_uid: str, user_id: Optional[str] = None,
                                 verify: bool = False) -> List[Dict[str, Any]]:
        """按「对话用户ID」查这个客户名下还没发货的订单（每个订单号只算一笔，最新在前）

        订单列表接口没有买家 UID，但平台会把该客户的订单卡推进这个会话，订单号已经
        按 session_id(=from_uid) 落在 orders 表里，按客户查就是「他自己买的那份」。

        verify=True 时会用平台订单详情核对状态：已发货/已关闭的老单会从候选里剔掉
        （并把本地标记成已发货），避免客户下了新单还被念旧单号。
        """
        try:
            from database import db_manager
            from database.models import Order

            with db_manager.session_scope() as session:
                rows = (
                    session.query(Order)
                    .filter(
                        Order.shop_id == str(shop_id),
                        Order.session_id == str(from_uid),
                        Order.status != 1,
                        Order.order_sn.isnot(None),
                        Order.order_sn != "",
                    )
                    .order_by(Order.id.desc())
                    .all()
                )
                seen: Set[str] = set()
                orders: List[Dict[str, Any]] = []
                for r in rows:
                    order_sn = str(r.order_sn or "")
                    if not order_sn or order_sn in seen:
                        continue
                    seen.add(order_sn)
                    orders.append({
                        "order_id": r.id,
                        "order_sn": order_sn,
                        "sn_imei": r.sn_imei or "",
                        "service_mode": r.service_mode,
                        "created_at": r.created_at,
                        "verified": False,
                    })
        except Exception as e:
            self.logger.warning(f"[订单查询] 按客户查待发货单失败 from_uid={from_uid}: {e}")
            return []

        if not verify or not user_id or not orders:
            return orders

        # 每次回复前按平台核对状态：不是「待发货」的老单剔掉，别拿旧单号糊弄客户
        from core.order_lookup import get_order_detail, is_cooling_down

        alive: List[Dict[str, Any]] = []
        for order in orders[:3]:
            detail = get_order_detail(shop_id, user_id, order["order_sn"], retries=0)
            if detail is None:
                # 查不到（限流 / 超出可查窗口 / 接口异常）：不敢下结论，保留候选
                alive.append(order)
                continue
            if detail.get("pending_shipment"):
                order["verified"] = True
                alive.append(order)
            else:
                self.logger.info(
                    f"[订单查询] 客户名下订单已不是待发货，剔掉 order_sn={order['order_sn']} "
                    f"状态={detail.get('order_status_str')}")
                self._update_order_status(order.get("order_id"), 1, order["order_sn"])
        return alive

    def _save_standalone_serial(self, sn: str, shop_id: str, from_uid: str,
                                order_id: Optional[int] = None) -> Optional[int]:
        """把序列号存到指定订单行（不传 order_id 就单独起一行，等客户指定订单号后再绑）

        多笔待发货单时不能把序列号随手挂到某一笔上，否则会发错货；
        只有一笔时就把序列号补到那张单上，别再多建一行。
        """
        if not sn:
            return None
        try:
            from database import db_manager
            from database.models import Order

            with db_manager.session_scope() as session:
                if order_id:
                    row = session.query(Order).filter_by(id=order_id).first()
                    if row is not None and not row.sn_imei:
                        row.sn_imei = sn
                        row.service_mode = _SERVICE_MODE_SHIP
                        return row.id
                row = (session.query(Order)
                       .filter(Order.shop_id == str(shop_id),
                               Order.session_id == str(from_uid),
                               Order.sn_imei == sn,
                               Order.status != 1)
                       .order_by(Order.id.desc())
                       .first())
                if row is None:
                    row = Order(shop_id=str(shop_id), session_id=str(from_uid), sn_imei=sn,
                                coupons="", status=0, service_mode=_SERVICE_MODE_SHIP)
                    session.add(row)
                    session.flush()
                elif not row.order_sn:
                    row.service_mode = _SERVICE_MODE_SHIP
                return row.id
        except Exception as e:
            self.logger.warning(f"[发货] 保存序列号失败 sn={sn}: {e}")
            return None

    def _order_by_id(self, order_id: Optional[int]) -> Optional[Dict[str, Any]]:
        """按主键取本地订单（客户只发序列号、该单已发货时用）"""
        if not order_id:
            return None
        try:
            from database import db_manager
            from database.models import Order

            with db_manager.session_scope() as session:
                order = session.query(Order).filter_by(id=order_id).first()
                if not order:
                    return None
                return {
                    "order_id": order.id,
                    "order_sn": order.order_sn or "",
                    "status": order.status,
                    "sn_imei": order.sn_imei or "",
                    "sku_id": order.sku_id or "",
                    "gsx_result": order.gsx_result or "",
                    "service_mode": order.service_mode,
                }
        except Exception as e:
            self.logger.debug(f"[发货] 按主键查单失败 order_id={order_id}: {e}")
            return None

    def _latest_shipped_order(self, shop_id: str, from_uid: str) -> Optional[Dict[str, Any]]:
        """该客户最近一张已发货、且留有验机服务口径（sku_id）或历史报告的单"""
        try:
            from database import db_manager
            from database.models import Order

            with db_manager.session_scope() as session:
                order = session.query(Order).filter(
                    Order.shop_id == str(shop_id),
                    Order.session_id == str(from_uid),
                    Order.status == 1,
                ).order_by(Order.id.desc()).first()
                if not order:
                    return None
                return {
                    "order_id": order.id,
                    "order_sn": order.order_sn or "",
                    "status": order.status,
                    "sn_imei": order.sn_imei or "",
                    "sku_id": order.sku_id or "",
                    "gsx_result": order.gsx_result or "",
                    "service_mode": order.service_mode,
                }
        except Exception as e:
            self.logger.debug(f"[发货] 查询历史已发货单失败 from_uid={from_uid}: {e}")
            return None

    def _clear_order_sn(self, order_id: Optional[int]) -> None:
        """清空 Order 的 sn_imei，让用户可以重新提供序列号"""
        if not order_id:
            return
        try:
            from database import db_manager
            from database.models import Order
            with db_manager.session_scope() as session:
                order = session.query(Order).filter_by(id=order_id).first()
                if order:
                    order.sn_imei = ""
        except Exception as e:
            self.logger.error(f"[核销] 清空 sn_imei 失败 order_id={order_id}: {e}")

    def _clear_order_coupon(self, order_id: Optional[int]) -> None:
        """清空 Order 的 coupons，让用户可以重新提供卡券号"""
        if not order_id:
            return
        try:
            from database import db_manager
            from database.models import Order
            with db_manager.session_scope() as session:
                order = session.query(Order).filter_by(id=order_id).first()
                if order:
                    order.coupons = ""
        except Exception as e:
            self.logger.error(f"[核销] 清空 coupons 失败 order_id={order_id}: {e}")

    def _pick_pending_reference(self, shop_id: str, user_id: str,
                                from_uid: str,
                                goods_id=None) -> Optional[Dict[str, Any]]:
        """查询店铺「唯一待发货订单」，给商品模式判定与订单号补全做参考

        订单列表接口没有买家UID（只有脱敏昵称），不能按客户精确反查；
        因此只在全店只有一笔待发货订单时采用，多笔一律放弃，避免把别人的单发掉。
        """
        try:
            from core.order_lookup import pick_unique_pending_ship_order
            from database import db_manager
            from database.models import Order

            known_sns = set()
            with db_manager.session_scope() as session:
                rows = session.query(Order.order_sn).filter(
                    Order.shop_id == str(shop_id), Order.order_sn.isnot(None)
                ).all()
                known_sns = {str(r[0]) for r in rows if r and r[0]}

            order = pick_unique_pending_ship_order(
                shop_id, user_id, goods_id=goods_id, exclude_order_sns=known_sns
            )
            if order:
                self.logger.info(
                    f"[订单查询] 客户 {from_uid} 参考待发货订单 order_sn={order.get('order_sn')} "
                    f"goods_id={order.get('goods_id')}"
                )
            return order
        except Exception as e:
            self.logger.warning(f"[订单查询] 查询待发货订单失败 from_uid={from_uid}: {e}")
            return None

    def _owned_order_sns(self, shop_id: str, from_uid: str) -> Set[str]:
        """本会话里出现过的订单号（客户发过订单卡片/订单号才算“他的单”）

        平台待发货列表是全店口径、没有买家UID，不能凭它认定某笔单属于某个客户，
        否则新客户咨询时会收到别人的订单号。
        """
        try:
            from database import db_manager
            from database.models import Order

            with db_manager.session_scope() as session:
                rows = (session.query(Order.order_sn)
                        .filter(Order.shop_id == str(shop_id),
                                Order.session_id == str(from_uid),
                                Order.order_sn.isnot(None),
                                Order.order_sn != "")
                        .all())
                return {str(r[0]) for r in rows if r[0]}
        except Exception as e:
            self.logger.debug(f"[发货] 校验订单归属失败 from_uid={from_uid}: {e}")
            return set()

    @staticmethod
    def _merge_platform_meta(order: Dict[str, Any],
                             platform_orders: List[Dict[str, Any]]) -> Dict[str, Any]:
        """本地单只有订单号：平台待发货列表里同一单号带着商品/规格，直接合并过来（省一次详情请求）"""
        merged = dict(order or {})
        order_sn = str(merged.get("order_sn") or "")
        for candidate in platform_orders or []:
            if str(candidate.get("order_sn") or "") != order_sn:
                continue
            merged["goods_id"] = merged.get("goods_id") or candidate.get("goods_id")
            merged["spec"] = merged.get("spec") or str(candidate.get("spec") or "")
            merged["goods_name"] = (merged.get("goods_name")
                                    or str(candidate.get("goods_name") or ""))
            break
        return merged

    @staticmethod
    def _newest_order(orders: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """最新的一笔（下单时间最大；没有时间就按订单号倒序——订单号前缀是日期）"""
        if not orders:
            return None

        def _key(order: Dict[str, Any]):
            raw = order.get("order_time")
            try:
                ts = int(raw)
            except Exception:
                ts = 0
            return (ts, str(order.get("order_sn") or ""))

        return max(orders, key=_key)

    def _pick_local_single(self, shop_id: str, from_uid: str,
                           local_all: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """客户自己名下唯一一笔「刚下的单」（续接窗口内），用于多笔待发货时精确对上人

        只认窗口内的单：超过窗口的旧单不在这里使用（避免又念旧单号）。
        """
        fresh = [o for o in (local_all or []) if self._is_fresh_order(o)]
        if len(fresh) == 1:
            return fresh[0]
        return None

    @staticmethod
    def _is_fresh_order(order: Dict[str, Any]) -> bool:
        """本地单是不是「刚下的」（还在待处理续接窗口内）"""
        created = order.get("created_at")
        if not created:
            return False
        try:
            from datetime import datetime as dt, timedelta

            return created >= dt.now() - timedelta(minutes=PENDING_WINDOW_MINUTES)
        except Exception:
            return False

    def _placed_order_recently(self, shop_id: str, from_uid: str,
                               minutes: int = 120) -> bool:
        """这个客户刚才是不是在本会话下单成功过（聊天里会有「邀请下单成功…实收X元」系统消息）

        平台订单列表没有买家UID，但这条系统消息只出现在下单人的会话里，
        可以作为「店里那笔待发货单属于他」的证据，从而不用再让客户发订单卡片。
        """
        try:
            from datetime import datetime as dt, timedelta
            from database import db_manager
            from database.models import MessageRecord

            since = dt.now() - timedelta(minutes=minutes)
            with db_manager.session_scope() as session:
                row = (session.query(MessageRecord.id)
                       .filter(MessageRecord.shop_id == str(shop_id),
                               MessageRecord.from_uid == str(from_uid),
                               MessageRecord.created_at >= since,
                               MessageRecord.content.like("%邀请下单成功%")
                               | MessageRecord.content.like("%拼单成功%")
                               | MessageRecord.content.like("%实收%"))
                       .first())
                return row is not None
        except Exception as e:
            self.logger.debug(f"[发货] 查客户下单信号失败 from_uid={from_uid}: {e}")
            return False

    def _local_order_mode(self, shop_id: str, from_uid: str, order_sn=None) -> Optional[int]:
        """本单落库的服务模式，只有「订单号对得上」才算依据

        不能拿「该客户最近一单」当依据：客户上一单是核销商品、这一单是发货商品时，
        据此判定就会给仅发货商品索要卡券。orders 表没有商品列，所以只认订单号；
        消息里没有订单号时宁可返回 None，让上层按「二选一」降级。
        """
        if not order_sn:
            return None
        try:
            from database import db_manager
            from database.models import Order
            from datetime import datetime as dt, timedelta

            since = dt.now() - timedelta(minutes=PENDING_WINDOW_MINUTES)
            with db_manager.session_scope() as session:
                order = session.query(Order).filter(
                    Order.shop_id == shop_id,
                    Order.session_id == from_uid,
                    Order.order_sn == str(order_sn),
                    Order.created_at >= since,
                    Order.service_mode.isnot(None),
                ).order_by(Order.id.desc()).first()
                mode = order.service_mode if order else None
            if mode is not None:
                self.logger.info(
                    f"[核销] 订单号对上下游单的落库模式 order_sn={order_sn} mode={mode}")
            return mode
        except Exception as e:
            self.logger.debug(f"[核销] 读取本单落库模式失败 order_sn={order_sn}: {e}")
            return None

    def _set_order_service_mode(self, order_id: Optional[int], mode: Optional[int]) -> None:
        """把已判定的服务模式写回本单，供后续消息与 AI 提示词复用同一口径

        只在值变化时写；None 表示未确定，不覆盖已有结论。
        """
        if not order_id or mode is None:
            return
        try:
            from database import db_manager
            from database.models import Order

            with db_manager.session_scope() as session:
                order = session.query(Order).filter_by(id=order_id).first()
                if order and order.service_mode != mode:
                    self.logger.info(
                        f"[核销] 写回本单服务模式 order_id={order_id} "
                        f"{order.service_mode} -> {mode}")
                    order.service_mode = mode
        except Exception as e:
            self.logger.debug(f"[核销] 写回服务模式失败 order_id={order_id}: {e}")

    def _order_already_shipped(self, shop_id: str, order_sn: str) -> bool:
        """该订单号在本店是否已发货成功（不依赖 order_id，防同一单重复调用发货接口）"""
        if not order_sn:
            return False
        try:
            from database import db_manager
            from database.models import Order

            with db_manager.session_scope() as session:
                count = session.query(Order).filter(
                    Order.shop_id == str(shop_id),
                    Order.order_sn == str(order_sn),
                    Order.status == 1,
                ).count()
                return count > 0
        except Exception as e:
            self.logger.debug(f"[发货] 查询订单发货状态失败 order_sn={order_sn}: {e}")
            return False

    def _shipped_by_id(self, order_id: Optional[int]) -> bool:
        """该单是否已发货成功（只按主键判断，订单号缺失时用来防重复发货/重复索要）"""
        if not order_id:
            return False
        try:
            from database import db_manager
            from database.models import Order
            with db_manager.session_scope() as session:
                order = session.query(Order).filter(Order.id == order_id).first()
                return bool(order and order.status == 1)
        except Exception as e:
            self.logger.debug(f"[发货] 查询订单状态失败 order_id={order_id}: {e}")
            return False

    def _already_shipped(self, order_id: Optional[int], order_sn: str) -> bool:
        """该单是否「已发货成功，且订单号与本次一致」（重复提交时不重复打接口）"""
        if not order_id or not order_sn:
            return False
        try:
            from database import db_manager
            from database.models import Order
            with db_manager.session_scope() as session:
                order = session.query(Order).filter(Order.id == order_id).first()
                return bool(order and order.status == 1 and order.order_sn == order_sn)
        except Exception as e:
            self.logger.debug(f"[发货] 查询已发货状态失败 order_id={order_id}: {e}")
            return False

    def _update_order_status(self, order_id: int, status: int, order_sn: Optional[str] = None) -> None:
        """更新 Order 状态

        已核销成功（status=1）的单不会被后续失败结果回退成失败：
        客户把同一张已核销成功的券再发一次时，接口会返回"已核销"错误，
        此时应保留成功状态，只把错误反馈给客户。

        order_sn 仅发货流程传入：成功时顺带把订单号与本单实际执行的服务模式写库。
        """
        if not order_id:
            return
        try:
            from database import db_manager
            from database.models import Order
            with db_manager.session_scope() as session:
                order = session.query(Order).filter_by(id=order_id).first()
                if order:
                    if order.status == 1 and status == 2:
                        self.logger.info(
                            f"[核销] Order id={order_id} 已是核销成功状态，忽略失败状态回写"
                        )
                        return
                    order.status = status
                    if order_sn and status == 1:
                        order.order_sn = order_sn
                        order.service_mode = _SERVICE_MODE_SHIP
        except Exception as e:
            self.logger.opt(exception=True).error(f"[核销] 更新 Order 状态失败 order_id={order_id}: {e}")
