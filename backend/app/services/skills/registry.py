# 【Skill 注册表】
# 这个文件是所有 Skill 的注册中心，负责：
# 1. 导入所有 Skill 的 SPEC 和执行函数。
# 2. 维护 SKILL_HANDLERS（名称 -> 执行函数）和 SKILL_SPECS（名称 -> 规格说明）两个映射表。
# 3. 维护 ACTION_TYPE_TO_SKILL（行动类型 -> Skill 名称）映射表，用于根据意图选择 Skill。
# 4. 提供 choose_skill_name 和 run_skill 两个公共函数。
from __future__ import annotations

from typing import Any

from app.services.skills.base import SkillResult, SkillSpec
# 导入各 Skill 的规格和执行函数
from app.services.skills.combat_lite.skill import SPEC as COMBAT_LITE_SPEC, run as run_combat_lite
from app.services.skills.danger_and_sanity.skill import SPEC as DANGER_AND_SANITY_SPEC, run as run_danger_and_sanity
from app.services.skills.investigate.skill import SPEC as INVESTIGATE_SPEC, run as run_investigate
from app.services.skills.move.skill import SPEC as MOVE_SPEC, run as run_move
from app.services.skills.social_interaction.skill import SPEC as SOCIAL_INTERACTION_SPEC, run as run_social_interaction
from app.services.skills.use_item.skill import SPEC as USE_ITEM_SPEC, run as run_use_item
from app.services.skills.wait_or_observe.skill import SPEC as WAIT_OR_OBSERVE_SPEC, run as run_wait_or_observe


# Skill 名称 -> 执行函数的映射表
SKILL_HANDLERS = {
    INVESTIGATE_SPEC.name: run_investigate,
    MOVE_SPEC.name: run_move,
    SOCIAL_INTERACTION_SPEC.name: run_social_interaction,
    USE_ITEM_SPEC.name: run_use_item,
    DANGER_AND_SANITY_SPEC.name: run_danger_and_sanity,
    COMBAT_LITE_SPEC.name: run_combat_lite,
    WAIT_OR_OBSERVE_SPEC.name: run_wait_or_observe,
}

# Skill 名称 -> 规格说明的映射表，供 PlannerAgent 校验白名单使用
SKILL_SPECS: dict[str, SkillSpec] = {
    INVESTIGATE_SPEC.name: INVESTIGATE_SPEC,
    MOVE_SPEC.name: MOVE_SPEC,
    SOCIAL_INTERACTION_SPEC.name: SOCIAL_INTERACTION_SPEC,
    USE_ITEM_SPEC.name: USE_ITEM_SPEC,
    DANGER_AND_SANITY_SPEC.name: DANGER_AND_SANITY_SPEC,
    COMBAT_LITE_SPEC.name: COMBAT_LITE_SPEC,
    WAIT_OR_OBSERVE_SPEC.name: WAIT_OR_OBSERVE_SPEC,
}

# 行动类型 -> Skill 名称的映射表，用于根据意图的 action_type 选择对应的 Skill
# 例如：action_type="调查" -> "InvestigateSkill"
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
    """根据行动类型选择 Skill 名称，未匹配时默认使用 InvestigateSkill。"""
    return ACTION_TYPE_TO_SKILL.get(action_type, "InvestigateSkill")


def run_skill(skill_name: str, state: dict[str, Any], runtime: dict[str, Any]) -> SkillResult:
    """执行指定 Skill：查找对应的执行函数并调用。

    参数：
        skill_name：Skill 名称，如 "InvestigateSkill"
        state：当前回合状态（KeeperState 的子集）
        runtime：运行时参数（retrieval、debug_emit、allowed_tools 等）
    返回：
        SkillResult，包含所有 Tool 的观察结果和决策摘要
    """
    handler = SKILL_HANDLERS.get(skill_name) or run_investigate  # 未找到时回退到调查技能
    return handler(state, runtime)
