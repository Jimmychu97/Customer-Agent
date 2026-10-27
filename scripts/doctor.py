# -*- coding: utf-8 -*-
"""部署自检脚本（只读）

用途：一眼看出"运行中的代码"是否完整 —— 特别是历史上出现过的
"新版调用 + 旧版被调"混合部署问题：
    WARNING | Message.core.persistence:save_message_record:64 - 消息落库失败
    direction=out from_uid=...: 'DatabaseManager' object has no attribute 'save_message'

检查内容：
1. 关键文件的路径 / 修改时间 / 是否含关键符号
2. 运行实例（DI 容器里的 DatabaseManager）是否具备 save_message、session_scope
3. 数据库里 message_records / agent_messages 表是否存在（含 agent_messages.source 列）
4. 消息记录统计（direction + msg_type）

用法：
    PYTHONIOENCODING=utf-8 python scripts/doctor.py
    PYTHONIOENCODING=utf-8 python scripts/doctor.py --db ./temp/channel_shop.db

退出码：0 = 全绿；1 = 存在问题（可直接用于部署脚本）
"""
import argparse
import ast
import os
import sys
import time

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# (相对路径, 必须存在的符号)
REQUIRED_FILES = [
    ("database/db_manager.py", ["DatabaseManager", "save_message", "message_exists"]),
    ("database/models.py", ["MessageRecord"]),
    ("database/__init__.py", ["db_manager"]),
    ("Message/core/persistence.py", ["save_message_record", "check_deployment",
                                     "record_agent_session_message", "save_outgoing_raw"]),
    ("Agent/CustomerAgent/custom/session_manager.py", ["AgentMessage", "add_message"]),
    ("Channel/pinduoduo/core/pdd_message_handler.py", ["_record_mall_cs_message"]),
]

# 阻塞性问题（会让消息记录不可用）
problems = []
# 非阻塞提示（可自动修复或仅影响可读性）
notes = []


def _symbols_in(path, wanted):
    """用 AST 静态扫描文件里定义了哪些函数/类（不执行代码）"""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            tree = ast.parse(f.read())
    except Exception as e:
        return set(), f"解析失败: {e}"
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    found.add(target.id)
    return found, None


def check_files():
    print("=" * 74)
    print("[1/4] 关键文件检查（路径 / 修改时间 / 关键符号）")
    print("=" * 74)
    for rel, wanted in REQUIRED_FILES:
        path = os.path.join(ROOT, rel)
        if not os.path.exists(path):
            print(f"  [缺失] {rel}")
            problems.append(f"{rel} 不存在（部署不完整）")
            continue
        mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(path)))
        found, err = _symbols_in(path, wanted)
        missing = [s for s in wanted if s not in found]
        if err:
            print(f"  [警告] {rel}  mtime={mtime}  {err}")
            problems.append(f"{rel} 解析失败: {err}")
        elif missing:
            print(f"  [异常] {rel}  mtime={mtime}  缺少符号: {', '.join(missing)}")
            problems.append(f"{rel} 缺少符号: {', '.join(missing)}（该文件版本过旧）")
        else:
            print(f"  [OK]   {rel}  mtime={mtime}")


def check_runtime(db_path=None):
    print()
    print("=" * 74)
    print("[2/4] 运行实例检查（DI 容器中的 DatabaseManager）")
    print("=" * 74)
    try:
        from database.db_manager import DatabaseManager, get_db_manager
    except Exception as e:
        print(f"  [错误] 无法导入 database.db_manager: {e}")
        problems.append(f"无法导入 database.db_manager: {e}")
        return None

    if db_path:
        try:
            inst = DatabaseManager(db_path=db_path)
            print(f"  使用 --db 指定的数据库: {db_path}")
        except Exception as e:
            print(f"  [错误] 打不开指定数据库 {db_path}: {e}")
            problems.append(f"打不开数据库 {db_path}: {e}")
            return None
    else:
        try:
            inst = get_db_manager()
        except Exception as e:
            print(f"  [错误] 获取 DatabaseManager 失败: {e}")
            problems.append(f"获取 DatabaseManager 失败: {e}")
            return None

    try:
        from database.db_manager import OUTGOING_DEDUP_SECONDS
        dedup = f"{OUTGOING_DEDUP_SECONDS}s"
    except Exception:
        dedup = "未知（db_manager.py 版本过旧）"
        notes.append("database/db_manager.py 缺少 OUTGOING_DEDUP_SECONDS（版本过旧，无法做内容级去重）")

    try:
        import inspect
        cls_file = inspect.getfile(type(inst))
    except Exception:
        cls_file = getattr(type(inst), "__module__", "?")

    has_save = hasattr(inst, "save_message")
    has_scope = hasattr(inst, "session_scope")
    print(f"  实例类      : {type(inst).__name__}")
    print(f"  类定义文件  : {cls_file}")
    print(f"  save_message: {'有' if has_save else '【没有】'}   session_scope: {'有' if has_scope else '没有'}")
    print(f"  out 去重窗口: {dedup}")

    if not has_save:
        problems.append(
            "DatabaseManager 没有 save_message（database/db_manager.py 版本过旧，"
            "与 Message/core/persistence.py 不匹配）"
        )
    return inst


