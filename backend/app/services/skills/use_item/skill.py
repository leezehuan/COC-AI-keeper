from __future__ import annotations

from typing import Any

from app.services.skills.base import SkillResult, SkillSpec
from app.services.skills.common import run_generic_skill
from app.services.skills.use_item.prompt import SKILL_PROMPT


SPEC = SkillSpec(
    name="UseItemSkill",
    action_types=["使用物品"],
    allowed_tools=["InventoryLookupTool", "SceneAffordanceTool", "RuleCheckTool"],
    description=SKILL_PROMPT,
    constraints=["不直接改动物品栏", "物品变化必须经 inventory_changes 校验"],
)


def run(state: dict[str, Any], runtime: dict[str, Any]) -> SkillResult:
    return run_generic_skill(spec=SPEC, state=state, runtime=runtime)
