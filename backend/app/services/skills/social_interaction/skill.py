from __future__ import annotations

from typing import Any

from app.services.skills.base import SkillResult, SkillSpec
from app.services.skills.common import run_generic_skill
from app.services.skills.social_interaction.prompt import SKILL_PROMPT


SPEC = SkillSpec(
    name="SocialInteractionSkill",
    action_types=["交谈", "说服", "恐吓", "社交"],
    allowed_tools=["ContextSearchTool", "RuleCheckTool", "MemoryRecallTool"],
    description=SKILL_PROMPT,
    constraints=["不泄露 NPC 隐藏动机", "不替玩家做重大决定"],
)


def run(state: dict[str, Any], runtime: dict[str, Any]) -> SkillResult:
    return run_generic_skill(spec=SPEC, state=state, runtime=runtime)
