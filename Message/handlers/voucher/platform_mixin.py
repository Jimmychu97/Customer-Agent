"""平台订单协调：认领客户待发货单、订单详情补全。"""
from .io_exec import _io
from typing import Any
from typing import Dict


class PlatformMixin:
    async def _resolve_customer_ship_order(self, shop_id: str, user_id: str, from_uid: str,
                                           sn: str = "", force: bool = False) -> Dict[str, Any]:
        """按「对话用户ID + 平台订单状态」认领这个客户要发的那一笔订单（平台调用走线程池）

        返回:
            status: one       - 唯一一笔待发货单，可直接用（order 字段给出）
                    many      - 多笔，必须让客户指定（orders 字段给出候选）
                    stale     - 名下订单都已发货，请他发新订单号
                    none      - 确实没有待发货单
                    unavailable - 平台查不到，不能下结论
            order:  status=one 时可直接使用的那一笔
            orders: 候选列表
            source: local（按会话/客户本地单）/ platform（店铺待发货单）
        """
        local_all = await _io(self._customer_pending_orders, shop_id, from_uid)
        local_by_sn = {o.get("order_sn"): o for o in local_all}

        async def _platform(force: bool) -> Dict[str, Any]:
            try:
                from core.order_lookup import pending_orders_summary

                return await _io(pending_orders_summary, shop_id, user_id, force)
            except Exception as e:
                self.logger.debug(f"[订单查询] 平台待发货单核对失败 from_uid={from_uid}: {e}")
                return {"status": "unavailable", "orders": []}

        # 平台待发货列表是权威口径（带商品ID/规格，能直接定查询项目）；
        # 本地 orders 表只是「这个客户聊过哪几笔单」，状态可能过期，所以不能优先。
        # 客户主动发序列号/问订单时（sn 非空或 force=True）**先去后台实查一次**，
        # 不吃缓存、不受冷却限制——否则刚被限流过就只能干等，客户以为系统不会查。
        trigger_force = bool(force or sn)
        summary = await _platform(trigger_force)
        status = str(summary.get("status") or "")

        # 还是空/查不到：再强制刷新一次（每店铺 10s 最多一次）
        if status in ("empty", "unavailable") and not trigger_force:
            refreshed = await _platform(True)
            if refreshed.get("orders") or refreshed.get("status") == "unavailable" and status == "empty":
                summary = refreshed
                status = str(summary.get("status") or "")

        platform_orders = summary.get("orders") or []

        owns_unavailable = None
        owned_sns = self._owned_order_sns(shop_id, from_uid)
        just_bought = self._placed_order_recently(shop_id, from_uid)
        if status == "unique" and platform_orders:
            order = dict(platform_orders[0])
            order_sn_platform = str(order.get("order_sn") or "")
            # 平台待发货列表是**全店**口径（没有买家UID）：必须能证明是这客户的单——
            # 要么本会话出现过这个订单号（订单卡片/订单号），要么聊天里有他刚下单成功的系统消息。
            if order_sn_platform not in owned_sns and not just_bought:
                self.logger.info(
                    f"[发货] 平台有 1 笔待发货 {order_sn_platform}，但本会话没有这笔单的任何证据，"
                    f"不发给客户 from_uid={from_uid}")
                return {"status": "unverified", "order": None, "orders": [],
                        "source": "platform"}
            local_row = local_by_sn.get(order_sn_platform)
            if local_row:
                order.setdefault("order_id", local_row.get("order_id"))
            assumed = order_sn_platform not in owned_sns
            self.logger.info(
                f"[发货] 按平台待发货单确认 order_sn={order_sn_platform} "
                f"spec={order.get('spec')!r} 依据={'会话订单记录' if not assumed else '刚下单系统消息'}")
            return {"status": "one", "order": order, "orders": [order],
                    "source": "platform", "assumed": assumed}

        # 平台有多笔（店里同时在处理好几个客户的单）：只在这个客户自己的单里挑最新一笔，
        # 别人的单一律不发（本地 orders 行按会话 from_uid 落库，能精确对上人）。
        local_single = self._pick_local_single(shop_id, from_uid, local_all)
        if status in ("common", "mixed") and platform_orders:
            mine = [o for o in platform_orders if str(o.get("order_sn") or "") in owned_sns]
            assumed = False
            if not mine and just_bought:
                # 他刚下单成功，店里最新的那笔就是他的
                newest = self._newest_order(platform_orders)
                mine = [newest] if newest else []
                assumed = True
            if not mine and local_single:
                mine = [local_single]
            if mine:
                order = self._merge_platform_meta(
                    self._newest_order(mine) or mine[0], platform_orders)
                if not order.get("spec"):
                    order = await self._enrich_order_with_detail(shop_id, user_id, order)
                self.logger.info(
                    f"[发货] 平台 {len(platform_orders)} 笔待发货，按客户自己的最新一笔确认 "
                    f"order_sn={order.get('order_sn')} spec={order.get('spec')!r} "
                    f"owned={len(mine)} assumed={assumed} from_uid={from_uid}")
                return {"status": "one", "order": order, "orders": [order],
                        "source": "local", "newest": len(mine) > 1, "assumed": assumed,
                        "candidates": len(mine)}
            # 对不上人：不能把别人的单号发给客户，让他发自己的订单卡片
            self.logger.info(
                f"[发货] 平台 {len(platform_orders)} 笔待发货，本会话对不上，请客户发订单卡片 "
                f"from_uid={from_uid}")
            return {"status": "unverified", "order": None, "orders": [],
                    "source": "platform"}
        if status == "empty":
            # 平台列表可能滞后或刚好被限流（缓存里也是空的）：客户刚发过订单号的单还算数，
            # 先按他那笔走确认流程（发货前还会再核对「待发货」，不会发错）。
            if local_single:
                order = await self._enrich_order_with_detail(shop_id, user_id, local_single)
                self.logger.info(
                    f"[发货] 平台列表暂时为空，用客户刚发的那笔单 "
                    f"order_sn={order.get('order_sn')} spec={order.get('spec')!r}")
                return {"status": "one", "order": order, "orders": [order], "source": "local"}
            return {"status": "stale" if local_all else "none", "order": None,
                    "orders": [], "source": "platform"}

        # 平台查不到（限流/会话过期）：只认「能被平台核实的本地单」，绝不拿旧单号顶
        verified_rows = await _io(self._customer_pending_orders, shop_id, from_uid, user_id, True)
        verified = [o for o in verified_rows if o.get("verified")]
        if len(verified) == 1:
            order = await self._enrich_order_with_detail(shop_id, user_id, verified[0])
            return {"status": "one", "order": order, "orders": [order], "source": "local"}
        if len(verified) > 1:
            return {"status": "many", "order": None, "orders": verified, "source": "local"}
        if local_single:
            order = await self._enrich_order_with_detail(shop_id, user_id, local_single)
            self.logger.info(
                f"[发货] 平台暂时查不到，用客户刚下的单 order_sn={order.get('order_sn')}")
            return {"status": "one", "order": order, "orders": [order], "source": "local"}
        return {"status": "unavailable", "order": None, "orders": [], "source": "platform"}

    async def _enrich_order_with_detail(self, shop_id: str, user_id: str,
                                  order: Dict[str, Any]) -> Dict[str, Any]:
        """本地订单行只存订单号，商品/规格要从平台订单详情补上（查询项目靠它定 SERVERID）"""
        enriched = dict(order or {})
        if enriched.get("goods_id") and enriched.get("spec"):
            return enriched
        order_sn = str(enriched.get("order_sn") or "")
        if not order_sn:
            return enriched
        try:
            from core.order_lookup import get_order_detail

            detail = await _io(get_order_detail, shop_id, user_id, order_sn, retries=1)
        except Exception as e:
            self.logger.debug(f"[订单查询] 补订单详情失败 order_sn={order_sn}: {e}")
            return enriched
        if detail:
            enriched["goods_id"] = enriched.get("goods_id") or detail.get("goods_id")
            enriched["spec"] = enriched.get("spec") or str(detail.get("spec") or "")
            enriched["goods_name"] = (enriched.get("goods_name")
                                      or str(detail.get("goods_name") or ""))
        return enriched

    def _pending_ship_note(self, shop_id: str, user_id: str):
        """核对本店待发货订单，返回 (给客户的话术, 可直接使用的那一笔|None)

        订单接口没有买家 UID，只能看店铺侧待发货列表；核到唯一一笔就直接拿它发货，
        核不到就明确告诉客户「有没有待发货的单」，再要订单卡片/订单号，别让客户猜。
        """
        try:
            from core.order_lookup import pending_orders_summary

            summary = pending_orders_summary(shop_id, user_id)
        except Exception as e:
            self.logger.debug(f"[订单查询] 待发货单核对失败: {e}")
            summary = {"status": "unavailable", "orders": []}

        status = str(summary.get("status") or "")
        orders = summary.get("orders") or []
        if status == "unique":
            order = orders[0]
            desc = " / ".join(
                x for x in (str(order.get("goods_name") or ""), str(order.get("spec") or "")) if x)
            return f"📦 已查后台：您有一笔待发货订单{'：' + desc if desc else ''}\n", order
        if status in ("common", "mixed"):
            first = orders[0]
            desc = " / ".join(
                x for x in (str(first.get("goods_name") or ""), str(first.get("spec") or "")) if x)
            return (
                f"📦 已查后台：您有 {len(orders)} 笔待发货订单"
                f"{'（' + desc + (' 等' if status == 'mixed' else '') + '）' if desc else ''}\n"
            ), None
        if status == "empty":
            return "📦 已查后台：本店当前没有待发货的订单\n", None
        return "📦 后台订单暂时没查到（查询被限流），稍后我再帮您核\n", None
