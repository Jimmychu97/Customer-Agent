"""
核销自动检测处理器

规则：
- 只有序列号/IMEI → 建 Order 记录，引导用户补充卡券号（截断链，AI不介入）
- 只有卡券号      → 建 Order 记录，引导用户补充序列号/IMEI（截断链，AI不介入）
- 两个都有        → 调用外部 API 验证核销
  - 核销成功      → 发成功回复，放行链（AI可继续回答后续问题）
  - 核销失败/异常 → 发失败回复，截断链
"""
import re
import json
import asyncio
import threading
from typing import Optional, Dict, Any, Tuple
from bridge.context import Context, ContextType
from .base import BaseHandler


def _luhn_check(number: str) -> bool:
    """Luhn 算法校验 IMEI是否合法"""
    digits = [int(d) for d in number]
    odd_sum = sum(digits[-1::-2])
    even_sum = sum(sum(divmod(d * 2, 10)) for d in digits[-2::-2])
    return (odd_sum + even_sum) % 10 == 0


def _normalize_number(text: str) -> str:
    """将数字串中的空格、连字符、全角空格统一去除"""
    return re.sub(r'[\s\-\u3000]+', '', text)


def _extract_imei(text: str) -> Optional[str]:
    """提取 IMEI（15位数字，支持任意分隔符/空格格式）

    优先匹配 IMEI: 前缀（图片OCR场景，跳过Luhn校验避免OCR误差导致漏提取），
    再匹配通用15位数字并做Luhn校验。
    """
    # 第一轮：明确带 IMEI: 前缀，直接提取（OCR场景，不做Luhn校验）
    for m in re.finditer(r'IMEI[:\s]*([\d][\d\s\-]{13,22}[\d])', text, re.IGNORECASE):
        candidate = _normalize_number(m.group(1))
        if len(candidate) == 15 and candidate.isdigit():
            return candidate
    # 第二轮：通用匹配，做Luhn校验
    for m in re.finditer(r'(?<![\w])([\d][\d\s\-]{13,22}[\d])(?![\w])', text):
        candidate = _normalize_number(m.group(1))
        if len(candidate) == 15 and candidate.isdigit() and _luhn_check(candidate):
            return candidate
    return None


def _extract_serial(text: str) -> Optional[str]:
    """提取苹果序列号（10或12位字母数字，支持带空格格式）
    - 字符集：A-Z + 0-9，排除 I、O
    - 必须含至少一个字母（允许全字母，如 KHFDFDWWHN）
    - 用户输入的 o/O 自动替换为 0

    先在原文上匹配（换行/空格本身就是边界），再退回"去掉所有空白"的匹配。
    必须先走原文这一轮：直接去掉空白会把上一行的型号（如 MQ1C3CH/A 结尾的 A）
    和序列号粘成 11 位，导致序列号漏识别（服务器日志 2026-09-22 07:38 / 07:40 实际情况）。
    """
    APPLE_CHARS = r'[A-HJ-NP-Z0-9]'
    normalized = text.replace('o', '0').replace('O', '0').upper()

    def _search(source: str) -> Optional[str]:
        for length in (12, 10):
            for m in re.finditer(
                rf'(?<![A-Z0-9])({APPLE_CHARS}{{{length}}})(?![A-Z0-9])', source
            ):
                val = m.group(1)
                # 必须含至少一个字母（排除纯数字串被误识别为序列号）
                if re.search(r'[A-Z]', val):
                    return val
        return None

    # 第一轮：原文（保留换行/空格作为边界）
    hit = _search(normalized)
    if hit:
        return hit
    # 第二轮：去掉所有空白后再匹配，兼容 "D06 X14 PJCF" 这种被空格拆开的序列号
    return _search(re.sub(r'\s+', '', normalized))


def _is_phone(candidate: str) -> bool:
    """判断是否为手机号"""
    if len(candidate) == 11 and re.match(r'^1[3-9]', candidate):
        return True
    if len(candidate) == 13 and re.match(r'^861[3-9]', candidate):
        return True
    return False


# 卡券号位数规则（与仓库历史实现保持一致，勿随意改动）
# - 拼多多卡券号：10-14 位纯数字，支持空格/连字符分组格式，排除手机号
# - 15 位数字一律按 IMEI/订单号处理
# - "待补卡券"的严格模式更保守：只认 10-13 位，且排除 14 位以上长数字串的子串
_COUPON_MIN_DIGITS = 10
_COUPON_MAX_DIGITS = 14
_COUPON_STRICT_MAX_DIGITS = 13


def _has_imei_label(text: str, start: int) -> bool:
    """数字串前面一小段里是否带 IMEI/MEID 标签（文本和 OCR 里的常见写法）"""
    prefix = text[max(0, start - 12):start].upper()
    return ("IMEI" in prefix) or ("MEID" in prefix)


