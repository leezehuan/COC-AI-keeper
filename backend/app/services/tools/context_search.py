from __future__ import annotations

from typing import Any

from app.services.tools.base import ToolObservation, ToolSpec


TOOL_NAME = "ContextSearchTool"


def tool_spec() -> ToolSpec:
    return ToolSpec(
        name=TOOL_NAME,
        description="检索剧本、规则、实体、线索索引或当前会话记忆，只返回结构化观察，不写入状态。",
        input_schema={
            "query": "检索查询文本",
            "collections": "允许检索的 collection 名称列表",
            "n_results": "每个 collection 返回条数",
            "where": "可选 Chroma metadata 过滤条件",
        },
        constraints=[
            "只能读取检索结果，不能创建线索或修改状态。",
            "调用方必须先用计划白名单确认允许使用该工具。",
            "玩家可见输出前必须经过防剧透过滤。",
        ],
    )


def run_context_search(
    *,
    retrieval: Any,
    query: str,
    collections: list[str],
    n_results: int = 4,
    where: dict[str, Any] | None = None,
) -> ToolObservation:
    payload = {"query": query, "collections": collections, "n_results": n_results, "where": where or {}}
    rows: dict[str, list[dict[str, Any]]] = {}
    errors: dict[str, str] = {}
    for collection in collections:
        try:
            rows[collection] = retrieval.query(collection, query, n_results=n_results, where=where)
        except Exception as exc:
            rows[collection] = []
            errors[collection] = str(exc)
    return ToolObservation(
        tool=TOOL_NAME,
        input=payload,
        output={"results": rows, "errors": errors, "count": sum(len(items) for items in rows.values())},
        success=not errors,
        error="; ".join(f"{key}: {value}" for key, value in errors.items()),
    )
