# =============================================================================
# 【ContextSearchTool：上下文检索工具】
# =============================================================================
# 这是 RAG 检索的核心入口，负责从 ChromaDB 向量库中检索相关上下文。
# 可以检索多种 collection：剧本、实体、线索索引、规则、会话记忆。
#
# 重要约束：
# - 只读操作，不能创建线索或修改状态
# - 必须经过 PlannerAgent 白名单校验才能调用
# - 检索结果展示前必须经过 GuardAgent 防剧透过滤
# =============================================================================
from __future__ import annotations

from typing import Any

from app.services.tools.base import ToolObservation, ToolSpec


TOOL_NAME = "ContextSearchTool"


def tool_spec() -> ToolSpec:
    """返回 Tool 的规格说明（tool_spec = Tool 规格说明）。

    【中文名称】Tool 规格说明

    【功能说明】
    返回 ContextSearchTool 的规格说明，供 PlannerAgent 校验白名单
    和 LLM 理解 Tool 用途。

    【返回值】
    - ToolSpec: 包含名称、描述、输入格式、约束条件
    """
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
    """执行上下文检索（run_context_search = 运行上下文检索）。

    【中文名称】运行上下文检索

    【功能说明】
    对每个 collection 分别调用 ChromaDB 查询，汇总所有结果。
    如果某个 collection 查询失败，记录错误但不影响其他 collection。

    【执行流程】
    遍历 collections → 对每个 collection 调用 retrieval.query()
      → 成功：保存结果
      → 失败：记录错误，继续下一个
      → 汇总返回

    【参数说明】
    - retrieval: RetrievalService 实例
    - query: 检索查询文本
    - collections: 要检索的 collection 名称列表
    - n_results: 每个 collection 返回条数
    - where: 可选的 ChromaDB metadata 过滤条件

    【返回值】
    - ToolObservation: 包含所有 collection 的检索结果和错误信息
    """
    payload = {"query": query, "collections": collections, "n_results": n_results, "where": where or {}}
    rows: dict[str, list[dict[str, Any]]] = {}  # 每个 collection 的检索结果
    errors: dict[str, str] = {}  # 每个 collection 的错误信息
    for collection in collections:
        try:
            rows[collection] = retrieval.query(collection, query, n_results=n_results, where=where)
        except Exception as exc:
            rows[collection] = []  # 查询失败时返回空列表，不影响其他 collection
            errors[collection] = str(exc)
    return ToolObservation(
        tool=TOOL_NAME,
        input=payload,
        output={"results": rows, "errors": errors, "count": sum(len(items) for items in rows.values())},
        success=not errors,  # 所有 collection 都成功才算成功
        error="; ".join(f"{key}: {value}" for key, value in errors.items()),
    )
