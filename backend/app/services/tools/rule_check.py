# =============================================================================
# 【RuleCheckTool：规则检定工具】
# =============================================================================
# 这个 Tool 负责 CoC 规则中的骰点检定：技能检定、属性检定、幸运检定和理智检定。
# 它是"确定性规则引擎"——骰点结果由 Python random 模块生成，LLM 不能篡改。
#
# 重要约束：
# - 骰点结果必须写入 react_trace 或 tool_observations，保证可审计
# - LLM 不得重掷、改写或否定该工具返回的随机结果
# - 工具不直接修改角色 HP/SAN 或数据库（由 commit_state 统一处理）
# =============================================================================
from __future__ import annotations

from typing import Any

from app.services.rules import adjudicate_action, as_adjudication_dict, execute_rule_tools
from app.services.tools.base import ToolObservation, ToolSpec


TOOL_NAME = "RuleCheckTool"


def tool_spec() -> ToolSpec:
    """返回 Tool 的规格说明（tool_spec = Tool 规格说明）。

    【中文名称】Tool 规格说明

    【功能说明】
    返回 RuleCheckTool 的规格说明，供 PlannerAgent 校验白名单。

    【返回值】
    - ToolSpec: 包含名称、描述、输入格式、约束条件
    """
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
    """执行规则检定（run_rule_check = 运行规则检定）。

    【中文名称】运行规则检定

    【功能说明】
    先裁定行动需要哪些检定，然后执行实际的骰点。
    骰点结果由 Python 代码生成，确保公平。

    【执行流程】
    1. adjudicate_action() → 裁定需要什么检定
    2. execute_rule_tools() → 执行骰点（D100 等）
    3. 汇总返回

    【参数说明】
    - message: 玩家行动文本
    - intent: 结构化意图
    - character_skills: 角色技能字典（如 {"侦查": 60}）
    - character_attributes: 角色属性字典
    - scenario_context: 场景上下文片段
    - default_skill: 默认技能名
    - current_san: 当前理智值
    - luck: 幸运值

    【返回值】
    - ToolObservation: 包含裁定结果和所有检定结果
    """
    adjudication = adjudicate_action(message, intent, character_skills, character_attributes, scenario_context, default_skill, luck)
    adjudication_payload = as_adjudication_dict(adjudication)  # 转为可序列化的字典
    results = execute_rule_tools(adjudication_payload, current_san)  # 执行骰点检定
    return ToolObservation(
        tool=TOOL_NAME,
        input={"message": message, "intent": intent, "default_skill": default_skill},
        output={"adjudication": adjudication_payload, **results},  # 合并裁定和检定结果
    )
