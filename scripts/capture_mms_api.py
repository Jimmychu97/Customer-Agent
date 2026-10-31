# -*- coding: utf-8 -*-
"""拼多多商家后台接口录制脚本（人工点页面，程序抓包）

打开已登录的真实浏览器窗口，人工依次点开【订单查询】与【发货】；期间所有发往
mms.pinduoduo.com 的 xhr/fetch/document 请求与响应逐条写入 raw.jsonl（含请求体与响应体），
并生成 summary.md（接口清单 + 样例）。脚本不含任何硬编码业务接口路径：接口由抓包结果决定，
--url 只是默认起始页。用法见 --help；退出码 0 = 正常/自检通过，1 = 失败。
"""
import argparse, asyncio, contextlib, json, os, sys, threading, time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
with contextlib.suppress(Exception):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from utils.path_utils import get_app_dir
from utils.logger_loguru import get_logger

logger = get_logger("CaptureMmsApi")
TARGET_HOST = "mms.pinduoduo.com"
DEFAULT_URL = "https://mms.pinduoduo.com/orders/order/"
RESOURCE_TYPES = {"xhr", "fetch", "document"}
MAX_BODY_BYTES = 200 * 1024
# 与 Channel/pinduoduo/pdd_login.py 的 launch_kwargs.args 一致
LAUNCH_ARGS = ["--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage",
               "--disable-blink-features=AutomationControlled",
               "--disable-notifications", "--disable-web-security",
               "--disable-features=VizDisplayCompositor"]
PROMPT = ("\n已打开商家后台。请依次操作：① 点开【订单查询】看清待发货列表 "
          "② 对其中一单点【发货】并完成 ③ 回到本终端按 Enter 结束录制\n")
FALLBACK_KEYS = {"password", "cookies", "token", "api_key", "access_token",
                 "anti-content", "anti_content", "cookie", "authorization"}

def _sensitive_keys() -> set:
    """脱敏键名以 BaseRequest._SENSITIVE_KEYS 为准，另补 cookie/authorization"""
    try:
        from Channel.pinduoduo.utils.base_request import BaseRequest
        return set(BaseRequest._SENSITIVE_KEYS) | {"cookie", "authorization"}
    except Exception as e:  # 导入失败不应让抓包起不来
        logger.warning(f"读取 BaseRequest._SENSITIVE_KEYS 失败，用内置清单: {e}")
        return set(FALLBACK_KEYS)

def _prepare_env() -> None:
    """必须在使用 playwright 之前调用：复用 pdd_login 的浏览器目录选择逻辑"""
    browsers = get_app_dir() / ".browsers"
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers) if browsers.exists() else (
        os.path.join(os.getenv("LOCALAPPDATA", ""), "ms-playwright"))

def _user_dir(account: str) -> Path:
    return get_app_dir() / "user_data" / account

def _out_dir(args) -> Path:
    return Path(args.out) if args.out else ROOT / "temp" / f"capture_{datetime.now():%Y%m%d_%H%M%S}"

def _resolve_account(explicit):
    """显式传入优先；否则取数据库第一个账号的 username"""
    if explicit:
        return explicit
    from database import db_manager
    rows = db_manager.get_all_accounts_with_details() or []
    return next((r["username"] for r in rows if r.get("username")), None)

def _guess_purpose(url: str, body) -> str:
    """按 url/请求体关键词标注疑似用途"""
    text = f"{url} {body or ''}".lower()
    for hit, label in (("order" in text and "list" in text, "订单列表"),
                       ("order" in text and ("detail" in text or "info" in text), "订单详情"),
                       (any(k in text for k in ("ship", "deliver", "consign")), "发货"),
                       ("logistics" in text, "物流")):
        if hit:
            return label
    return ""

