# -*- coding: utf-8 -*-
"""卡券号提取回归：ICCID / IMEI / 序列号 不得被当成卡券号（离线）

背景：线上 2026-11-02 16:43 把截图里的 ICCID `898600c6112484013529`
中间的 13 位当成了卡券号，触发核销后必然「未找到券码对应订单」。

运行：uv run python scripts/test_coupon_extract.py
"""
import os
import sys

os.environ["LOG_FILE"] = "logs/test.log"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Message.handlers.voucher.parsers import (      # noqa: E402
    _extract_coupon, _extract_coupon_strict, _extract_imei, _extract_serial,
)

_results = {"pass": 0, "fail": 0}


def check(name, cond, detail=""):
    if cond:
        _results["pass"] += 1
        print(f"  [PASS] {name}")
    else:
        _results["fail"] += 1
        print(f"  [FAIL] {name}  <- {detail}")


# 线上真实 OCR 文本（关于本机截图）
OCR_TEXT = """16:42

关于本机

总容量 256 GB
可用容量 178.04 GB
无线局域网地址 OC:6F:8B:A1:6C:46
蓝牙 OC:6F:8B:92:99:98
调制解调器固件 2.20.04
SEID
运营商锁 无SIM卡限制
IMEI 35 739469 194696 2
网络 中国移动
运营商 中国移动 73.0
IMEI2 35 739469 171954 2
ICCID 898600c6112484013529
证书信任设置
"""

print("1) 线上真实截图：ICCID 不得当卡券号")
check("ICCID 不被识别为卡券号", _extract_coupon(OCR_TEXT) is None, _extract_coupon(OCR_TEXT))
check("严格模式也不认 ICCID", _extract_coupon_strict(OCR_TEXT) is None,
      _extract_coupon_strict(OCR_TEXT))
check("IMEI 仍能正常提取", _extract_imei(OCR_TEXT) == "357394691946962", _extract_imei(OCR_TEXT))

print("2) 正常卡券号仍要认得")
for text, expect in (
    ("卡券 888401748943", "888401748943"),
    ("卡券号：888401748943", "888401748943"),
    ("卡券号888401748943", "888401748943"),
    ("1234 5678 9012", "123456789012"),
):
    got = _extract_coupon(text)
    check(f"识别 {text!r}", got == expect, got)

print("3) 序列号/IMEI 不得被当卡券号")
check("序列号文本无卡券", _extract_coupon("序列号 VQ627F3QQH") is None, _extract_coupon("序列号 VQ627F3QQH"))
check("IMEI 文本无卡券", _extract_coupon("IMEI 357394691946962") is None,
      _extract_coupon("IMEI 357394691946962"))
check("IMEI2 段不误判", _extract_coupon("IMEI2 35 739469 171954 2") is None,
      _extract_coupon("IMEI2 35 739469 171954 2"))

print("4) 严格模式：长数字串里的子串不认")
check("16 位长串不截断当卡券", _extract_coupon_strict("898600c6112484013529") is None,
      _extract_coupon_strict("898600c6112484013529"))
check("严格模式正常卡券仍认", _extract_coupon_strict("卡券 888401748943") == "888401748943",
      _extract_coupon_strict("卡券 888401748943"))

print("5) 序列号提取不受影响")
check("序列号提取正常", _extract_serial("序列号 VQ627F3QQH") == "VQ627F3QQH",
      _extract_serial("序列号 VQ627F3QQH"))

total = _results["pass"] + _results["fail"]
print(f"\n卡券号提取回归：{_results['pass']}/{total} 通过")
sys.exit(0 if _results["fail"] == 0 else 1)
