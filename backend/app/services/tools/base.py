# =============================================================================
# 【Tool 基类】
# =============================================================================
# 这个文件定义了所有 Tool 共用的数据结构。
# Tool（工具）是系统中最小的功能单元，每个 Tool 只做一件具体的事。
#
# 三个核心概念：
#
# 1. ToolSpec（Tool 规格说明）
#    - 描述一个 Tool 叫什么、能做什么、接受什么输入、有什么限制
#    - 供 PlannerAgent 校验白名单用——确保 LLM 不会"幻觉"出不存在的 Tool
#    - 类比：就像电器的"使用说明书"
#
# 2. ToolObservation（Tool 观察结果）
#    - 记录 Tool 执行后的结果：输入了什么、输出了什么、是否成功
#    - 名字来自 ReAct 模式：Agent 执行 Action 后观察到的 Observation
#    - 类比：就像做完实验后写的"实验记录"
#
# 3. ToolHandler（Tool 执行函数类型）
#    - 定义了 Tool 执行函数的标准签名
#    - 所有 Tool 的执行函数都遵循这个签名
# =============================================================================
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class ToolSpec:
    """Tool 规格说明（可以理解为"使用说明书"）。

    【中文名称】Tool 规格说明

    【功能说明】
    描述一个 Tool 的基本信息：名称、功能、输入格式、约束条件。
    PlannerAgent 用它来校验 LLM 生成的计划是否使用了合法的 Tool。

    【为什么是 frozen=True】
    创建后不能修改，防止意外篡改 Tool 的定义。

    【字段说明】
    - name: Tool 名称，如 "ContextSearchTool"
    - description: 功能描述，供 LLM 理解用途
    - input_schema: 输入参数说明
    - constraints: 使用约束（如"只能读取，不能修改状态"）
    """
    name: str  # Tool 名称，如 "ContextSearchTool"
    description: str  # 功能描述，供 LLM 理解 Tool 的用途
    input_schema: dict[str, Any]  # 输入参数说明，如 {"query": "检索查询文本", "collections": "..."}
    constraints: list[str] = field(default_factory=list)  # 约束条件，如 ["只能读取，不能修改状态"]


@dataclass
class ToolObservation:
    """Tool 执行观察结果（可以理解为"实验记录"）。

    【中文名称】Tool 观察结果

    【功能说明】
    记录 Tool 执行后的完整信息：调用了哪个 Tool、传入了什么参数、
    得到了什么结果、是否成功。

    【名字来源】
    来自 ReAct（Reasoning + Acting）模式：
    Agent 执行一个 Action 后，会观察环境得到 Observation。

    【字段说明】
    - tool: Tool 名称
    - input: 传给 Tool 的输入参数
    - output: Tool 返回的输出结果
    - success: 是否执行成功
    - error: 错误信息（仅失败时有值）
    """
    tool: str  # Tool 名称
    input: dict[str, Any]  # 传给 Tool 的输入参数
    output: dict[str, Any]  # Tool 返回的输出结果
    success: bool = True  # 是否执行成功
    error: str = ""  # 错误信息（仅 success=False 时有值）

    def as_dict(self) -> dict[str, Any]:
        """转为字典（as_dict = 转为字典）。

        【中文名称】转为字典

        【功能说明】
        将 ToolObservation 转为普通字典，方便写入 state 和 JSON 序列化。

        【返回值】
        - dict: 包含 tool、input、output、success、error 的字典
        """
        return {
            "tool": self.tool,
            "input": self.input,
            "output": self.output,
            "success": self.success,
            "error": self.error,
        }


# ToolHandler 类型签名：接受一个参数字典，返回 ToolObservation
ToolHandler = Callable[[dict[str, Any]], ToolObservation]