def check_tables(inst):
    print()
    print("=" * 74)
    print("[3/4] 数据库表检查")
    print("=" * 74)
    if inst is None:
        print("  [跳过] 实例不可用")
        return
    try:
        db_file = inst.engine.url.database
        print(f"  数据库文件  : {db_file}  (存在: {os.path.exists(db_file) if db_file else '?'})")
    except Exception as e:
        print(f"  [警告] 无法获取数据库路径: {e}")
        db_file = None

    from sqlalchemy import text as _sql_text

    def table_exists(name):
        try:
            with inst.engine.connect() as conn:
                row = conn.execute(
                    _sql_text("SELECT name FROM sqlite_master WHERE type='table' AND name=:n"),
                    {"n": name},
                ).fetchone()
            return row is not None
        except Exception as e:
            print(f"  [警告] 检查表 {name} 失败: {e}")
            return False

    has_records = table_exists("message_records")
    print(f"  message_records 表 : {'存在' if has_records else '【不存在】'}")
    if not has_records:
        problems.append("message_records 表不存在（database/models.py 版本过旧，或数据库未初始化）")

    has_agent = table_exists("agent_messages")
    print(f"  agent_messages  表 : {'存在' if has_agent else '【不存在】'}")
    if has_agent:
        try:
            with inst.engine.connect() as conn:
                cols = [row[1] for row in conn.execute(_sql_text("PRAGMA table_info(agent_messages)"))]
            has_source = "source" in cols
            print(f"  agent_messages.source 列 : {'存在（可区分人工/AI）' if has_source else '【不存在】（旧库，重启后会自动补列）'}")
            if not has_source:
                notes.append("agent_messages 缺少 source 列（旧库；启动应用或跑一次自测后会自动补列）")
        except Exception as e:
            print(f"  [警告] 检查 source 列失败: {e}")


def check_stats(inst):
    print()
    print("=" * 74)
    print("[4/4] 消息记录统计")
    print("=" * 74)
    if inst is None:
        print("  [跳过] 实例不可用")
        return
    from sqlalchemy import text as _sql_text
    try:
        with inst.engine.connect() as conn:
            rows = list(conn.execute(_sql_text(
                "SELECT direction, COALESCE(msg_type,'(null)'), COUNT(*) "
                "FROM message_records GROUP BY 1,2 ORDER BY 3 DESC"
            )))
        if not rows:
            print("  message_records 里还没有任何记录")
            print("  → 若刚发过消息，请检查日志里是否有 '[消息记录]' 相关行")
        for direction, msg_type, cnt in rows:
            label = {"in": "客户", "out": "客服/程序"}.get(direction, direction)
            print(f"  {direction:<4} {label:<8} {msg_type:<16} {cnt}")
    except Exception as e:
        print(f"  [跳过] 读取统计失败: {e}")


def main():
    parser = argparse.ArgumentParser(description="部署自检（只读）")
    parser.add_argument("--db", default=None, help="指定数据库文件（默认用配置里的 db_path）")
    args = parser.parse_args()

    print(f"项目根目录: {ROOT}")
    check_files()
    inst = check_runtime(args.db)
    check_tables(inst)
    check_stats(inst)

    print()
    if notes:
        print("提示（非阻塞）:")
        for i, n in enumerate(notes, 1):
            print(f"  {i}. {n}")
        print()
    print("=" * 74)
    if problems:
        print(f"结论: 发现 {len(problems)} 个问题，消息记录功能【不可用或不完整】")
        for i, p in enumerate(problems, 1):
            print(f"  {i}. {p}")
        print()
        print("修复建议: 把 database/ 目录（db_manager.py、models.py、__init__.py）与")
        print("          Message/、Agent/ 等一起同步到同一版本后重启（不要只拷部分目录）。")
        return 1
    print("结论: 全部检查通过，消息记录功能可用 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
