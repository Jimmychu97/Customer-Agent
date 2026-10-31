"""
纯文本解析：IMEI / 苹果序列号 / 卡券号 / 拼多多订单号 / 结构化内容。

全部无状态、不碰 self，可独立单测。
"""
import json
import re
from typing import Optional

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

# 拼多多订单号：6 位日期 - 序号段。序号段长度实测既有 15 位（261028-423740065360537）
# 也有 12 位（261029-401373929091）。老规则只认 15 位，导致订单号被整条丢弃、来回索要序列号。
_ORDER_SN_RE = re.compile(r'(?<!\d)(\d{6}-\d{8,20})(?!\d)')

def _strip_order_sn(text: str) -> str:
    """去掉订单号，避免它的序号段（10-14 位）被误当成卡券号"""
    if not text:
        return ""
    out = str(text)
    for ch in ("－", "—", "–", "─"):
        out = out.replace(ch, "-")
    return _ORDER_SN_RE.sub(" ", out)

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
    text = _strip_order_sn(text)
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
    text = _strip_order_sn(text)
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

def _extract_order_sn(text: str) -> Optional[str]:
    r"""提取拼多多订单号（形如 261028-423740065360537 / 261029-401373929091）

    序号段 8-20 位都认；全角/长破折号先归一化成半角 '-'。
    与卡券号(10-14位)、IMEI(15位)规则互斥，不会被当卡券号。
    """
    if not text:
        return None
    normalized = str(text)
    for ch in ("－", "—", "–", "─"):
        normalized = normalized.replace(ch, "-")
    m = _ORDER_SN_RE.search(normalized)
    return m.group(1) if m else None

def _parse_json_content(content) -> Optional[dict]:
    """把 Context.content（dict 或 JSON 字符串）解析成 dict，非结构化内容返回 None"""
    try:
        data = content if isinstance(content, dict) else json.loads(content)
    except Exception:
        return None
    data = data[0] if isinstance(data, list) and data else data
    return data if isinstance(data, dict) else None
