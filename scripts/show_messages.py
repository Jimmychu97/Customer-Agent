# -*- coding: utf-8 -*-
"""聊天记录查看脚本（只读）

用于快速确认"店家人工客服发的消息到底有没有被记住"，以及区分人工与 AI：

1. message_records（主消息表）
   - direction=in  -> 客户发送
   - direction=out -> 客服/程序发送
   - msg_type=mall_cs         -> 本店人工客服发送
   - msg_type=mall_cs_control -> STOP AI / START AI 控制命令（仅审计）
   - msg_type=text            -> 程序发送（AI 自动回复、核销引导语等）
2. agent_messages（AI 会话历史）
   - role=user       -> 客户说的话
   - role=assistant  -> 客服这一侧说的话（AI 自动回复 或 人工客服）
   - source          -> 来源标记：customer / ai / mall_cs / voucher / control / system
                        （旧数据可能为空，说明是加 source 列之前写入的）

用法：
    PYTHONIOENCODING=utf-8 python scripts/show_messages.py --uid 4251855160868 --limit 30
    PYTHONIOENCODING=utf-8 python scripts/show_messages.py --stats
    PYTHONIOENCODING=utf-8 python scripts/show_messages.py --uid 4251855160868 --only mall_cs
    PYTHONIOENCODING=utf-8 python scripts/show_messages.py --uid 4251855160868 --db ./temp/channel_shop.db
"""
import argparse
import os
import sys

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

DIRECTION_LABEL = {"in": "客户→店", "out": "店→客户"}
MSGTYPE_LABEL = {
    "mall_cs": "人工客服",
    "mall_cs_control": "AI控制命令",
    "text": "AI/程序",
}
SOURCE_LABEL = {
    "customer": "客户",
    "ai": "AI自动回复",
    "mall_cs": "人工客服",
    "voucher": "核销流程",
    "control": "控制命令",
    "system": "系统",
}


def _cut(text, width=70):
    text = "" if text is None else str(text)
    text = text.replace("\n", " ")
    return text if len(text) <= width else text[: width - 1] + "…"


def _columns(inst, table):
    """读取表的列名集合（表不存在时返回空集合）"""
    from sqlalchemy import text as _sql_text
    try:
        with inst.engine.connect() as conn:
            return {row[1] for row in conn.execute(_sql_text(f"PRAGMA table_info({table})"))}
    except Exception:
        return set()


def _get_instance(db_path=None):
    from database.db_manager import DatabaseManager, get_db_manager
    if db_path:
        return DatabaseManager(db_path=db_path)
    return get_db_manager()


def show_stats(inst):
    from sqlalchemy import text as _sql_text
    print("=" * 78)
    print("message_records 统计")
    print("=" * 78)
    try:
        with inst.engine.connect() as conn:
            rows = list(conn.execute(_sql_text(
                "SELECT direction, COALESCE(msg_type,'(null)'), COUNT(*) "
                "FROM message_records GROUP BY 1,2 ORDER BY 3 DESC"
            )))
    except Exception as e:
        print(f"  读取失败（表可能不存在）: {e}")
        return
    if not rows:
        print("  （空）")
    for direction, msg_type, cnt in rows:
        print(f"  {DIRECTION_LABEL.get(direction, direction):<8} {MSGTYPE_LABEL.get(msg_type, msg_type):<12} {cnt}")

    print()
    print("=" * 78)
    print("agent_messages 来源统计（source 为空 = 加列之前的旧数据）")
    print("=" * 78)
    has_source = "source" in _columns(inst, "agent_messages")
    if not has_source:
        print("  提示: 该库的 agent_messages 还没有 source 列（旧库，重启应用后会自动补列）")
    sql = ("SELECT role, COALESCE(source,'(未标记)'), COUNT(*) "
           "FROM agent_messages GROUP BY 1,2 ORDER BY 3 DESC") if has_source else \
          "SELECT role, '(未标记)', COUNT(*) FROM agent_messages GROUP BY 1 ORDER BY 2 DESC"
    try:
        with inst.engine.connect() as conn:
            rows = list(conn.execute(_sql_text(sql)))
    except Exception as e:
        print(f"  读取失败: {e}")
        return
    for role, source, cnt in rows:
        print(f"  role={role:<10} source={SOURCE_LABEL.get(source, source):<10} {cnt}")


