from __future__ import annotations

from typing import Any

from app.services.skills.base import SkillResult, SkillSpec
from app.services.skills.common import run_generic_skill
from app.services.skills.move.prompt import SKILL_PROMPT


SPEC = SkillSpec(
    name="MoveSkill",
    action_types=["移动"],
    allowed_tools=["SceneAffordanceTool", "ContextSearchTool"],
    description=SKILL_PROMPT,
    constraints=["不直接改变地点", "地点变化必须由 state_delta 和 guardrails 处理"],
)


def run(state: dict[str, Any], runtime: dict[str, Any]) -> SkillResult:
    return run_generic_skill(spec=SPEC, state=state, runtime=runtime)
