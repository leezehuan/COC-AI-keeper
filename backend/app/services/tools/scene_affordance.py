from __future__ import annotations

from typing import Any

from app.services.tools.base import ToolObservation, ToolSpec


TOOL_NAME = "SceneAffordanceTool"


def tool_spec() -> ToolSpec:
    return ToolSpec(
        name=TOOL_NAME,
        description="查询当前地点可交互对象、出口、NPC 与风险元素，只返回观察结果。",
        input_schema={"location_context": "地点实体检索结果", "story_state": "当前剧情状态"},
        constraints=["只读场景状态。", "不能直接改变当前位置或可前往地点。"],
    )


def run_scene_affordance(*, location_context: list[dict[str, Any]], story_state: dict[str, Any]) -> ToolObservation:
    story = story_state.get("剧情", {}) if isinstance(story_state.get("剧情"), dict) else {}
    scene = story_state.get("场景", {}) if isinstance(story_state.get("场景"), dict) else {}
    affordances: list[dict[str, Any]] = []
    for row in location_context:
        metadata = row.get("metadata") or {}
        affordances.append(
            {
                "name": metadata.get("title") or row.get("id"),
                "entity_type": metadata.get("entity_type"),
                "visibility": metadata.get("visibility") or metadata.get("secret_level"),
                "summary": str(row.get("document") or "")[:500],
            }
        )
    output = {
        "affordances": affordances,
        "available_locations": story.get("当前可前往地点", []),
        "investigated_objects": scene.get("已调查对象", []),
        "risk_elements": story.get("当前风险", []),
    }
    return ToolObservation(tool=TOOL_NAME, input={}, output=output)