class Recorder:
    """边录边写 raw.jsonl；request 先挂起，response 到达后按 url+method 配对"""
    def __init__(self, out_dir: Path):
        self.raw_path, self.records, self._seq = out_dir / "raw.jsonl", [], 0
        self._sensitive, self._pending = _sensitive_keys(), {}
        self._fh = self.raw_path.open("w", encoding="utf-8")

    @staticmethod
    def _wanted(url: str, rtype: str) -> bool:
        try:
            return (urlparse(url).hostname or "").lower() == TARGET_HOST \
                and rtype in RESOURCE_TYPES
        except Exception:
            return False

    def on_request(self, request) -> None:
        try:
            if self._wanted(request.url, request.resource_type):
                self._pending.setdefault((request.method, request.url), []).append(
                    (time.time(), request))
        except Exception as e:
            logger.warning(f"请求记录失败（已跳过该条）: {e}")

    async def on_response(self, response) -> None:
        try:
            req = response.request
            if not self._wanted(req.url, req.resource_type):
                return
            queue = self._pending.get((req.method, req.url)) or []
            ts, req = queue.pop(0) if queue else (time.time(), req)
            text = (await response.body()).decode("utf-8", "replace")
            try:
                json.loads(text)
            except Exception:
                text = None  # 非 JSON 一律记 null
            over = bool(text) and len(text) > MAX_BODY_BYTES
            self._write(ts, req, response.status, req.post_data,
                        text[:MAX_BODY_BYTES] if over else text, over)
        except Exception as e:  # 单条失败只记日志，不中断整体录制
            logger.warning(f"响应记录失败（已跳过该条）: {e}")

    def _write(self, ts, req, status, req_body, resp_body, truncated) -> None:
        self._seq += 1
        heads = {k: ("***" if any(s in str(k).lower() for s in self._sensitive) else v)
                 for k, v in (req.headers or {}).items()}
        record = {"seq": self._seq, "method": req.method, "url": req.url, "status": status,
                  "ts": datetime.fromtimestamp(ts).isoformat(timespec="milliseconds"),
                  "req_headers": heads, "req_body": req_body, "resp_body": resp_body,
                  "resource_type": req.resource_type}
        record.update({"truncated": True} if truncated else {})
        self.records.append(record)
        try:
            self._fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._fh.flush()
        except Exception as e:
            logger.warning(f"写入 raw.jsonl 失败: {e}")

    def close(self) -> None:
        """收尾：未配对的请求也落盘，再关闭文件"""
        try:
            for queue in list(self._pending.values()):
                for ts, req in queue:
                    self._write(ts, req, None, req.post_data, None, False)
            self._fh.close()
        except Exception as e:
            logger.warning(f"关闭 raw.jsonl 失败: {e}")

    def summary(self, out_dir, account, url, seconds) -> Path:
        """接口清单 + 逐个接口详情（anti-content 标记、请求/响应样例、顶层字段）"""
        groups = {}
        for record in self.records:
            parts = urlparse(record["url"])
            groups.setdefault((record["method"],
                               f"{parts.scheme}://{parts.netloc}{parts.path}"),
                              []).append(record)
        rows, blocks = [], []
        for i, ((method, base), items) in enumerate(groups.items(), 1):
            codes = "/".join(sorted({str(x.get("status")) for x in items}))
            first, heads = items[0], items[0].get("req_headers") or {}
            anti = any(str(k).lower() in ("anti-content", "anti_content")
                       and str(v).strip() for k, v in heads.items())
            resp = next((x["resp_body"] for x in items if x.get("resp_body")), None)
            try:
                top = json.dumps(list(json.loads(resp).keys()), ensure_ascii=False)
            except Exception:
                top = "[]"
            rows.append(f"| {i} | {method} | {base} | {len(items)} | {codes} | "
                        f"{_guess_purpose(base, first.get('req_body'))} |")
            blocks += [f"### {i}. {method} {base}",
                       f"- 出现次数：{len(items)}    状态码：{codes}",
                       f"- 是否需要 anti-content：{'是' if anti else '否'}"
                       "（请求头里是否带且非空）",
                       "- 请求体样例（第一条）：", "```json",
                       first.get("req_body") or "null", "```",
                       "- 响应体样例（第一条，非 JSON 则原样截断）：", "```json",
                       (resp or "null")[:2000], "```",
                       f"- 响应体顶层字段：`{top}`", ""]
        lines = [f"# 抓包摘要 {datetime.now():%Y-%m-%d %H:%M:%S}", "",
                 f"账号：{account}    起始页：{url}    录制时长：{int(seconds)} 秒    "
                 f"记录数：{len(self.records)}", "", "## 接口清单", "",
                 "| # | Method | URL(去掉 query) | 次数 | 状态码 | 疑似用途 |",
                 "| --- | --- | --- | --- | --- | --- |"] + rows
        path = out_dir / "summary.md"
        path.write_text("\n".join(lines + ["", "## 逐个接口详情", ""] + blocks) + "\n",
                        encoding="utf-8")
        return path

async def _capture(args, account, recorder, holder) -> None:
    """录制线程：开浏览器、挂监听、等 stop 置位；任何异常都保证关闭 context"""
    from playwright.async_api import async_playwright
    holder["loop"] = asyncio.get_running_loop()
    stop = holder["stop"] = asyncio.Event()
    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            str(_user_dir(account)), headless=False, args=list(LAUNCH_ARGS))
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            page.on("request", recorder.on_request)
            page.on("response", recorder.on_response)
            await page.goto(args.url, wait_until="domcontentloaded")
            print(PROMPT, flush=True)
            print(f"READY {recorder.raw_path.parent.resolve()}", flush=True)
            holder["ready"].set()
            if args.stop_file:
                # 非交互运行：轮询文件信号，出现即停止（不阻塞事件循环）
                signal_file = Path(args.stop_file)
                while not stop.is_set() and not signal_file.exists():
                    await asyncio.sleep(0.5)
                signal_file.unlink(missing_ok=True)
            else:
                await stop.wait()
        finally:
            with contextlib.suppress(Exception):
                await context.close()

