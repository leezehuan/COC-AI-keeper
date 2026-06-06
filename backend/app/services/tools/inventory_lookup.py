# 【InventoryLookupTool：物品栏查询工具】
# 只读查询当前会话的物品栏，支持按关键词过滤。
# 不能新增、消耗、丢弃或使用物品（物品变更由 commit_state 中的 apply_inventory_changes 处理）。
from __future__ import annotations

from typing import Any

from app.services.tools.base import ToolObservation, ToolSpec


TOOL_NAME = "InventoryLookupTool"


def tool_spec() -> ToolSpec:
    """返回 Tool 的规格说明（tool_spec = Tool 规格说明）。

    【中文名称】Tool 规格说明
    【功能说明】返回 InventoryLookupTool 的规格说明。
    """
    return ToolSpec(
        name=TOOL_NAME,
        description="查询当前会话物品及可用状态，不修改物品栏。",
        input_schema={"query": "可选物品名或用途关键词"},
        constraints=["只读当前物品栏。", "不能新增、消耗、丢弃或使用物品。"],
    )


def run_inventory_lookup(*, items: list[Any], query: str = "") -> ToolObservation:
    """查询物品栏（run_inventory_lookup = 运行物品栏查询）。

    【中文名称】运行物品栏查询

    【功能说明】
    在角色物品栏中搜索关键词，返回匹配的物品列表。
    如果 query 为空，返回所有物品。

    【参数说明】
    - items: 当前会话的 InventoryItem ORM 对象列表
    - query: 可选的关键词过滤

    【返回值】
    - ToolObservation: 包含匹配的物品列表
    """
    normalized_query = query.strip().lower()
    result: list[dict[str, Any]] = []
    for item in items:
        name = str(getattr(item, "name", ""))
        description = str(getattr(item, "description", ""))
        haystack = f"{name}\n{description}".lower()  # 在名称和描述中搜索
        if normalized_query and normalized_query not in haystack:
            continue  # 关键词不匹配则跳过
        metadata = getattr(item, "metadata_", {}) if isinstance(getattr(item, "metadata_", {}), dict) else {}
        result.append(
            {
                "item_key": getattr(item, "item_key", ""),
                "name": name,
                "description": description,
                "quantity": int(getattr(item, "quantity", 0) or 0),
                "metadata": metadata,
                "usable": True,  # 默认标记为可用
            }
        )
    return ToolObservation(tool=TOOL_NAME, input={"query": query}, output={"items": result, "count": len(result)})
