"""服务模式（卡券核销 / 仅发货 / 二选一）与验机配置解析。"""
from types import SimpleNamespace

from .modes import _SERVICE_MODE_VOUCHER
from typing import Any
from typing import Dict
from typing import Optional


class ServiceModeMixin:
    def _resolve_service_mode(self, shop_id: str, goods_id) -> int:
        """读取商品级服务模式（兼容接口：查不到时退回卡券核销）"""
        mode = self._resolve_service_mode_optional(shop_id, goods_id)
        return _SERVICE_MODE_VOUCHER if mode is None else mode

    def _resolve_service_mode_optional(self, shop_id: str, goods_id) -> Optional[int]:
        """读取商品级服务模式；商品未配置返回 None

        未配置不能再当成卡券核销，否则会给仅发货类商品索要卡券。
        """
        try:
            from core.service_mode import resolve_service_mode_optional
        except ImportError:
            # 离线自测脚本会注入只带 resolve_service_mode 的替身模块
            try:
                from core.service_mode import resolve_service_mode
                return resolve_service_mode(shop_id, goods_id)
            except Exception as e:
                self.logger.warning(
                    f"[发货] 解析服务模式失败 shop_id={shop_id} goods_id={goods_id}: {e}")
                return None
        try:
            return resolve_service_mode_optional(shop_id, goods_id)
        except Exception as e:
            self.logger.warning(
                f"[发货] 解析服务模式失败 shop_id={shop_id} goods_id={goods_id}: {e}")
            return None

    def _resolve_order_service_mode(
        self,
        shop_id: str,
        from_uid: str,
        *,
        message_goods_id=None,
        message_spec: str = "",
        order_sn=None,
        order_detail_goods_id=None,
        order_detail_spec: str = "",
        claimed_order=None,
        picked_order=None,
        common_goods=None,
        history_goods_id=None,
    ):
        """按「客户这一单」判定服务模式（订单 > 待发货单 > 历史 > 本单落库）

        线索由 handle() 在本次消息处理里已经查到的结果传入，这里不重复查平台。
        返回对象带 mode / evidence / goods_id / spec / order_sn / is_known / is_weak，
        mode 为 None 表示未确定——调用方必须按二选一降级，不得据此索要卡券。
        """
        clues = {
            "message_goods_id": message_goods_id,
            "message_spec": message_spec,
            "order_sn": order_sn,
            "order_detail_goods_id": order_detail_goods_id,
            "order_detail_spec": order_detail_spec,
            "claimed_order": claimed_order,
            "picked_order": picked_order,
            "common_goods": common_goods,
            "history_goods_id": history_goods_id,
        }
        try:
            from core.service_mode import resolve_service_mode_with_evidence
        except ImportError:
            # 离线自测会注入只带 resolve_service_mode 的替身模块
            goods_id = (
                message_goods_id
                or (claimed_order or {}).get("goods_id")
                or order_detail_goods_id
                or (picked_order or {}).get("goods_id")
                or (common_goods or {}).get("goods_id")
                or history_goods_id
            )
            mode = self._resolve_service_mode_optional(shop_id, goods_id)
            spec = message_spec or str((claimed_order or {}).get("spec") or "")
            return SimpleNamespace(
                mode=mode, evidence="legacy",
                goods_id=(str(goods_id) if goods_id else None),
                spec=spec or "", order_sn=order_sn or None,
                is_known=mode is not None, is_weak=False,
            )

        local_mode = self._local_order_mode(shop_id, from_uid, order_sn=order_sn)

        # 卡片优先：本条消息自带的商品 > 会话里绑定的商品（都不查平台订单）
        try:
            from core.service_mode import EVIDENCE_CONVERSATION, ServiceModeResolution
            from database.goods_config import resolve_service_mode_for_session

            bound = resolve_service_mode_for_session(
                shop_id, from_uid,
                fallback_goods_id=message_goods_id, fallback_spec=message_spec)
            if bound.get("mode") is not None:
                evidence = (EVIDENCE_CONVERSATION if bound.get("evidence") == "conversation_card"
                            else "message_goods")
                self.logger.info(
                    f"[核销] 服务模式判定 mode={bound['mode']} 依据={evidence} "
                    f"goods_id={bound.get('goods_id')} spec={bound.get('spec')!r}")
                return ServiceModeResolution(
                    mode=bound["mode"], evidence=evidence,
                    goods_id=bound.get("goods_id"), spec=bound.get("spec") or "",
                    order_sn=order_sn or bound.get("order_sn"))
        except Exception as e:
            self.logger.debug(f"[核销] 卡片商品判定失败: {e}")

        resolution = resolve_service_mode_with_evidence(
            shop_id, local_order_mode=local_mode, **clues)
        self.logger.info(
            f"[核销] 服务模式判定 mode={resolution.mode} 依据={resolution.evidence} "
            f"goods_id={resolution.goods_id} spec={resolution.spec!r} "
            f"order_sn={resolution.order_sn}")
        return resolution

    def _resolve_query_config(self, goods_id, spec) -> Dict[str, Any]:
        """解析验机配置：只认「商品 + 规格」精确匹配，绝不换规格顶替

        客户买的是「特惠激活锁」就只能查特惠激活锁（zhenxin 1014）；
        缺规格时退到「全面验机报告」（zhenxin 1032）是错的：报告字段对不上客户
        付的钱，还白烧一次更贵的接口额度。规格确实拿不到时，调用方按「先不查、
        核对后再补报告」处理。
        """
        cfg = self._get_query_config(str(goods_id or ""), spec or "")
        if not (cfg.get("server_id") or cfg.get("zhenxin_server_id")):
            self.logger.warning(
                f"[核销] 未匹配到验机服务，不做规格顶替 goods_id={goods_id} spec={spec!r}")
        return cfg

    def _get_query_config(self, goods_id: str, spec: str) -> dict:
        """验机配置统一走 database.goods_config（与 AI 提示词共用同一份配置来源）"""
        empty = {"server_id": None, "zhenxin_server_id": None, "device_type": None}
        try:
            from database.goods_config import get_verify_config

            cfg = get_verify_config(goods_id, spec or None)
            return {
                "server_id": cfg.get("server_id"),
                "zhenxin_server_id": cfg.get("zhenxin_server_id"),
                "device_type": cfg.get("device_type"),
            }
        except Exception as e:
            self.logger.error(f"[核销] 查询验机配置失败: {e}")
            return empty