def _extract_coupon(text: str, exclude_imei: str = None) -> Optional[str]:
    """提取拼多多卡券号（10-14位纯数字，支持空格/连字符分组格式，排除手机号）

    位数规则沿用仓库历史实现（git log -p -- Message/handlers/voucher_handler.py
    自 first commit 起即为"10-14位纯数字"、"排除15位IMEI"）：
    - 卡券号：_COUPON_MIN_DIGITS ~ _COUPON_MAX_DIGITS 位
    - 15 位数字串一律按 IMEI 处理，绝不截尾当卡券号
    优先匹配带分隔符的分组格式（更可信），再匹配纯连续数字。
    对于超长连续数字串（16-18位），尝试从右截取标准长度（12位优先）。
    exclude_imei: 已提取的IMEI，避免将其子串误识别为卡券号
    """
    # 第一轮：必须含空格或连字符分隔的分组格式（高可信度），不跨行
    for m in re.finditer(r'(?<!\d)(\d+(?:[ \t\-]+\d+)+)(?!\d)', text):
        candidate = _normalize_number(m.group(1))
        if (candidate.isdigit() and not _is_phone(candidate)
                and _COUPON_MIN_DIGITS <= len(candidate) <= _COUPON_MAX_DIGITS):
            if exclude_imei and (candidate in exclude_imei or exclude_imei in candidate):
                continue
            return candidate

    # 第二轮：纯连续数字，精确匹配 10-14 位
    for m in re.finditer(
        rf'(?<!\d)(\d{{{_COUPON_MIN_DIGITS},{_COUPON_MAX_DIGITS}}})(?!\d)', text
    ):
        candidate = m.group(1)
        if not _is_phone(candidate):
            if exclude_imei and (candidate in exclude_imei or exclude_imei in candidate):
                continue
            return candidate

    # 第三轮：超长连续数字串（16-18位，通常是 OCR 把卡券号与其它数字粘在一起），
    # 尝试从右截取 12/10 位。
    # 注意：15 位数字串一律按 IMEI/订单号处理，不再截尾当卡券号——
    # 服务器日志 2026-09-23 08:25 就出现过把第二个 IMEI 的尾部
    # （355549350251721 -> 549350251721）误当卡券号，验券失败还多建了一张单。
    for m in re.finditer(r'(?<!\d)(\d{16,18})(?!\d)', text):
        raw = m.group(1)
        if exclude_imei and (raw == exclude_imei or exclude_imei in raw):
            continue
        if _has_imei_label(text, m.start()):
            continue
        for length in (12, 10):
            candidate = raw[-length:]
            if not _is_phone(candidate):
                if exclude_imei and (candidate in exclude_imei or exclude_imei in candidate):
                    continue
                return candidate

    return None


def _extract_coupon_strict(text: str, exclude_imei: Optional[str] = None) -> Optional[str]:
    """严格卡券号提取（已有序列号待补卡券时使用）

    只认 10-13 位（_COUPON_STRICT_MAX_DIGITS）、排除手机号、
    排除 14 位以上长数字串（IMEI/订单号）的子串，
    避免把序列号或订单号误判成卡券号。
    """
    long_numbers = re.findall(rf'(?<!\d)(\d{{{_COUPON_STRICT_MAX_DIGITS + 1},}})(?!\d)', text)
    for m in re.finditer(
        rf'(?<!\d)(\d+(?:[ \t\-]+\d+)+|\d{{{_COUPON_MIN_DIGITS},{_COUPON_STRICT_MAX_DIGITS}}})(?!\d)',
        text,
    ):
        raw = m.group(1)
        candidate = _normalize_number(raw) if (' ' in raw or '-' in raw) else raw
        if not candidate.isdigit() or not (
            _COUPON_MIN_DIGITS <= len(candidate) <= _COUPON_STRICT_MAX_DIGITS
        ):
            continue
        if _is_phone(candidate):
            continue
        if any(candidate in ln or ln in candidate for ln in long_numbers):
            continue
        if exclude_imei and (candidate in exclude_imei or exclude_imei in candidate):
            continue
        return candidate
    return None


# 核销结果常量
_VOUCHER_PENDING = "pending"   # 信息不完整，引导补充
_VOUCHER_SUCCESS = "success"   # 核销成功
_VOUCHER_FAILED  = "failed"    # 核销失败（API返回失败）
_VOUCHER_ERROR   = "error"     # 处理异常

# 待核销订单的续接窗口（分钟）：仅用于"本条消息只带缺失的那一项"时把消息挂到已有的待核销单，
# 卡券号/序列号的精确匹配不受此窗口限制（同一张券/同一个序列号永远复用同一行，不新建单号）
PENDING_WINDOW_MINUTES = 120

# 按 (shop_id, from_uid) 串行化"查找 + 建单"，避免并发消息把同一张券建成两单
_order_locks: Dict[Tuple[str, str], threading.Lock] = {}
_order_locks_guard = threading.Lock()


def _get_order_lock(shop_id: str, from_uid: str) -> threading.Lock:
    """获取指定客户的建单锁"""
    with _order_locks_guard:
        lock = _order_locks.get((shop_id, from_uid))
        if lock is None:
            lock = threading.Lock()
            _order_locks[(shop_id, from_uid)] = lock
        return lock


def _format_gsx_result(data) -> str:
    """将 GSX 返回的 dict 或 list 格式化为可读文本"""
    if isinstance(data, list):
        data = data[0] if data else {}
    if not isinstance(data, dict):
        return str(data)
    return "\n".join(f"{k}：{v}" for k, v in data.items())


