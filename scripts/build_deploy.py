# -*- coding: utf-8 -*-
"""把项目打成可部署的精简包（白名单收集 + 自检 + zip）

只带「跑起来必需」的东西：源码包、配置、业务库、启动入口；
绝不带 .venv / user_data / logs / 各类缓存 / 测试产物。

用法：
    uv run python scripts/build_deploy.py                    # 带业务库与 config.json
    uv run python scripts/build_deploy.py --no-db            # 空库上线
    uv run python scripts/build_deploy.py --no-config        # 不带密钥（到服务器手填）
    uv run python scripts/build_deploy.py --with-tests       # 连自测脚本一起带
    uv run python scripts/build_deploy.py --with-browsers    # 带上 .browsers（Playwright）
"""
from __future__ import annotations

import argparse
import shutil
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 源码包：整个目录都要
SOURCE_DIRS = ["Agent", "bridge", "Channel", "core", "database", "Message", "ui", "utils", "icon"]

# 根目录单文件
ROOT_FILES = [
    "app.py", "config.py", "pyproject.toml", "uv.lock", "uv.toml",
    ".python-version", "README.md", "启动.bat", ".gitignore",
]

# 服务器上仍用得着的脚本（自测脚本默认不带）
SCRIPT_FILES = [
    "install_playwright.py", "doctor.py", "relogin_account.py", "show_messages.py",
    "capture_mms_api.py", "check_order_api.py", "build_exe.py", "build_win_exe.py",
    "agent_customer.spec", "version_info.txt", "README.md",
]

# 永远不要进包的东西（自检用）
FORBIDDEN = [
    ".venv", "user_data", "logs", "data-gym-cache", "dist", ".amazonq",
    "jieba.cache", "debug.log", "__pycache__", "temp/capture_20261028_165603",
    "temp/capture_20261028_165735", "temp/capture_20261028_181535",
    "temp/capture_round2", "temp/capture_round3", "temp/capture_round4",
    "temp/playwright", "temp/backup", "temp/baseline", "temp/stage1",
]

SKIP_SUFFIX = {".pyc", ".pyo", ".pyd"}
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache"}

DEPLOY_NOTE = """# 部署说明（Windows 服务器）

## 1. 环境
- Python >= 3.11，并安装 uv：`pip install uv`
- 把本目录放到服务器上任意路径（路径不要带奇怪的符号）

## 2. 装依赖
```cmd
uv sync
```
> 若报 `invalid peer certificate: UnknownIssuer`（公司网络做了 SSL 解密），
> 本目录已带 `uv.toml`（`system-certs = true`），直接 `uv sync` 即可。

## 3. 装浏览器内核（必须，用于拼多多登录 / Cookie 刷新）
```cmd
uv run python scripts/install_playwright.py
```
或：`uv run playwright install chromium`

## 4. 确认配置
- `config.json`：LLM 地址/密钥/模型、GSX 与真心验机密钥、工作时间
- 业务库：`temp/channel_shop.db`（知识库、商品与验机配置、历史消息）

## 5. 启动
```cmd
uv run python app.py
```
或双击 `启动.bat`。

## 6. 上线前自检
```cmd
uv run python scripts/doctor.py
```
启动时会自动拉起「在线」账号的自动回复；平台不会把消息推给同一账号的第二个连接，
所以同一个号不要在本机与服务器同时登。

## 注意
- 本包**不含**登录 Cookie（`user_data/`）：服务器首次需要在「账号管理」里重新登录。
- 本包不含依赖（`.venv/`）、日志与缓存，它们会在服务器上重新生成。
- 同一账号只允许一处在线，切服务器前先停掉本机程序。
"""


def log(msg: str) -> None:
    print(msg, flush=True)


def skip_file(path: Path) -> bool:
    if path.suffix in SKIP_SUFFIX:
        return True
    return any(part in SKIP_DIRS for part in path.parts)


def copy_tree(src: Path, dst_root: Path, counter: dict) -> None:
    for item in sorted(src.rglob("*")):
        if item.is_dir() or skip_file(item):
            continue
        rel = item.relative_to(ROOT)
        target = dst_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)
        counter["files"] += 1
        counter["bytes"] += item.stat().st_size


def copy_one(rel: str, dst_root: Path, counter: dict, required: bool = True) -> bool:
    src = ROOT / rel
    if not src.exists():
        if required:
            log(f"  [缺] {rel}")
        return False
    target = dst_root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, target)
    counter["files"] += 1
    counter["bytes"] += src.stat().st_size
    return True


