# -*- coding: utf-8 -*-
"""把商品/规格的服务模式统一设成「二选一兼容」，让卡券核销与发货两条路并存

默认**干跑**（只打印对照表，不改库）；确认后加 --apply 才写库，写前自动快照。

用法：
    uv run python scripts/fix_service_modes.py                    # 干跑：看现状与将改成什么
    uv run python scripts/fix_service_modes.py --apply            # 全部设为 2（二选一兼容）
    uv run python scripts/fix_service_modes.py --apply --mode 1   # 全部设为 1（仅发货）
    uv run python scripts/fix_service_modes.py --apply --only-level sku   # 只改 SKU 级配置
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

LABELS = {0: "卡券核销", 1: "仅发货", 2: "二选一兼容", None: "未配置", 3: "未配置"}


def _ensure_columns(con):
    """幂等补齐服务模式相关列（程序启动时也会补，脚本独立运行时自己补一份）"""
    targets = [
        ("goods_commit", "service_mode", "INTEGER"),
        ("goods_commit", "no_coupon_needed", "BOOLEAN DEFAULT 0"),
        ("orders", "shipped_at", "DATETIME"),
    ]
    for table, column, ddl in targets:
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
        if cols and column not in cols:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            print(f"  [迁移] {table} 已添加列 {column}")
    con.commit()


def main() -> int:
    ap = argparse.ArgumentParser(description="统一商品服务模式")
    ap.add_argument("--apply", action="store_true", help="真正写库（默认只干跑）")
    ap.add_argument("--mode", type=int, default=2, choices=[0, 1, 2],
                    help="目标服务模式：0 卡券核销 / 1 仅发货 / 2 二选一（默认 2）")
    ap.add_argument("--only-level", choices=["product", "sku", "both"], default="product",
                    help="改哪一层：product（商品级，默认）/ sku（规格级）/ both")
    ap.add_argument("--db", default=None, help="数据库路径（默认取 config.json 的 db_path）")
    args = ap.parse_args()

    if args.db:
        db_path = args.db
    else:
        try:
            from config import config
            db_path = config.get("db_path", "./temp/channel_shop.db")
        except Exception:
            db_path = "./temp/channel_shop.db"
    if not os.path.isabs(db_path):
        db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               db_path.lstrip("./"))
    if not os.path.exists(db_path):
        print(f"[失败] 数据库不存在: {db_path}")
        return 1

    con = sqlite3.connect(db_path)
    try:
        # 干跑不改任何东西；只有 --apply 时才顺手补列（与程序启动时的迁移一致）
        if args.apply:
            _ensure_columns(con)
        sku_cols = [r[1] for r in con.execute("PRAGMA table_info(goods_commit)")]
        has_sku_mode = "service_mode" in sku_cols
        print(f"数据库: {db_path}")
        print(f"目标模式: {args.mode}（{LABELS[args.mode]}）  层级: {args.only_level}")
        print(f"模式: {'【写库】' if args.apply else '【干跑，不改库】'}\n")

        print("=== 商品级（product_knowledge.service_mode）===")
        rows = list(con.execute(
            "SELECT goods_id, goods_name, service_mode FROM product_knowledge ORDER BY goods_id"))
        for goods_id, name, mode in rows:
            print(f"  {goods_id:<16} {LABELS.get(mode, mode)!s:<8}  {str(name)[:34]}")
        dist = {}
        for _, _, mode in rows:
            dist[mode] = dist.get(mode, 0) + 1
        print(f"  小计 {len(rows)} 个，当前分布: "
              + ", ".join(f"{LABELS.get(k, k)}={v}" for k, v in sorted(dist.items(), key=lambda x: str(x[0]))))

        print("\n=== 规格级（goods_commit.service_mode）===")
        if not has_sku_mode:
            print("  该库还没有 goods_commit.service_mode 列（程序启动一次会自动补；"
                  "或加 --apply 由本脚本补）——本次跳过规格级检查")
        else:
            sku_rows = list(con.execute(
                "SELECT id, goods_id, spec_name, service_mode, no_coupon_needed FROM goods_commit"))
            sku_null = sum(1 for r in sku_rows if r[3] is None)
            print(f"  共 {len(sku_rows)} 条 SKU；未单独配置(跟随商品)={sku_null}"
                  f"，已单独配置={len(sku_rows) - sku_null}")

        if not args.apply:
            print("\n干跑结束。确认无误后重新执行并加 --apply 才会写库。")
            return 0

        # 写库前快照
        backup_dir = os.path.join(os.path.dirname(db_path), "backup")
        os.makedirs(backup_dir, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = os.path.join(backup_dir, f"service_mode_{stamp}.db")
        shutil.copy2(db_path, backup)
        print(f"\n已快照数据库 -> {backup}")

        if args.only_level in ("product", "both"):
            cur = con.execute("UPDATE product_knowledge SET service_mode = ?", (args.mode,))
            print(f"商品级已更新 {cur.rowcount} 行")

        if args.only_level in ("sku", "both"):
            cur = con.execute("UPDATE goods_commit SET service_mode = ?", (args.mode,))
            print(f"规格级已更新 {cur.rowcount} 行")

        con.commit()

        after = {r[0]: r[1] for r in con.execute(
            "SELECT service_mode, COUNT(*) FROM product_knowledge GROUP BY service_mode")}
        print("写后商品级分布: " + ", ".join(f"{LABELS.get(k, k)}={v}" for k, v in after.items()))
        print("完成。重启程序后，新的会话就会按新配置分派。")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
