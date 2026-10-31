"""
验机报告结果格式化：GSX 返回的 dict/list → 可读文本 / 中文简析。
"""

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
