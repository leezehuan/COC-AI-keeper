# 【Tools 包入口】
# 统一导出所有 Tool 的基类和执行函数，方便其他模块 import。
# Tool 是 Agent 执行计划时的"原子操作"，每个 Tool 只做一件事：
# - ContextSearchTool：检索剧本/规则/实体等上下文
# - RuleCheckTool：执行技能/理智检定
# - InventoryLookupTool：查询物品栏
# - SceneAffordanceTool：查询场景可交互信息
# - ClueEligibilityTool：判断线索是否可被发现
# - MemoryRecallTool：检索会话记忆
from app.services.tools.base import ToolObservation, ToolSpec
from app.services.tools.clue_eligibility import run_clue_eligibility
from app.services.tools.context_search import run_context_search
from app.services.tools.inventory_lookup import run_inventory_lookup
from app.services.tools.memory_recall import run_memory_recall
from app.services.tools.rule_check import run_rule_check
from app.services.tools.scene_affordance import run_scene_affordance

__all__ = [
    "ToolObservation",
    "ToolSpec",
    "run_clue_eligibility",
    "run_context_search",
    "run_inventory_lookup",
    "run_memory_recall",
    "run_rule_check",
    "run_scene_affordance",
]
