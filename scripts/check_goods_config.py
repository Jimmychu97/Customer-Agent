# -*- coding: utf-8 -*-
"""商品验机配置巡检（只读，不联网）

为什么要这个脚本：
  发货/核销要按「商品 + 规格」找到验机编码（server_id / zhenxin_server_id）。
  查不到编码时，程序只能对客户说「报告没出，麻烦发订单卡片核对」——
  线上 16:26 / 16:45 / 16:46 三单就是这么卡住的，其中
  goods_id=1009607436389 在 goods_commit / product_knowledge 里根本不存在。

  跑一遍本脚本，可以在客户下单之前发现缺口，而不是等客户在聊天里催。

用法：
  uv run python scripts/check_goods_config.py
  uv run python scripts/check_goods_config.py --db temp/channel_shop.db
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _load(db_path: str):
    from config import config
    from database.db_manager import DatabaseManager
    from database.models import GoodsCommit, ProductKnowledge

    path = db_path or str(config.get("db_path", "") or "./temp/channel_shop.db")
    print(f"数据库: {os.path.abspath(path)}")
    return DatabaseManager(db_path=path), GoodsCommit, ProductKnowledge


def main() -> int:
    parser = argparse.ArgumentParser(description="商品验机配置巡检（只读）")
    parser.add_argument("--db", default="", help="数据库路径（默认取 config.json 的 db_path）")
    args = parser.parse_args()

    db, GoodsCommit, ProductKnowledge = _load(args.db)

    with db.session_scope() as session:
        sku_rows = [
            {
                "goods_id": str(g.goods_id or ""),
                "spec": str(g.spec_name or ""),
                "mode": g.service_mode,
                "no_coupon": bool(g.no_coupon_needed),
                "server_id": g.server_id,
                "zhenxin": getattr(g, "zhenxin_server_id", None),
                "device_type": g.device_type,
            }
            for g in session.query(GoodsCommit).all()
        ]
        knowledge = [
            {"goods_id": str(p.goods_id or ""), "goods_name": str(p.goods_name or ""),
             "mode": p.service_mode}
            for p in session.query(ProductKnowledge).all()
        ]

    sku_goods = {r["goods_id"] for r in sku_rows}
    no_spec_rows = [r for r in sku_rows if not r["spec"]]
    no_code_rows = [r for r in sku_rows
                    if not (r["server_id"] or r["zhenxin"]) and not r["no_coupon"]]
    not_synced = [k for k in knowledge if k["goods_id"] not in sku_goods]
    mode_missing = [k for k in knowledge if k["mode"] is None]

    print(f"\n已同步 SKU 商品数: {len(sku_goods)}  规格行数: {len(sku_rows)}  "
          f"商品知识行数: {len(knowledge)}")

    print(f"\n【1】商品知识里有、但没同步 SKU 的商品（发货时查不到验机编码）: {len(not_synced)}")
    for k in not_synced:
        print(f"    goods_id={k['goods_id']:<16} {k['goods_name'][:28]}")

    print(f"\n【2】缺验机编码的规格行（server_id / zhenxin_server_id 都为空，且不是免卡券）: "
          f"{len(no_code_rows)}")
    for r in no_code_rows:
        print(f"    goods_id={r['goods_id']:<16} spec={r['spec']!r:<22} "
              f"mode={r['mode']} device_type={r['device_type']}")

    print(f"\n【3】没有规格名的 SKU 行（按规格精确匹配，等于查不到配置）: {len(no_spec_rows)}")
    for r in no_spec_rows:
        print(f"    goods_id={r['goods_id']:<16} mode={r['mode']}")

    print(f"\n【4】服务模式未配置的商品知识行: {len(mode_missing)}")
    for k in mode_missing:
        print(f"    goods_id={k['goods_id']:<16} {k['goods_name'][:28]}")

    gaps = len(not_synced) + len(no_code_rows) + len(no_spec_rows)
    print("\n结论: " + ("存在配置缺口，发货/报告链路会受影响（见上）。" if gaps else "配置齐全。"))
    return 1 if gaps else 0


if __name__ == "__main__":
    sys.exit(main())