def _analyze_gsx_result(data) -> str:
    """基于 GSX 返回字段生成简明中文简析"""
    if isinstance(data, list):
        data = data[0] if data else {}
    if not isinstance(data, dict):
        return ""

    lines = []

    model = data.get("机型", "")
    if model:
        lines.append(f"● 机型：{model}")

    capacity = data.get("容量", "")
    color = data.get("颜色", "")
    if capacity:
        lines.append(f"● 容量：{capacity}")
    if color:
        lines.append(f"● 颜色：{color}")

    id_status = str(data.get("ID黑白", ""))
    if id_status:
        ok = "Clean" in id_status or "白名单" in id_status
        lines.append(f"● ID黑白：{'✅ 干净（白名单）' if ok else '⚠️ ' + id_status}")

    lock = str(data.get("网络锁", "") or data.get("是否有锁", ""))
    if lock:
        if "Unlocked" in lock or "无锁" in lock:
            lines.append("● 网络锁：✅ 无锁")
        elif "ON" in lock or "有锁" in lock:
            lines.append("● 网络锁：⚠️ 有锁（请确认运营商）")
        else:
            lines.append(f"● 网络锁：{lock}")

    activation = str(data.get("激活状态", ""))
    if activation:
        lines.append(f"● 激活状态：{activation}")

    activate_date = str(data.get("激活日期", ""))
    if activate_date:
        lines.append(f"● 激活日期：{activate_date}")

    warranty = str(data.get("保修状态", "") or data.get("保修剩余", ""))
    if warranty:
        in_w = "Active" in warranty or "在保" in warranty
        lines.append(f"● 保修：{'✅ ' if in_w else ''}{warranty}")

    official_replace = str(data.get("是否官换", "") or data.get("官换", ""))
    if official_replace:
        replaced = official_replace.strip() in ("是", "Yes")
        lines.append(f"● 官换机：{'⚠️ 是' if replaced else '✅ 否'}")

    official_refurb = str(data.get("是否官翻机", ""))
    if official_refurb:
        refurb = official_refurb.strip() in ("是", "Yes")
        lines.append(f"● 官翻机：{'⚠️ 是' if refurb else '✅ 否'}")

    blacklist = str(data.get("美国黑名单", "") or data.get("黑名单状态（GSMA）", ""))
    if blacklist:
        clean = "Clean" in blacklist or "白名单" in blacklist
        lines.append(f"● 黑名单：{'✅ 干净' if clean else '⚠️ ' + blacklist}")

    return "\n".join(lines) if lines else ""