def build(args) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    pkg = ROOT / args.out / f"CustomerAgent_deploy_{stamp}"
    if pkg.exists():
        shutil.rmtree(pkg)
    pkg.mkdir(parents=True)
    counter = {"files": 0, "bytes": 0}
    groups: list[tuple[str, int, int]] = []

    def snapshot() -> tuple[int, int]:
        return counter["files"], counter["bytes"]

    # 1. 源码包
    before = snapshot()
    for name in SOURCE_DIRS:
        src = ROOT / name
        if not src.exists():
            log(f"  [缺] {name}/")
            continue
        copy_tree(src, pkg, counter)
    groups.append(("源码包", *map(lambda a, b: a - b, snapshot(), before)))

    # 2. 根文件
    before = snapshot()
    for rel in ROOT_FILES:
        copy_one(rel, pkg, counter)
    groups.append(("根文件", *map(lambda a, b: a - b, snapshot(), before)))

    # 3. 脚本
    before = snapshot()
    names = list(SCRIPT_FILES)
    if args.with_tests:
        names += [p.name for p in (ROOT / "scripts").glob("test_*.py")]
    for name in names:
        copy_one(f"scripts/{name}", pkg, counter, required=False)
    groups.append(("脚本", *map(lambda a, b: a - b, snapshot(), before)))

    # 4. 配置
    before = snapshot()
    if args.no_config:
        log("  [跳过] config.json（--no-config）")
    else:
        copy_one("config.json", pkg, counter)
    groups.append(("配置", *map(lambda a, b: a - b, snapshot(), before)))

    # 5. 业务库
    before = snapshot()
    if args.no_db:
        log("  [跳过] temp/channel_shop.db（--no-db）")
    else:
        copy_one("temp/channel_shop.db", pkg, counter)
    groups.append(("业务库", *map(lambda a, b: a - b, snapshot(), before)))

    # 6. Playwright 浏览器（可选，通常服务器上装）
    before = snapshot()
    if args.with_browsers:
        browsers = ROOT / ".browsers"
        if browsers.exists():
            copy_tree(browsers, pkg, counter)
        else:
            log("  [跳过] .browsers 不存在；请到服务器执行 scripts/install_playwright.py")
    groups.append(("浏览器内核", *map(lambda a, b: a - b, snapshot(), before)))

    # 7. 运行时目录（空壳）
    for d in ("logs", "user_data", "temp"):
        (pkg / d).mkdir(parents=True, exist_ok=True)
    (pkg / "部署说明.md").write_text(DEPLOY_NOTE, encoding="utf-8")
    counter["files"] += 1
    counter["bytes"] += len(DEPLOY_NOTE.encode("utf-8"))

    # ---- 自检：包里绝不能出现这些 ----
    problems = []
    for rel in FORBIDDEN:
        target = pkg / rel
        if not target.exists():
            continue
        if target.is_dir():
            # 允许留空壳（logs/ user_data/ 由程序自己写），但绝不能带内容
            if any(p.is_file() for p in target.rglob("*")):
                problems.append(f"{rel} 不应包含文件")
        else:
            problems.append(rel)
    if not args.no_db and not (pkg / "temp/channel_shop.db").exists():
        problems.append("业务库缺失（预期带库）")
    if not args.no_config and not (pkg / "config.json").exists():
        problems.append("config.json 缺失（预期带配置）")
    for required in ("app.py", "config.py", "Agent", "Message", "ui", "database", "core"):
        if not (pkg / required).exists():
            problems.append(f"缺少必需项 {required}")

    log("\n=== 打包结果 ===")
    for name, files, size in groups:
        if files:
            log(f"  {name:<6} {files:>4} 个文件  {size / 1024 / 1024:>7.2f} MB")
    log(f"  {'合计':<6} {counter['files']:>4} 个文件  {counter['bytes'] / 1024 / 1024:>7.2f} MB")

    if problems:
        log("\n[失败] 自检不通过: " + "; ".join(problems))
        sys.exit(1)
    log("[自检] 未包含 .venv / user_data / logs / 缓存 / 测试产物 ✓")

    # ---- 打 zip ----
    if not args.no_zip:
        zip_path = pkg.with_suffix(".zip")
        if zip_path.exists():
            zip_path.unlink()
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for item in sorted(pkg.rglob("*")):
                if item.is_file():
                    zf.write(item, item.relative_to(pkg.parent))
        log(f"[zip] {zip_path.relative_to(ROOT)}  {zip_path.stat().st_size / 1024 / 1024:.2f} MB")

    log(f"\n目录: {pkg}")
    return pkg


def main() -> int:
    ap = argparse.ArgumentParser(description="打精简部署包")
    ap.add_argument("--out", default="dist", help="输出目录（默认 dist）")
    ap.add_argument("--no-db", action="store_true", help="不带业务库 temp/channel_shop.db")
    ap.add_argument("--no-config", action="store_true", help="不带 config.json（密钥到服务器手填）")
    ap.add_argument("--with-tests", action="store_true", help="带上 scripts/test_*.py")
    ap.add_argument("--with-browsers", action="store_true", help="带上 .browsers 浏览器内核")
    ap.add_argument("--no-zip", action="store_true", help="只出目录，不打 zip")
    args = ap.parse_args()
    log("=== 收集文件（白名单）===")
    build(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
