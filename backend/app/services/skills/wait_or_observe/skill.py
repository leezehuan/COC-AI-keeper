from __future__ import annotations

from typing import Any

from app.services.skills.base import SkillResult, SkillSpec
from app.services.skills.common import run_generic_skill
from app.services.skills.wait_or_observe.prompt import SKILL_PROMPT


SPEC = SkillSpec(
    name="WaitOrObserveSkill",
    action_types=["等待", "观察", "查询状态", "剧情回顾", "其他"],
    allowed_tools=["ContextSearchTool", "MemoryRecallTool"],
    description=SKILL_PROMPT,
    constraints=["不触发隐藏事件", "只给非剧透提示"],
)


def run(state: dict[str, Any], runtime: dict[str, Any]) -> SkillResult:
    return run_generic_skill(spec=SPEC, state=state, runtime=runtime)