def _self_check(args) -> int:
    """逐项校验环境（不启动浏览器）；全部通过打印 SELF-CHECK PASSED 并返回 0"""
    _prepare_env()
    out_dir, checks, account = _out_dir(args), [], args.account
    try:
        account = account or _resolve_account(None)
    except Exception as e:
        checks.append((False, "账号解析", f"数据库读取失败: {e}"))
    ok_dir = bool(account and _user_dir(account).is_dir())
    checks.append((ok_dir, f"账号目录({account})",
                   f"{_user_dir(account)} 存在={ok_dir}" if account
                   else "未传 --account 且库里无账号"))
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        checks.append((True, "输出目录", str(out_dir)))
    except Exception as e:
        checks.append((False, "输出目录", f"{out_dir} 无法创建: {e}"))
    for name in ("raw.jsonl", "summary.md"):
        try:
            (out_dir / name).write_text("", encoding="utf-8")
            (out_dir / name).unlink()
            checks.append((True, f"可写文件 {name}", str(out_dir / name)))
        except Exception as e:
            checks.append((False, f"可写文件 {name}", f"{out_dir / name} 不可写: {e}"))
    if args.stop_file:
        signal_file = Path(args.stop_file)
        try:
            signal_file.parent.mkdir(parents=True, exist_ok=True)
            signal_file.write_text("", encoding="utf-8")
            signal_file.unlink()
            checks.append((True, "停止信号文件", str(signal_file)))
        except Exception as e:
            checks.append((False, "停止信号文件", f"{signal_file} 不可写: {e}"))
    try:
        from playwright.async_api import async_playwright  # noqa: F401
        checks.append((True, "Playwright 导入", os.environ["PLAYWRIGHT_BROWSERS_PATH"]))
    except Exception as e:
        checks.append((False, "Playwright 导入", str(e)))
    parts = urlparse(args.url)
    valid = parts.scheme in ("http", "https") and bool(parts.netloc)
    checks.append((valid, "目标 URL", args.url if valid else f"非法 URL: {args.url!r}"))
    for passed, title, detail in checks:
        print(f"[{'OK' if passed else 'FAIL'}] {title}: {detail}")
    good = all(item[0] for item in checks)
    print("SELF-CHECK PASSED" if good else "SELF-CHECK FAILED")
    return 0 if good else 1

def _record(args, account, recorder) -> bool:
    """后台线程跑录制；无 --stop-file 时主线程 input() 等 Enter，有则直接 join 录制线程"""
    holder = {"ready": threading.Event()}

    def _worker():
        try:
            asyncio.run(_capture(args, account, recorder, holder))
        except Exception as e:
            holder["error"] = str(e)
            logger.error(f"录制失败: {e}")
        finally:
            holder["ready"].set()

    def _stop():
        loop = holder.get("loop")
        if loop and holder.get("stop"):
            with contextlib.suppress(Exception):
                loop.call_soon_threadsafe(holder["stop"].set)

    worker = threading.Thread(daemon=True, target=_worker)
    worker.start()
    if not holder["ready"].wait(300) or "error" in holder:
        print(f"[FAIL] 浏览器启动失败: {holder.get('error', '等待超时（300 秒）')}")
        return False
    timer = threading.Timer(args.timeout, lambda: (
        print(f"\n[超时] 录制已达 {args.timeout} 秒上限，已停止录制", flush=True),
        _stop())) if args.timeout > 0 else None
    if timer:
        timer.start()
    try:
        if args.stop_file:
            # 非交互：stdin 无关紧要，一直等到录制线程自行结束（文件信号或超时）
            print(f"等待停止信号文件: {Path(args.stop_file).resolve()}", flush=True)
            worker.join()
        else:
            try:
                input("\n按 Enter 结束录制...")
            except (EOFError, KeyboardInterrupt):
                pass
            _stop()
            worker.join(60)
    finally:
        timer and timer.cancel()
    return True

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="录制拼多多商家后台订单/发货接口（人工点页面）")
    parser.add_argument("--account", default=None,
                        help="登录名（对应 user_data/<用户名>），默认取数据库第一个账号")
    parser.add_argument("--out", default=None, help="产物目录，默认 temp/capture_<时间戳>")
    parser.add_argument("--url", default=DEFAULT_URL, help=f"起始页，默认 {DEFAULT_URL}")
    parser.add_argument("--self-check", action="store_true", help="只做自检，不启动浏览器")
    parser.add_argument("--stop-file", default=None,
                        help="非交互运行：该文件出现即结束录制（给定时不再等待 Enter）")
    parser.add_argument("--timeout", type=int, default=1800, help="录制超时秒数，默认 1800")
    args = parser.parse_args(argv)
    if args.self_check:
        return _self_check(args)
    _prepare_env()
    account = _resolve_account(args.account)
    if not account:
        print("[FAIL] 未指定 --account，且数据库中没有可用账号")
        return 1
    out_dir = _out_dir(args)
    out_dir.mkdir(parents=True, exist_ok=True)
    if not _user_dir(account).is_dir():
        print(f"[WARN] 账号目录不存在（可能尚未登录过）: {_user_dir(account)}")
    recorder, started = Recorder(out_dir), time.time()
    try:
        if not _record(args, account, recorder):
            return 1
    finally:
        recorder.close()
    summary = recorder.summary(out_dir, account, args.url, time.time() - started)
    print(f"\n录制结束：共 {len(recorder.records)} 条记录")
    print(f"原始数据: {recorder.raw_path.resolve()}")
    print(f"摘要文件: {summary.resolve()}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
