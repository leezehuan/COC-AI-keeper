from __future__ import annotations

from typing import Any

from app.services.tools.base import ToolObservation, ToolSpec


TOOL_NAME = "InventoryLookupTool"


def tool_spec() -> ToolSpec:
    return ToolSpec(
        name=TOOL_NAME,
        description="查询当前会话物品及可用状态，不修改物品栏。",
        input_schema={"query": "可选物品名或用途关键词"},
        constraints=["只读当前物品栏。", "不能新增、消耗、丢弃或使用物品。"],
    )


def run_inventory_lookup(*, items: list[Any], query: str = "") -> ToolObservation:
    normalized_query = query.strip().lower()
    result: list[dict[str, Any]] = []
    for item in items:
        name = str(getattr(item, "name", ""))
        description = str(getattr(item, "description", ""))
        haystack = f"{name}\n{description}".lower()
        if normalized_query and normalized_query not in haystack:
            continue
        metadata = getattr(item, "metadata_", {}) if isinstance(getattr(item, "metadata_", {}), dict) else {}
        result.append(
            {
                "item_key": getattr(item, "item_key", ""),
                "name": name,
                "description": description,
                "quantity": int(getattr(item, "quantity", 0) or 0),
                "metadata": metadata,
                "usable": True,
            }
        )
    return ToolObservation(tool=TOOL_NAME, input={"query": query}, output={"items": result, "count": len(result)})
