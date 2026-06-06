# 【Skill 基类】
# 定义了所有 Skill 共用的数据结构：
# - SkillSpec：Skill 的规格说明（名称、行动类型、允许的 Tool 列表、约束条件）。
# - SkillResult：Skill 执行后的结果（输入、Tool 观察列表、决策摘要）。
# - SkillHandler：Skill 执行函数的类型签名。
#
# Skill 与 Tool 的关系：
# - Tool 是原子操作（如"检索上下文"、"骰点检定"），只做一件事。
# - Skill 是组合操作（如"调查" = 检索上下文 + 查线索候选 + 可能的骰点检定），
#   按顺序调用多个 Tool，汇总结果。
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class SkillSpec:
    """Skill 规格说明：描述一个 Skill 能处理哪些行动类型、可以使用哪些 Tool。

    frozen=True 表示不可变，创建后不能修改。
    PlannerAgent 使用 SkillSpec 的 allowed_tools 来校验计划白名单。
    """
    name: str  # Skill 名称，如 "InvestigateSkill"
    action_types: list[str]  # 可处理的行动类型，如 ["调查", "观察", "阅读文献"]
    allowed_tools: list[str]  # 允许使用的 Tool 名称列表，如 ["ContextSearchTool", "ClueEligibilityTool"]
    description: str  # 功能描述
    constraints: list[str] = field(default_factory=list)  # 约束条件


@dataclass
class SkillResult:
    """Skill 执行结果：记录 Skill 的输入、所有 Tool 的观察结果和决策摘要。

    observations 是 ToolObservation.as_dict() 的列表，包含每个 Tool 的输入、输出和状态。
    result 包含 decision_summary（决策摘要）和 candidate_resolution（候选裁定）。
    """
    skill: str  # Skill 名称
    input: dict[str, Any]  # Skill 的输入参数
    observations: list[dict[str, Any]]  # 所有 Tool 的观察结果列表
    result: dict[str, Any]  # 决策摘要和候选裁定
    success: bool = True  # 是否执行成功
    error: str = ""  # 错误信息

    def as_dict(self) -> dict[str, Any]:
        """转为字典，方便写入 state 和序列化。"""
        return {
            "skill": self.skill,
            "input": self.input,
            "observations": self.observations,
            "result": self.result,
            "success": self.success,
            "error": self.error,
        }


# SkillHandler 类型签名：接受 state 和 runtime 字典，返回 SkillResult
SkillHandler = Callable[[dict[str, Any]], SkillResult]
