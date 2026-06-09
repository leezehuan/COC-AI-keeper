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
# 【重要变量】SKILL_HANDLERS
# 这是“Skill 调度表”。
# ExecutorAgent 在真正执行技能时，并不会写一长串 if/else 判断，
# 而是用 skill_name 作为 key 直接到这里取出对应的 run 函数。
# 例如：
# - "InvestigateSkill" -> run_investigate
# - "MoveSkill" -> run_move
# 关键调用路径：
# ExecutorAgent.run -> run_skill(skill_name, state, runtime) -> SKILL_HANDLERS[skill_name]
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
# 【重要变量】SKILL_SPECS
# 这里保存的是“技能规格说明书”，不是执行函数。
# 每个 SkillSpec 里会描述：
# 1. 这个技能的正式名称
# 2. 它允许调用哪些 Tool
# 3. 它的用途和行为边界
# PlannerAgent 会读取这个映射做白名单校验和自动补全，
# 比如发现计划里选中了 InvestigateSkill，就能顺手把它依赖的 Tool 一并补上。
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
# 【重要变量】ACTION_TYPE_TO_SKILL
# 这是“意图到技能模板”的分发表。
# ContextAgent 先把玩家自然语言解析成高层 action_type，
# 然后 PlannerAgent / ExecutorAgent 再通过这张表把高层意图落到具体 Skill 模板上。
# 你可以把它理解成“业务路由表”：
# - 调查类动作走 InvestigateSkill
# - 社交类动作走 SocialInteractionSkill
# - 战斗类动作走 CombatLiteSkill
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
    """根据行动类型选择 Skill 名称。

    【中文名称】选择 Skill 名称

    【功能说明】
    这是系统把“高层意图”映射到“具体技能模板”的标准入口。
    上游通常只知道玩家在做“调查 / 移动 / 社交 / 战斗”中的哪一类动作，
    但真正执行时必须落到一个明确的 Skill，如 InvestigateSkill 或 MoveSkill。

    【实现方法】
    1. 读取 ACTION_TYPE_TO_SKILL 这张固定映射表。
    2. 用 action_type 作为 key 查找对应 Skill 名称。
    3. 如果没匹配到，就回退到 InvestigateSkill。

    【为什么默认回退到 InvestigateSkill】
    调查是 CoC 中最常见、风险最小、兼容面最广的动作模板。
    当意图解析不够准确时，把动作先归到调查流程，通常比误判成战斗或社交更稳妥。

    【参数说明】
    - action_type: ContextAgent/PlannerAgent 产生的高层行动类型

    【返回值】
    - str: 对应的 Skill 名称，供 PlannerAgent 和 ExecutorAgent 继续使用
    """
    # 1. PlannerAgent 会把 ContextAgent 解析出的 action_type 传进来，例如“移动”“社交”“战斗”。
    # 2. ACTION_TYPE_TO_SKILL 是系统的分发表，决定本回合交给哪个 Skill 模板处理。
    # 3. 未匹配时回退到 InvestigateSkill，因为调查是 CoC 中最常见且最安全的默认行动。
    return ACTION_TYPE_TO_SKILL.get(action_type, "InvestigateSkill")  # 返回 Skill 名称，供 PlannerAgent/ExecutorAgent 使用


def run_skill(skill_name: str, state: dict[str, Any], runtime: dict[str, Any]) -> SkillResult:
    """执行指定 Skill。

    【中文名称】运行 Skill

    【功能说明】
    这是 Skill 执行层对外暴露的统一入口。
    ExecutorAgent 不需要知道每个 Skill 分别定义在哪个文件、函数名是什么，
    只需要给出一个 skill_name，本函数就会自动去注册表里查找并执行对应处理器。

    【实现方法】
    1. 从 SKILL_HANDLERS 中查找 skill_name 对应的执行函数。
    2. 如果没找到，回退到 run_investigate，避免整条链路直接崩掉。
    3. 把 state 和 runtime 原样传入具体 Skill 函数。
    4. 返回统一格式的 SkillResult，供 ExecutorAgent 继续汇总。

    【参数说明】
    - skill_name: Skill 名称，如 "InvestigateSkill"
    - state: 当前回合上下文字典，通常包含 session、character、intent、检索结果等
    - runtime: 运行时依赖，如 retrieval、debug_emit、allowed_tools、trace_recorder

    【返回值】
    - SkillResult: Skill 的标准输出，内部含 observations、result、input 等结构化字段
    """
    # 1. 根据 Skill 名称从注册表查找执行函数，例如 "MoveSkill" -> run_move。
    handler = SKILL_HANDLERS.get(skill_name) or run_investigate  # handler = Skill 执行函数；未找到时回退到调查技能
    # 2. 调用 Skill 执行函数；state 是本回合上下文，runtime 是检索服务、白名单、调试器等运行时依赖。
    # 3. 返回 SkillResult；ExecutorAgent 会继续读取 observations、result、success 等字段。
    return handler(state, runtime)  # 执行 Skill 并返回标准结果