class VoucherDetectionHandler(BaseHandler):
    """
    核销自动检测处理器

    引导中（单条件）：发引导提示，截断链（AI不介入，避免干扰核销流程）。
    核销成功：发成功回复，放行链（AI可继续回答客户后续问题）。
    核销失败/异常：发失败回复，截断链。
    """

    def __init__(self):
        super().__init__("VoucherDetectionHandler")

    def can_handle(self, context: Context) -> bool:
        if context.type != ContextType.TEXT:
            return False
        content = context.content or ""
        return bool(
            _extract_serial(content)
            or _extract_imei(content)
            or _extract_coupon(content)
        )

    async def handle(self, context: Context, metadata: Dict[str, Any]) -> bool:
        content = context.content or ""
        from_uid = metadata.get("from_uid")
        shop_id = metadata.get("shop_id")
        user_id = metadata.get("user_id")
        pending: Optional[str] = None

        # 图片来源时直接使用 ai_handler 已提取好的值，不重新提取
        # 避免 IMEI2 等被误截为卡券号
        if "_image_sn" in metadata or "_image_coupon" in metadata:
            sn = metadata.get("_image_sn") or None
            coupon_no = metadata.get("_image_coupon") or None
            self.logger.info(f"[核销] 图片来源，使用预提取值 sn={sn!r} coupon={coupon_no!r}")
        else:
            sn = _extract_imei(content) or _extract_serial(content)
            # 注意：这里不再根据待核销单的缺失项把用户真实发来的值置空。
            # 重复提交时由 _upsert_order 按卡券号/序列号复用同一张单，避免重复建单，
            # 也避免"两值皆空"时用空参数去核销。
            pending = self._get_pending_order_state(shop_id, from_uid)
            if pending == "need_coupon":
                # 待补卡券：用严格提取，避免把序列号/订单号误判为卡券号
                coupon_no = _extract_coupon_strict(content, exclude_imei=sn)
            else:
                coupon_no = _extract_coupon(content, exclude_imei=sn)

        if not all([shop_id, user_id, from_uid]):
            self.logger.warning(
                f"[核销] 缺少必要参数 shop_id={shop_id} user_id={user_id} from_uid={from_uid}，跳过处理"
            )
            return False

        if not sn and not coupon_no:
            self.logger.info(
                f"[核销] 本条消息未提取到卡券号/序列号 pending={pending!r}，跳过核销处理 content={content[:40]!r}"
            )
            return False

        self.logger.info(
            f"[核销] 提取到信息 sn={sn!r} coupon={coupon_no!r} "
            f"from_uid={from_uid} shop_id={shop_id}"
        )

        session_id = f"pinduoduo{from_uid}"
        self._record_message(session_id, "user", content)

        # 只有卡券，没有序列号/IMEI → 存单后检查是否已凑齐
        if coupon_no and not sn:
            final_sn, final_coupon, order_id, updates = await self._upsert_order(sn, coupon_no, shop_id, from_uid)
            await self._send_updates(updates, shop_id, user_id, from_uid, session_id)
            if final_sn and final_coupon:
                notify = f"✅ 已记录您的卡券号：{coupon_no}，正在核销..."
                await self._send(shop_id, user_id, from_uid, notify)
                self._record_message(session_id, "assistant", notify)
                reply, _ = await self._do_verify(final_sn, final_coupon, shop_id, user_id, from_uid, order_id)
                if reply:
                    await self._send(shop_id, user_id, from_uid, reply)
                    self._record_message(session_id, "assistant", reply)
            else:
                guide = (
                    f"✅ 已记录您的卡券号：{coupon_no}\n"
                    f"如有误请直接发文字或截图纠正\n"
                    f"\n"
                    f"请继续提供以下任意一项完成核销：\n"
                    f"\n"
                    f"📱 IMEI码（推荐）\n"
                    f"拨号界面输入 *#06# 即可显示\n"
                    f"\n"
                    f"🔢 序列号\n"
                    f"设置 → 通用 → 关于本机 → 序列号"
                )
                await self._send(shop_id, user_id, from_uid, guide)
                self._record_message(session_id, "assistant", guide)
            return True

        # 只有序列号/IMEI，没有卡券 → 存单后检查是否已凑齐
        if sn and not coupon_no:
            final_sn, final_coupon, order_id, updates = await self._upsert_order(sn, coupon_no, shop_id, from_uid)
            await self._send_updates(updates, shop_id, user_id, from_uid, session_id)
            if final_sn and final_coupon:
                notify = f"✅ 已记录您的序列号/IMEI：{sn}，正在核销..."
                await self._send(shop_id, user_id, from_uid, notify)
                self._record_message(session_id, "assistant", notify)
                reply, _ = await self._do_verify(final_sn, final_coupon, shop_id, user_id, from_uid, order_id)
                if reply:
                    await self._send(shop_id, user_id, from_uid, reply)
                    self._record_message(session_id, "assistant", reply)
            else:
                guide = (
                    f"✅ 已记录您的序列号/IMEI：{sn}\n"
                    f"如有误请直接发文字或截图纠正\n"
                    f"\n"
                    f"请提供卡券号完成核销：\n"
                    f"待收货 → 点击商品 → 查看卡券 → 复制卡券发给客服"
                )
                await self._send(shop_id, user_id, from_uid, guide)
                self._record_message(session_id, "assistant", guide)
            return True

        # 同一条消息里两个都有 → 直接核销
        # 用 _upsert_order 返回的 final 值，避免消息里误提取的 coupon 覆盖已存的正确值
        final_sn, final_coupon, order_id, updates = await self._upsert_order(sn, coupon_no, shop_id, from_uid)
        await self._send_updates(updates, shop_id, user_id, from_uid, session_id)

        # 兜底：只要有一项为空就绝不调用核销接口（防止空序列号/空卡券把券核销掉）
        if not (final_sn and final_coupon):
            if not final_coupon:
                guide = (
                    f"✅ 已记录您的序列号/IMEI：{final_sn}\n"
                    f"如有误请直接发文字或截图纠正\n"
                    f"\n"
                    f"请提供卡券号完成核销：\n"
                    f"待收货 → 点击商品 → 查看卡券 → 复制卡券发给客服"
                )
            else:
                guide = (
                    f"✅ 已记录您的卡券号：{final_coupon}\n"
                    f"如有误请直接发文字或截图纠正\n"
                    f"\n"
                    f"请继续提供以下任意一项完成核销：\n"
                    f"\n"
                    f"📱 IMEI码（推荐）\n"
                    f"拨号界面输入 *#06# 即可显示\n"
                    f"\n"
                    f"🔢 序列号\n"
                    f"设置 → 通用 → 关于本机 → 序列号"
                )
            await self._send(shop_id, user_id, from_uid, guide)
            self._record_message(session_id, "assistant", guide)
            return True

        reply, _ = await self._do_verify(final_sn, final_coupon, shop_id, user_id, from_uid, order_id)
        if reply:
            await self._send(shop_id, user_id, from_uid, reply)
            self._record_message(session_id, "assistant", reply)
        return True

    async def _send_updates(self, updates, shop_id: str, user_id: str, from_uid: str,
                            session_id: str) -> None:
        """把"已更新您的序列号/IMEI"等变更提示发给客户"""
        if not updates:
            return
        note = "\n".join(updates)
        await self._send(shop_id, user_id, from_uid, note)
        self._record_message(session_id, "assistant", note)

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
    ) -> Tuple[str, str, Optional[int], list]:
        """建单或复用已有单（同一卡券/序列号永远复用同一行，防重复建单）

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

    def _already_verified(self, order_id: Optional[int], coupon_no: str) -> bool:
        """该单是否"已核销成功，且卡券号与本次一致"

        客户把同一张已核销成功的券再发一次时，不要重复调用验券接口
        （接口会返回"已核销"错误，容易被误报成"核销失败"）。
        """
        if not order_id or not coupon_no:
            return False
        try:
            from database import db_manager
            from database.models import Order
            with db_manager.session_scope() as session:
                order = session.query(Order).filter(Order.id == order_id).first()
                return bool(order and order.status == 1 and order.coupons == coupon_no)
        except Exception as e:
            self.logger.debug(f"[核销] 查询已核销状态失败 order_id={order_id}: {e}")
            return False

    async def _do_verify(
        self,
        sn: str,
        coupon_no: str,
        shop_id: str,
        user_id: str,
        from_uid: str,
        order_id: Optional[int],
    ) -> Tuple[Optional[str], str]:
        """触发核销流程，核销成功后串行 await GSX查询+LLM分析"""
        # 双保险：参数不完整时绝不调用核销接口
        if not sn or not coupon_no:
            self.logger.warning(
                f"[核销] 参数不完整 sn={sn!r} coupon={coupon_no!r} order_id={order_id}，跳过核销调用"
            )
            return "", _VOUCHER_ERROR
        # 该券已经核销成功 → 直接告知，不再打接口（避免"已核销"错误被误报成核销失败）
        if self._already_verified(order_id, coupon_no):
            self.logger.info(
                f"[核销] 卡券已核销成功，跳过重复核销调用 order_id={order_id} coupon={coupon_no}"
            )
            return f"✅ 该卡券已核销成功，无需重复核销\n卡券号：{coupon_no}", _VOUCHER_SUCCESS
        self.logger.info(f"[核销] 双条件齐备，触发核销 order_id={order_id}")
        result_text, gsx_params = await asyncio.to_thread(
            self._query_and_verify,
            sn, coupon_no, shop_id, user_id, from_uid, order_id
        )
        status = _VOUCHER_SUCCESS if result_text and "核销成功" in result_text else _VOUCHER_FAILED
        if status == _VOUCHER_SUCCESS and gsx_params:
            goods_id, spec, report_data, device_type = gsx_params
            if device_type == "apple":
                await self._query_gsxunlocking_and_send(sn, goods_id, spec, report_data, order_id, shop_id, user_id, from_uid, result_text)
            else:
                await self._query_gsx_and_send(sn, goods_id, spec, report_data, order_id, shop_id, user_id, from_uid, result_text)
            return None, status  # 核销成功时消息已在 _query_gsx_and_send 里全部发送
        return result_text, status

    def _query_and_verify(
        self,
        serial: str,
        voucher_code: str,
        shop_id: str,
        user_id: str,
        from_uid: str,
        order_id: int,
    ) -> Tuple[str, Optional[Tuple[str, str]]]:
        """MMS 验券 + GSX查询 + 核销，返回 (result_text, gsx_params)
        顺序：preview → GSX查询（失败则提示重新提供序列号，不核销）→ verification → 更新DB
        """
        from Channel.pinduoduo.utils.API.mms_voucher_api import MmsVoucherAPI
        api = MmsVoucherAPI(shop_id=shop_id, user_id=user_id)

        preview = api.preview(voucher_code)
        if not preview["success"]:
            self._update_order_status(order_id, 2)
            # 归类为"卡券问题"：保留已填的序列号，让客户只核对卡券号
            self.logger.warning(
                f"[核销] 验券失败（卡券问题）card_no={voucher_code} err={preview.get('error_msg')}"
            )
            return (
                f"❌ 卡券核销失败：{preview.get('error_msg', '未知错误')}\n"
                f"卡券号：{voucher_code}\n"
                f"该卡券号可能无效或已被使用，请核对卡券号后重新发送（序列号已保留，无需重发）"
            ), None

        goods_name = preview.get("goods_name", "")
        spec = preview.get("spec", "")
        order_sn = preview.get("order_sn", "")
        order_amount = preview.get("order_amount", 0)
        store_id = preview.get("store_id", 0)
        goods_id = str(preview.get("goods_id", ""))

        self.logger.info(
            f"[核销] 验券成功 card_no={voucher_code} order_sn={order_sn} goods={goods_name}"
        )

        # 验机查询：核销前先验证序列号，失败则清空 sn_imei 让用户重新提供
        query_cfg = self._get_query_config(goods_id, spec)
        server_id   = query_cfg["server_id"]
        zhenxin_sid = query_cfg["zhenxin_server_id"]
        device_type = query_cfg["device_type"]
        report_data = None

        if device_type == "apple" and zhenxin_sid:
            from Channel.pinduoduo.utils.API.gsxunlocking_api import query_gsxunlocking
            zx_result = query_gsxunlocking(serial, zhenxin_sid)
            if not zx_result["success"]:
                self.logger.warning(
                    f"[核销] GsxUnlocking查询失败，序列号可能有误 sn={serial}: {zx_result.get('error_msg')}"
                )
                self._clear_order_sn(order_id)
                # 归类为"序列号问题"：只清序列号，卡券号保留
                return (
                    f"❌ 序列号/IMEI 有误：{zx_result.get('error_msg', '未知错误')}\n"
                    f"请重新提供正确的序列号或 IMEI 码（卡券号已保留，无需重发）"
                ), None
            report_data = zx_result["result"]  # dict
        elif server_id:
            from Channel.pinduoduo.utils.API.gsx_api import query_gsx
            gsx_result = query_gsx(serial, server_id)
            if not gsx_result["success"]:
                self.logger.warning(
                    f"[核销] GSX查询失败，序列号可能有误 sn={serial} key={server_id}: {gsx_result.get('error_msg')}"
                )
                self._clear_order_sn(order_id)
                # 归类为"序列号问题"：只清序列号，卡券号保留
                return (
                    f"❌ 序列号/IMEI 有误：{gsx_result.get('error_msg', '未知错误')}\n"
                    f"请重新提供正确的序列号或 IMEI 码（卡券号已保留，无需重发）"
                ), None
            report_data = gsx_result["data"]  # dict
        else:
            self.logger.warning(f"[核销] 未找到查询配置，跳过验机验证 goods_id={goods_id} spec={spec!r}")

        result = api.verification(voucher_code, store_id=store_id or 0, order_sn=order_sn or "")
        if result["success"]:
            self._update_order_status(order_id, 1)
            self.logger.info(f"[核销] 核销成功 serial={serial} card_no={voucher_code} order_id={order_id}")
            gsx_params = (goods_id, spec, report_data, device_type)
            return (
                f"核销成功！\n"
                f"序列号/IMEI：{serial}\n"
                f"卡券号：{voucher_code}\n"
                f"商品：{goods_name}\n"
                + (f"规格：{spec}\n" if spec else "")
                + f"订单号：{order_sn}\n"
                f"订单金额：{order_amount / 100:.2f}元"
            ), gsx_params
        else:
            self.logger.warning(f"[核销] 核销失败 card_no={voucher_code} order_id={order_id} err={result.get('error_msg')}")
            self._update_order_status(order_id, 2)
            # 归类为"核销环节问题"：券信息正确，核销接口本身返回失败
            return (
                f"❌ 核销失败：{result.get('error_msg', '未知错误')}\n"
                f"卡券号：{voucher_code}\n"
                f"序列号/IMEI：{serial}"
            ), None

    def _get_query_config(self, goods_id: str, spec: str) -> dict:
        """从 GoodsCommit 查询验机配置"""
        try:
            from database import db_manager
            from database.models import GoodsCommit
            with db_manager.session_scope() as session:
                gc = session.query(GoodsCommit).filter_by(
                    goods_id=goods_id, spec_name=spec
                ).first()
                if not gc:
                    return {"server_id": None, "zhenxin_server_id": None, "device_type": None}
                return {
                    "server_id":         str(gc.server_id) if gc.server_id else None,
                    "zhenxin_server_id": str(gc.zhenxin_server_id) if gc.zhenxin_server_id else None,
                    "device_type":       gc.device_type,
                }
        except Exception as e:
            self.logger.error(f"[核销] 查询验机配置失败: {e}")
            return {"server_id": None, "zhenxin_server_id": None, "device_type": None}

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

    async def _query_gsxunlocking_and_send(
        self, serial: str, goods_id: str, spec: str, report_data,
        order_id: int, shop_id: str, user_id: str, from_uid: str,
        voucher_success_text: str = ""
    ) -> None:
        """发送 GsxUnlocking 报告（苹果设备）
        发送顺序：验机报告 → AI分析 → 核销成功 → 五星好评
        """
        try:
            from database import db_manager
            from database.models import Order

            if not report_data:
                self.logger.warning(f"[核销] 无 GsxUnlocking 数据，跳过报告发送 sn={serial}")
                if voucher_success_text:
                    await self._send(shop_id, user_id, from_uid, voucher_success_text)
                return

            query_cfg = self._get_query_config(goods_id, spec)
            with db_manager.session_scope() as session:
                order = session.query(Order).filter_by(id=order_id).first()
                if order:
                    order.sku_id = query_cfg.get("zhenxin_server_id") or ""
                    order.gsx_result = str(report_data)

            # format=text 时 result 直接是纯文本字符串
            report_text = str(report_data)

            fixed = "亲，五行好平+关注+收藏+截图+文字好平赏个。以后有关于这部机器的验机问题，都可以随时联系美高技术员，技术员将为您解答！非常感谢您选择我们,祝你购机愉快~"
            session_id = f"pinduoduo{from_uid}"

            # 1. 发送验机报告
            await self._send(shop_id, user_id, from_uid, report_text)

            # 2. LLM 分析（复用 GSX 分析逻辑）
            analysis = await self._llm_analyze_gsx(report_text, session_id)
            if analysis:
                await self._send(shop_id, user_id, from_uid, analysis)

            # 3. 核销成功消息
            if voucher_success_text:
                await self._send(shop_id, user_id, from_uid, voucher_success_text)

            # 4. 固定结语
            await self._send(shop_id, user_id, from_uid, fixed)

        except Exception as e:
            self.logger.opt(exception=True).error(f"[核销] GsxUnlocking报告发送失败: {e}")

    async def _query_lingsu_and_send(
        self, serial: str, goods_id: str, spec: str, report_text: Optional[str],
        order_id: int, shop_id: str, user_id: str, from_uid: str,
        voucher_success_text: str = ""
    ) -> None:
        """苹果凌速验机报告发送流程
        发送顺序：验机报告 → AI分析 → 核销成功 → 五星好评
        """
        try:
            from database import db_manager
            from database.models import Order

            if not report_text:
                self.logger.warning(f"[核销] 无凌速验机数据，跳过报告发送 sn={serial}")
                if voucher_success_text:
                    await self._send(shop_id, user_id, from_uid, voucher_success_text)
                return

            query_cfg = self._get_query_config(goods_id, spec)
            with db_manager.session_scope() as session:
                order = session.query(Order).filter_by(id=order_id).first()
                if order:
                    order.sku_id = query_cfg.get("zhenxin_server_id") or ""
                    order.gsx_result = report_text

            fixed = "亲，五行好平+关注+收藏+截图+文字好平赏个。以后有关于这部机器的验机问题，都可以随时联系美高技术员，技术员将为您解答！非常感谢您选择我们,祝你购机愉快~"
            session_id = f"pinduoduo{from_uid}"

            # 1. 发送验机报告原文
            await self._send(shop_id, user_id, from_uid, report_text)

            # 2. LLM 分析
            analysis = await self._llm_analyze_lingsu(report_text, session_id)
            if analysis:
                await self._send(shop_id, user_id, from_uid, analysis)

            # 3. 核销成功消息
            if voucher_success_text:
                await self._send(shop_id, user_id, from_uid, voucher_success_text)

            # 4. 固定结语
            await self._send(shop_id, user_id, from_uid, fixed)

        except Exception as e:
            self.logger.opt(exception=True).error(f"[核销] 凌速报告发送失败: {e}")

    async def _llm_analyze_lingsu(self, report_text: str, session_id: str) -> str:
        """苹果凌速验机报告 LLM 分析"""
        try:
            from Agent.CustomerAgent.custom.llm_client import LLMClient
            from Agent.CustomerAgent.custom.agent_config import AgentConfig
            agent_config = AgentConfig.load_from_config()
            llm_client = LLMClient(
                model_name=agent_config.model_name,
                api_key=agent_config.api_key,
                api_base=agent_config.api_base,
                temperature=agent_config.temperature,
            )
            await llm_client.initialize()
            self.logger.info(f"[凌速分析] 使用 LLM ({llm_client.model_name})")

            prompt = (
                "你是一名专业的苹果验机顾问。根据以下验机报告，"
                "直接输出【整体状况总结与购买建议】，不要重复报告原文，不要逐项解释。\n"
                "重要规则：\n"
                "- 报告中若出现维修记录相关字段，必须如实在风险中说明，不得省略或美化。\n"
                "- 只有苹果官方/授权服务商的维修才可标注为官方维修，私下维修必须明确指出。\n"
                "- 报告中未出现维修相关字段时，不要主动提及维修情况。\n"
                "格式要求：\n"
                "✅ 优点：2-3条简短说明\n"
                "⚠️ 风险：1-5条简短说明（有维修记录时必须列出）\n"
                "🏆 建议：1-3句购买建议\n"
                "总字数控制在230字以内，如有疑问可继续询问。\n\n"
                f"验机报告：\n{report_text}"
            )

            response = await llm_client.chat(
                messages=[{"role": "user", "content": prompt}],
                tool_choice="none",
            )
            analysis = (response.content or "").strip()

            session_content = f"[已完成凌速验机查询]\n验机报告：\n{report_text}"
            if analysis:
                self.logger.info(f"[核销] 凌速分析完成，长度={len(analysis)}")
                session_content += f"\n\n分析：\n{analysis}"
            self._record_message(session_id, "assistant", session_content)
            return analysis

        except Exception as e:
            self.logger.opt(exception=True).warning(f"[核销] LLM 分析凌速报告失败: {e}")
            return ""

    async def _query_gsx_and_send(
        self, serial: str, goods_id: str, spec: str, gsx_data: Optional[dict],
        order_id: int, shop_id: str, user_id: str, from_uid: str,
        voucher_success_text: str = ""
    ) -> None:
        """发送 GSX 报告（数据已在 _query_and_verify 中获取）
        发送顺序：GSX报告 → AI分析 → 核销成功 → 五星好评
        """
        try:
            from database import db_manager
            from database.models import Order

            self.logger.info(f"[核销] _query_gsx_and_send gsx_data={type(gsx_data).__name__} empty={not gsx_data}")

            if not gsx_data:
                self.logger.warning(f"[核销] 无 GSX 数据，跳过报告发送 sn={serial}")
                if voucher_success_text:
                    await self._send(shop_id, user_id, from_uid, voucher_success_text)
                return

            query_cfg = self._get_query_config(goods_id, spec)
            with db_manager.session_scope() as session:
                order = session.query(Order).filter_by(id=order_id).first()
                if order:
                    order.sku_id = query_cfg.get("server_id") or ""
                    order.gsx_result = json.dumps(gsx_data, ensure_ascii=False)

            gsx_text = _format_gsx_result(gsx_data)
            self.logger.info(f"[核销] gsx_text 长度={len(gsx_text)} 内容预览={gsx_text[:80]!r}")
            fixed = "亲，五行好平+关注+收藏+截图+文字好平赏个。以后有关于这部机器的验机问题，都可以随时联系美高技术员，技术员将为您解答！非常感谢您选择我们,祝你购机愉快~"
            session_id = f"pinduoduo{from_uid}"

            # 1. 发送 GSX 原始报告
            await self._send(shop_id, user_id, from_uid, gsx_text)

            # 2. 复用 CustomerAgent LLM 客户端生成分析，结果写入会话历史
            analysis = await self._llm_analyze_gsx(gsx_text, session_id)
            if analysis:
                await self._send(shop_id, user_id, from_uid, analysis)

            # 3. 发送核销成功消息
            if voucher_success_text:
                await self._send(shop_id, user_id, from_uid, voucher_success_text)

            # 4. 发送固定结语
            await self._send(shop_id, user_id, from_uid, fixed)

        except Exception as e:
            self.logger.opt(exception=True).error(f"[核销] GSX查询失败: {e}")

    async def _llm_analyze_gsx(self, gsx_text: str, session_id: str) -> str:
        """用 LLM 分析 GSX 报告，并将结果写入会话历史"""
        try:
            from Agent.CustomerAgent.custom.llm_client import LLMClient
            from Agent.CustomerAgent.custom.agent_config import AgentConfig
            agent_config = AgentConfig.load_from_config()
            llm_client = LLMClient(
                model_name=agent_config.model_name,
                api_key=agent_config.api_key,
                api_base=agent_config.api_base,
                temperature=agent_config.temperature,
            )
            await llm_client.initialize()
            self.logger.info(f"[GSX分析] 使用 LLM ({llm_client.model_name})")

            prompt = (
                "你是一名专业的苹果验机顾问。根据以下 GSX 验机报告，"
                "直接输出【整体状况总结与购买建议】，不要重复报告原文，不要逐项解释。\n"
                "重要规则：\n"
                "- 报告中若出现维修记录相关字段，必须如实在风险中说明，不得省略或美化。\n"
                "- 只有苹果官方/授权服务商的维修才可标注为官方维修，私下维修必须明确指出。\n"
                "- 报告中未出现维修相关字段时，不要主动提及维修情况。\n"
                "格式要求：\n"
                "✅ 优点：2-3条简短说明\n"
                "⚠️ 风险：1-2条简短说明（有维修记录时必须列出）\n"
                "🏆 建议：1-2句购买建议\n"
                "总字数控制在200字以内，如有疑问可继续询问。\n\n"
                f"验机���告：\n{gsx_text}"
            )

            response = await llm_client.chat(
                messages=[{"role": "user", "content": prompt}],
                tool_choice="none",
            )
            analysis = (response.content or "").strip()

            session_content = f"[已完成 GSX 验机查询]\n验机报告：\n{gsx_text}"
            if analysis:
                self.logger.info(f"[核销] GSX分析完成，长度={len(analysis)}")
                session_content += f"\n\n分析：\n{analysis}"
            self._record_message(session_id, "assistant", session_content)
            return analysis

        except Exception as e:
            self.logger.opt(exception=True).warning(f"[核销] LLM 分析 GSX 报告失败: {e}")
            return ""

    def _update_order_status(self, order_id: int, status: int) -> None:
        """更新 Order 状态

        已核销成功（status=1）的单不会被后续失败结果回退成失败：
        客户把同一张已核销成功的券再发一次时，接口会返回"已核销"错误，
        此时应保留成功状态，只把错误反馈给客户。
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
        except Exception as e:
            self.logger.opt(exception=True).error(f"[核销] 更新 Order 状态失败 order_id={order_id}: {e}")

    def _record_message(self, session_id: str, role: str, content: str) -> None:
        """将消息写入 agent_messages 会话历史

        source 标记来源：客户消息记 customer，核销流程发出的话记 voucher，
        便于在库里区分"人工客服"与"程序/AI 发出"的内容。
        """
        try:
            from Agent.CustomerAgent.custom.agent_config import AgentConfig
            from Agent.CustomerAgent.custom.session_manager import SessionManager
            agent_config = AgentConfig.load_from_config()
            SessionManager(
                db_path=agent_config.db_path,
                token_window=agent_config.token_window,
                compress_ratio=agent_config.compress_ratio,
                retain_count=agent_config.retain_count,
                model_name=agent_config.model_name,
            ).add_message(
                session_id=session_id,
                role=role,
                content=content,
                source="customer" if role == "user" else "voucher",
            )
        except Exception as e:
            self.logger.warning(f"[核销] 写入会话历史失败: {e}")

    async def _send(self, shop_id: str, user_id: str, from_uid: str, text: str) -> bool:
        try:
            from Channel.pinduoduo.utils.API.send_message import SendMessage
            result = await asyncio.to_thread(
                lambda: SendMessage(shop_id, user_id).send_text(from_uid, text)
            )
            success = isinstance(result, dict) and result.get("success", False)
            if success:
                self.logger.info(f"[核销] 发送成功 from_uid={from_uid} text={text[:30]!r}")
                self._record_outgoing(shop_id, user_id, from_uid, text)
            else:
                self.logger.warning(f"[核销] 发送失败 from_uid={from_uid} result={result}")
            return success
        except Exception as e:
            self.logger.opt(exception=True).error(f"[核销] 发送回复失败 from_uid={from_uid}: {e}")
            return False

    def _record_outgoing(self, shop_id: str, user_id: str, from_uid: str, text: str) -> None:
        """将核销等场景下客服发送的回复写入消息记录表"""
        try:
            from Message.core.persistence import save_outgoing_raw
            save_outgoing_raw(shop_id=shop_id, user_id=user_id, from_uid=from_uid, content=text)
        except Exception as e:
            self.logger.warning(f"[核销] 记录客服回复失败 from_uid={from_uid}: {e}")