def show_messages(inst, uid=None, shop=None, limit=30, channel="pinduoduo",
                  only=None, with_session=True):
    from sqlalchemy import text as _sql_text

    print("=" * 78)
    print("message_records（主消息表）")
    print("=" * 78)
    sql = ("SELECT id, created_at, direction, COALESCE(msg_type,''), from_uid, "
           "COALESCE(account_id,''), content FROM message_records WHERE 1=1")
    params = {}
    if uid:
        sql += " AND from_uid = :uid"
        params["uid"] = str(uid)
    if shop:
        sql += " AND shop_id = :shop"
        params["shop"] = str(shop)
    if only:
        sql += " AND msg_type = :only"
        params["only"] = only
    sql += " ORDER BY id DESC LIMIT :limit"
    params["limit"] = int(limit)
    try:
        with inst.engine.connect() as conn:
            rows = list(conn.execute(_sql_text(sql), params))
    except Exception as e:
        print(f"  读取失败（表可能不存在，说明 database/ 版本过旧）: {e}")
        rows = []
    if not rows:
        print("  （没有符合条件的记录）")
        print("  → 若刚发过消息：检查日志里是否有 '[消息记录] ' 行；")
        print("    或跑 'python scripts/doctor.py' 看是不是混合部署导致落库失败。")
    for r in reversed(rows):
        print(f"  #{r[0]:<6} {r[1]}  {DIRECTION_LABEL.get(r[2], r[2]):<8} "
              f"{MSGTYPE_LABEL.get(r[3], r[3]):<12} uid={r[4]:<16} {_cut(r[6])}")

    if not with_session:
        return
    print()
    print("=" * 78)
    print(f"agent_messages（AI 会话历史 session_id={channel}{uid or '<uid>'}）")
    print("=" * 78)
    if not uid:
        print("  需要 --uid 才能查看会话历史（或用 --no-session 跳过）")
        return
    has_source = "source" in _columns(inst, "agent_messages")
    if not has_source:
        print("  提示: 该库还没有 source 列，无法区分人工/AI（重启应用后会自动补列）")
    if has_source:
        sql = ("SELECT id, timestamp, role, COALESCE(source,''), content "
               "FROM agent_messages WHERE session_id = :sid ORDER BY id DESC LIMIT :limit")
    else:
        sql = ("SELECT id, timestamp, role, '', content "
               "FROM agent_messages WHERE session_id = :sid ORDER BY id DESC LIMIT :limit")
    try:
        with inst.engine.connect() as conn:
            rows = list(conn.execute(_sql_text(sql), {"sid": f"{channel}{uid}", "limit": int(limit)}))
    except Exception as e:
        print(f"  读取失败: {e}")
        return
    if not rows:
        print("  （该客户还没有会话历史）")
    for r in reversed(rows):
        src = SOURCE_LABEL.get(r[3], r[3] or "未标记")
        who = "客户" if r[2] == "user" else ("人工/AI" if r[2] == "assistant" else r[2])
        print(f"  #{r[0]:<6} {r[1]}  {who:<8} source={src:<10} {_cut(r[4])}")


def main():
    parser = argparse.ArgumentParser(description="查看聊天消息记录（只读）")
    parser.add_argument("--uid", default=None, help="客户 UID，例如 4251855160868")
    parser.add_argument("--shop", default=None, help="店铺 ID（可选）")
    parser.add_argument("--limit", type=int, default=30, help="显示条数，默认 30")
    parser.add_argument("--only", default=None, help="只看某个 msg_type，如 mall_cs / text")
    parser.add_argument("--channel", default="pinduoduo", help="渠道，默认 pinduoduo")
    parser.add_argument("--stats", action="store_true", help="只看统计汇总")
    parser.add_argument("--no-session", action="store_true", help="不显示 agent_messages 会话历史")
    parser.add_argument("--db", default=None, help="指定数据库文件（默认用配置里的 db_path）")
    args = parser.parse_args()

    inst = _get_instance(args.db)
    try:
        print(f"数据库: {inst.engine.url.database}")
    except Exception:
        pass
    print()

    if args.stats:
        show_stats(inst)
    else:
        show_messages(inst, uid=args.uid, shop=args.shop, limit=args.limit,
                      channel=args.channel, only=args.only,
                      with_session=not args.no_session)
    return 0


if __name__ == "__main__":
    sys.exit(main())
