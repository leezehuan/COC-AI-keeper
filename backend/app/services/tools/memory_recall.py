from __future__ import annotations

from typing import Any

from app.services.tools.base import ToolObservation, ToolSpec


TOOL_NAME = "MemoryRecallTool"


def tool_spec() -> ToolSpec:
    return ToolSpec(
        name=TOOL_NAME,
        description="检索当前会话长期记忆，只返回玩家已知信息。",
        input_schema={"query": "记忆检索查询", "session_id": "当前会话 ID", "n_results": "返回条数"},
        constraints=["只检索当前 session_id。", "只返回玩家可见会话记忆。", "不修改长期记忆。"],
    )


def run_memory_recall(*, retrieval: Any, query: str, session_id: str, n_results: int = 3) -> ToolObservation:
    try:
        rows = retrieval.query("session_memory_chunks", query, n_results=n_results, where={"session_id": session_id})
        safe_rows = [row for row in rows if (row.get("metadata") or {}).get("visibility") != "keeper_only"]
        return ToolObservation(tool=TOOL_NAME, input={"query": query, "session_id": session_id, "n_results": n_results}, output={"memories": safe_rows})
    except Exception as exc:
        return ToolObservation(tool=TOOL_NAME, input={"query": query, "session_id": session_id}, output={"memories": []}, success=False, error=str(exc))
