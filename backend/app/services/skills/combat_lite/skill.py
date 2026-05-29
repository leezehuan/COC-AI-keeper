from __future__ import annotations

from typing import Any

from app.services.skills.base import SkillResult, SkillSpec
from app.services.skills.common import run_generic_skill
from app.services.skills.combat_lite.prompt import SKILL_PROMPT


SPEC = SkillSpec(
    name="CombatLiteSkill",
    action_types=["战斗", "逃跑"],
    allowed_tools=["RuleCheckTool", "SceneAffordanceTool"],
    description=SKILL_PROMPT,
    constraints=["不直接结算长期伤害", "不无限循环战斗"],
)


def run(state: dict[str, Any], runtime: dict[str, Any]) -> SkillResult:
    return run_generic_skill(spec=SPEC, state=state, runtime=runtime)
