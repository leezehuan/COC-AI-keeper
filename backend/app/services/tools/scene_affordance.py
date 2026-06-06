# 【SceneAffordanceTool：场景可交互信息查询工具】
# 查询当前地点的可交互对象、出口、NPC 和风险元素。
# "Affordance" 是交互设计术语，指环境中可供操作的可能性（如"门可以打开"、"书可以阅读"）。
# 只读操作，不能直接改变当前位置或可前往地点。
from __future__ import annotations

from typing import Any

from app.services.tools.base import ToolObservation, ToolSpec


TOOL_NAME = "SceneAffordanceTool"


def tool_spec() -> ToolSpec:
    """返回 Tool 的规格说明（tool_spec = Tool 规格说明）。

    【中文名称】Tool 规格说明
    """
    return ToolSpec(
        name=TOOL_NAME,
        description="查询当前地点可交互对象、出口、NPC 与风险元素，只返回观察结果。",
        input_schema={"location_context": "地点实体检索结果", "story_state": "当前剧情状态"},
        constraints=["只读场景状态。", "不能直接改变当前位置或可前往地点。"],
    )


def run_scene_affordance(*, location_context: list[dict[str, Any]], story_state: dict[str, Any]) -> ToolObservation:
    """查询场景可交互信息（run_scene_affordance = 运行场景可交互查询）。

    【中文名称】运行场景可交互查询

    【功能说明】
    从地点实体和剧情状态中提取可交互要素：
    - affordances: 可交互对象（名称、类型、可见性、摘要）
    - available_locations: 当前可前往的地点
    - investigated_objects: 已调查过的对象
    - risk_elements: 当前风险元素

    【参数说明】
    - location_context: 地点实体检索结果
    - story_state: 当前剧情状态

    【返回值】
    - ToolObservation: 包含场景可交互信息
    """
    story = story_state.get("剧情", {}) if isinstance(story_state.get("剧情"), dict) else {}
    scene = story_state.get("场景", {}) if isinstance(story_state.get("场景"), dict) else {}
    affordances: list[dict[str, Any]] = []
    for row in location_context:
        metadata = row.get("metadata") or {}
        affordances.append(
            {
                "name": metadata.get("title") or row.get("id"),  # 实体名称
                "entity_type": metadata.get("entity_type"),  # 实体类型：地点/NPC/物品
                "visibility": metadata.get("visibility") or metadata.get("secret_level"),  # 可见性
                "summary": str(row.get("document") or "")[:500],  # 实体描述摘要
            }
        )
    output = {
        "affordances": affordances,  # 可交互对象
        "available_locations": story.get("当前可前往地点", []),  # 可前往地点
        "investigated_objects": scene.get("已调查对象", []),  # 已调查对象
        "risk_elements": story.get("当前风险", []),  # 风险元素
    }
    return ToolObservation(tool=TOOL_NAME, input={}, output=output)
