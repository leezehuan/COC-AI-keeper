# 【Skills 包入口】
# Skill 是比 Tool 更高层的抽象：一个 Skill 对应一类玩家行动（调查、移动、社交等），
# 它会按顺序调用多个 Tool 来完成整个行动的处理。
# 例如 InvestigateSkill 可能调用 ContextSearchTool + ClueEligibilityTool + RuleCheckTool。
from app.services.skills.base import SkillResult, SkillSpec
from app.services.skills.registry import SKILL_SPECS, choose_skill_name, run_skill

__all__ = ["SkillResult", "SkillSpec", "SKILL_SPECS", "choose_skill_name", "run_skill"]
