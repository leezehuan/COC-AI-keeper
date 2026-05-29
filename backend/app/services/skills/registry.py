from __future__ import annotations

from typing import Any

from app.services.skills.base import SkillResult, SkillSpec
from app.services.skills.combat_lite.skill import SPEC as COMBAT_LITE_SPEC, run as run_combat_lite
from app.services.skills.danger_and_sanity.skill import SPEC as DANGER_AND_SANITY_SPEC, run as run_danger_and_sanity
from app.services.skills.investigate.skill import SPEC as INVESTIGATE_SPEC, run as run_investigate
from app.services.skills.move.skill import SPEC as MOVE_SPEC, run as run_move
from app.services.skills.social_interaction.skill import SPEC as SOCIAL_INTERACTION_SPEC, run as run_social_interaction
from app.services.skills.use_item.skill import SPEC as USE_ITEM_SPEC, run as run_use_item
from app.services.skills.wait_or_observe.skill import SPEC as WAIT_OR_OBSERVE_SPEC, run as run_wait_or_observe


SKILL_HANDLERS = {
    INVESTIGATE_SPEC.name: run_investigate,
    MOVE_SPEC.name: run_move,
    SOCIAL_INTERACTION_SPEC.name: run_social_interaction,
    USE_ITEM_SPEC.name: run_use_item,
    DANGER_AND_SANITY_SPEC.name: run_danger_and_sanity,
    COMBAT_LITE_SPEC.name: run_combat_lite,
    WAIT_OR_OBSERVE_SPEC.name: run_wait_or_observe,
}

SKILL_SPECS: dict[str, SkillSpec] = {
    INVESTIGATE_SPEC.name: INVESTIGATE_SPEC,
    MOVE_SPEC.name: MOVE_SPEC,
    SOCIAL_INTERACTION_SPEC.name: SOCIAL_INTERACTION_SPEC,
    USE_ITEM_SPEC.name: USE_ITEM_SPEC,
    DANGER_AND_SANITY_SPEC.name: DANGER_AND_SANITY_SPEC,
    COMBAT_LITE_SPEC.name: COMBAT_LITE_SPEC,
    WAIT_OR_OBSERVE_SPEC.name: WAIT_OR_OBSERVE_SPEC,
}

ACTION_TYPE_TO_SKILL = {
    "调查": "InvestigateSkill",
    "观察": "InvestigateSkill",
    "阅读文献": "InvestigateSkill",
    "知识回忆": "InvestigateSkill",
    "移动": "MoveSkill",
    "交谈": "SocialInteractionSkill",
    "说服": "SocialInteractionSkill",
    "恐吓": "SocialInteractionSkill",
    "社交": "SocialInteractionSkill",
    "使用物品": "UseItemSkill",
    "战斗": "CombatLiteSkill",
    "逃跑": "CombatLiteSkill",
    "等待": "WaitOrObserveSkill",
    "查询状态": "WaitOrObserveSkill",
    "剧情回顾": "WaitOrObserveSkill",
}


def choose_skill_name(action_type: str) -> str:
    return ACTION_TYPE_TO_SKILL.get(action_type, "InvestigateSkill")


def run_skill(skill_name: str, state: dict[str, Any], runtime: dict[str, Any]) -> SkillResult:
    handler = SKILL_HANDLERS.get(skill_name) or run_investigate
    return handler(state, runtime)
