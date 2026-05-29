from __future__ import annotations

from typing import Any

from app.services.skills.base import SkillResult, SkillSpec
from app.services.skills.common import run_generic_skill
from app.services.skills.investigate.prompt import SKILL_PROMPT


SPEC = SkillSpec(
    name="InvestigateSkill",
    action_types=["调查", "观察", "阅读文献", "知识回忆"],
    allowed_tools=["ContextSearchTool", "ClueEligibilityTool", "RuleCheckTool"],
    description=SKILL_PROMPT,
    constraints=["不直接创建线索", "不写数据库", "不输出 keeper_only 信息"],
)


def run(state: dict[str, Any], runtime: dict[str, Any]) -> SkillResult:
    return run_generic_skill(spec=SPEC, state=state, runtime=runtime)
