from __future__ import annotations

from typing import Any

from app.services.skills.base import SkillResult, SkillSpec
from app.services.skills.common import run_generic_skill
from app.services.skills.danger_and_sanity.prompt import SKILL_PROMPT


SPEC = SkillSpec(
    name="DangerAndSanitySkill",
    action_types=["恐怖", "理智", "调查", "观察"],
    allowed_tools=["ContextSearchTool", "RuleCheckTool"],
    description=SKILL_PROMPT,
    constraints=["不直接修改 SAN", "不解释幕后真相"],
)


def run(state: dict[str, Any], runtime: dict[str, Any]) -> SkillResult:
    return run_generic_skill(spec=SPEC, state=state, runtime=runtime)
