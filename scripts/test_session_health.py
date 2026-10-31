# -*- coding: utf-8 -*-
"""会话心跳自测（离线，不联网、不启动浏览器）

H1 登录失效 → 自动重新登录一次，重登后状态变有效
H2 重登冷却期内不再重复登录
H3 登录有效且到刷新周期 → 刷新 cookies 保活
H4 ORDER_HEARTBEAT=0 时心跳不工作

运行：uv run python scripts/test_session_health.py
"""
import os
import sys

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("LOG_FILE", "logs/test.log")
os.environ["ORDER_HEARTBEAT"] = "1"
os.environ["ORDER_HEARTBEAT_INTERVAL"] = "180"
os.environ["ORDER_COOKIE_REFRESH_HOURS"] = "6"
os.environ["ORDER_RELOGIN_COOLDOWN"] = "1800"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import loguru

_o = loguru.logger.add
loguru.logger.add = lambda *a, **k: (k.__setitem__("enqueue", False), _o(*a, **k))[1]

from core import session_health as sh

_results = {"pass": 0, "fail": 0}


def check(name, ok, detail=""):
    if ok:
        _results["pass"] += 1
        print("  [PASS] %s" % name)
    else:
        _results["fail"] += 1
        print("  [FAIL] %s | %r" % (name, detail))


ACCOUNT = {"shop_id": "652909334", "user_id": "110997513",
           "username": "13431560467", "cookies": '{"a": "b"}'}


def reset_state():
    with sh._lock:
        sh._state.clear()
        for k in sh._checks:
            sh._checks[k] = 0


def main():
    print("H1 登录失效 → 自动重新登录")
    reset_state()
    sh._load_accounts = lambda: [dict(ACCOUNT)]
    valid_calls = {"n": 0}

    def _valid(account):
        valid_calls["n"] += 1
        return valid_calls["n"] > 1     # 第一次失效，重登后有效

    relogins = []
    sh._check_valid = _valid
    sh._relogin = lambda account: (relogins.append(account["username"]), True)[1]
    sh._refresh = lambda account: True
    state = sh.check_once(now=1000.0)
    check("H1 触发了 1 次重新登录", relogins == ["13431560467"], relogins)
    check("H1 重登后状态为有效",
          state["accounts"]["652909334:110997513"]["valid"] is True, state["accounts"])

    print("H2 冷却期内不重复登录")
    reset_state()
    valid_calls["n"] = 0
    relogins.clear()
    sh._check_valid = lambda account: False       # 一直失效
    sh.check_once(now=1000.0)
    sh.check_once(now=1100.0)                      # 100s 后，仍在 1800s 冷却内
    check("H2 只登录了 1 次", len(relogins) == 1, relogins)

    print("H3 有效且到刷新周期 → 刷新 cookies 保活")
    reset_state()
    refreshes = []
    sh._check_valid = lambda account: True
    sh._relogin = lambda account: True
    sh._refresh = lambda account: (refreshes.append(1), True)[1]
    sh.check_once(now=20000.0)                     # 首次：距上次刷新 0 → 会刷新
    count_after_first = len(refreshes)
    sh.check_once(now=20000.0 + 3600.0)            # 1 小时后：不到 6 小时周期
    check("H3 首次/到周期才刷新", count_after_first >= 1 and len(refreshes) == count_after_first,
          (count_after_first, len(refreshes)))

    print("H4 ORDER_HEARTBEAT=0 时心跳不工作")
    sh._ENABLED = False
    reset_state()
    relogins.clear()
    sh._check_valid = lambda account: False
    sh.check_once(now=40000.0)
    check("H4 关闭时不检查不登录", relogins == [] and not sh._state, (relogins, sh._state))
    sh._ENABLED = True

    print("\n合计: %d 通过 / %d 失败" % (_results["pass"], _results["fail"]))
    return 0 if _results["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
