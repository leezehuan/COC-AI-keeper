from __future__ import annotations

from typing import Any

from app.services.rules import adjudicate_action, as_adjudication_dict, execute_rule_tools
from app.services.tools.base import ToolObservation, ToolSpec


TOOL_NAME = "RuleCheckTool"


def tool_spec() -> ToolSpec:
    return ToolSpec(
        name=TOOL_NAME,
        description="执行技能、属性、幸运或理智检定；结果仅用于本回合裁定，不直接落库。",
        input_schema={
            "message": "玩家行动文本",
            "intent": "结构化意图",
            "default_skill": "默认技能名",
            "character_skills": "角色技能字典",
            "character_attributes": "角色属性字典",
            "scenario_context": "场景上下文片段",
            "current_san": "当前理智值",
            "luck": "幸运值",
        },
        constraints=[
            "骰点结果必须写入 react_trace 或 tool_observations。",
            "LLM 不得重掷、改写或否定该工具返回的随机结果。",
            "工具不直接修改角色 HP/SAN 或数据库。",
        ],
    )


def run_rule_check(
    *,
    message: str,
    intent: dict[str, Any],
    character_skills: dict[str, Any],
    character_attributes: dict[str, Any],
    scenario_context: list[dict[str, Any]],
    default_skill: str,
    current_san: int,
    luck: int = 50,
) -> ToolObservation:
    adjudication = adjudicate_action(message, intent, character_skills, character_attributes, scenario_context, default_skill, luck)
    adjudication_payload = as_adjudication_dict(adjudication)
    results = execute_rule_tools(adjudication_payload, current_san)
    return ToolObservation(
        tool=TOOL_NAME,
        input={"message": message, "intent": intent, "default_skill": default_skill},
        output={"adjudication": adjudication_payload, **results},
    )
