# 【MemoryRecallTool：会话记忆检索工具】
# 检索当前会话的长期记忆（之前回合的玩家行动和守秘人回应）。
# 这些记忆在每回合结束时由 commit_state 写入 ChromaDB 的 session_memory_chunks。
#
# 重要约束：
# - 只检索当前 session_id 的记忆，不会跨会话泄漏。
# - 只返回玩家可见的记忆，keeper_only 的记忆会被过滤掉。
# - 只读操作，不修改长期记忆（写入由 commit_state 处理）。
from __future__ import annotations

from typing import Any

from app.services.tools.base import ToolObservation, ToolSpec


TOOL_NAME = "MemoryRecallTool"


def tool_spec() -> ToolSpec:
    """返回 Tool 的规格说明（tool_spec = Tool 规格说明）。

    【中文名称】Tool 规格说明
    """
    return ToolSpec(
        name=TOOL_NAME,
        description="检索当前会话长期记忆，只返回玩家已知信息。",
        input_schema={"query": "记忆检索查询", "session_id": "当前会话 ID", "n_results": "返回条数"},
        constraints=["只检索当前 session_id。", "只返回玩家可见会话记忆。", "不修改长期记忆。"],
    )


def run_memory_recall(*, retrieval: Any, query: str, session_id: str, n_results: int = 3) -> ToolObservation:
    """检索会话记忆（run_memory_recall = 运行记忆召回）。

    【中文名称】运行记忆召回

    【功能说明】
    在 session_memory_chunks 中检索当前会话的长期记忆，
    过滤掉 keeper_only 的记忆，只返回玩家可见的内容。

    【参数说明】
    - retrieval: RetrievalService 实例
    - query: 记忆检索查询文本
    - session_id: 当前会话 ID
    - n_results: 返回条数

    【返回值】
    - ToolObservation: 包含过滤后的记忆列表
    """
    try:
        # 按 session_id 过滤，只检索当前会话的记忆
        rows = retrieval.query("session_memory_chunks", query, n_results=n_results, where={"session_id": session_id})
        # 过滤掉 keeper_only 的记忆，防止剧透
        safe_rows = [row for row in rows if (row.get("metadata") or {}).get("visibility") != "keeper_only"]
        return ToolObservation(tool=TOOL_NAME, input={"query": query, "session_id": session_id, "n_results": n_results}, output={"memories": safe_rows})
    except Exception as exc:
        # 检索失败时返回空列表，不影响主流程
        return ToolObservation(tool=TOOL_NAME, input={"query": query, "session_id": session_id}, output={"memories": []}, success=False, error=str(exc))
