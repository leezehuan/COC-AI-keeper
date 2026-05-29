from __future__ import annotations

from typing import Any, Literal

from sqlalchemy.orm import Session, selectinload

from app import models
from app.services.assistant_prompts import build_assistant_prompt, build_hyde_prompt, build_mqe_prompt, format_assistant_context
from app.services.debug_events import DebugEmitter, emit_debug
from app.services.llm import LLMClient
from app.services.retrieval import RetrievalService


AssistantMode = Literal["rules", "session_help", "auto"]
SPOILER_TERMS = ["真相", "幕后", "黑手", "怪物是什么", "达贡", "深潜者", "邪教", "结局", "秘密"]


class GameAssistantAgent:
    def __init__(self) -> None:
        self.llm = LLMClient()
        self.retrieval = RetrievalService()

    def chat(
        self,
        db: Session,
        *,
        message: str,
        session_id: str | None = None,
        mode: AssistantMode = "auto",
        enable_mqe: bool = True,
        mqe_expansions: int = 2,
        enable_hyde: bool | None = None,
        top_k: int = 5,
        candidate_pool_multiplier: int = 4,
        debug_emit: DebugEmitter | None = None,
    ) -> dict[str, Any]:
        effective_mode = infer_mode(message, mode)
        emit_debug(debug_emit, phase="assistant", name="infer_mode", status="success", message=f"推断模式：{effective_mode}", metadata={"mode": effective_mode, "raw_mode": mode})
        session = load_session(db, session_id) if session_id else None
        if is_spoiler_question(message):
            emit_debug(debug_emit, phase="assistant", name="spoiler_check", status="warning", message="检测到剧透问题，已拦截。")
            return spoiler_response(effective_mode)
        emit_debug(debug_emit, phase="assistant", name="expand_queries", status="start", message="开始 MQE 查询扩展。", metadata={"enable_mqe": enable_mqe, "mqe_expansions": mqe_expansions})
        expanded_queries = self.expand_queries(message, enable_mqe, mqe_expansions)
        emit_debug(debug_emit, phase="assistant", name="expand_queries", status="success", message=f"查询扩展完成：{len(expanded_queries)} 条。", metadata={"queries": expanded_queries})
        hyde_text = self.generate_hyde(message, enable_hyde, effective_mode)
        if hyde_text:
            expanded_queries.append(hyde_text)
            emit_debug(debug_emit, phase="assistant", name="hyde", status="success", message="HyDE 假设文档已生成。", metadata={"hyde_preview": hyde_text[:200]})
        emit_debug(debug_emit, phase="assistant", name="retrieve", status="start", message="开始检索规则与会话信息。", metadata={"collections": collections_for_mode(effective_mode, session), "top_k": top_k})
        rows = self.retrieve(expanded_queries, effective_mode, session, top_k, candidate_pool_multiplier)
        emit_debug(debug_emit, phase="assistant", name="retrieve", status="success", message=f"检索完成：{len(rows)} 条结果。", metadata={"result_count": len(rows), "visible_rows": [{"id": r.get("id", ""), "distance": r.get("distance")} for r in rows[:10]]})
        session_context = build_session_context(session) if session else "未绑定当前会话。"
        prompt = build_assistant_prompt(message=message, mode=effective_mode, context=format_assistant_context(rows), session_context=session_context)
        fallback = build_fallback_answer(message, rows, effective_mode)
        emit_debug(debug_emit, phase="assistant", name="generate_answer", status="start", message="开始生成助手回答。")
        answer = self.llm.chat_text(prompt, temperature=0.2) or fallback
        answer, blocked = sanitize_assistant_answer(answer, session)
        emit_debug(debug_emit, phase="assistant", name="generate_answer", status="success", message=f"回答生成完成，{len(answer)} 字。", metadata={"spoiler_blocked": blocked, "answer_preview": answer[:200]})
        citations = build_citations(rows)
        return {
            "answer": answer,
            "citations": citations,
            "retrieval_debug": {
                "queries": expanded_queries,
                "hyde_enabled": bool(hyde_text),
                "result_count": len(rows),
                "collections": collections_for_mode(effective_mode, session),
            },
            "spoiler_blocked": blocked,
            "mode": effective_mode,
        }

    def expand_queries(self, message: str, enable_mqe: bool, count: int) -> list[str]:
        queries = [message]
        if not enable_mqe:
            return queries
        fallback = {"queries": []}
        generated = self.llm.chat_json(build_mqe_prompt(message, max(1, min(count, 3))), fallback=fallback)
        for item in generated.get("queries", []):
            text = str(item).strip()
            if text and text not in queries:
                queries.append(text[:200])
        return queries

    def generate_hyde(self, message: str, enable_hyde: bool | None, mode: str) -> str:
        if enable_hyde is False:
            return ""
        if enable_hyde is None and mode == "session_help":
            return ""
        text = self.llm.chat_text(build_hyde_prompt(message), temperature=0.2)
        return text.strip()[:800] if text else ""

    def retrieve(self, queries: list[str], mode: str, session: models.GameSession | None, top_k: int, candidate_pool_multiplier: int) -> list[dict[str, Any]]:
        candidate_pool = max(top_k, top_k * max(candidate_pool_multiplier, 1))
        rows: list[dict[str, Any]] = []
        for query in queries:
            for collection in collections_for_mode(mode, session):
                try:
                    where = {"session_id": session.id} if collection == "session_memory_chunks" and session else None
                    rows.extend(self.retrieval.query(collection, query, n_results=min(candidate_pool, 20), where=where))
                except Exception:
                    continue
        return rank_and_dedupe(filter_visible_rows(rows, session))[:top_k]


def load_session(db: Session, session_id: str | None) -> models.GameSession | None:
    if not session_id:
        return None
    return (
        db.query(models.GameSession)
        .options(selectinload(models.GameSession.clues), selectinload(models.GameSession.inventory_items), selectinload(models.GameSession.turn_logs))
        .filter(models.GameSession.id == session_id)
        .one_or_none()
    )


def infer_mode(message: str, mode: str) -> str:
    if mode in {"rules", "session_help"}:
        return mode
    if any(word in message for word in ["我现在", "线索", "去哪", "下一步", "目前", "已发现"]):
        return "session_help"
    return "rules"


def is_spoiler_question(message: str) -> bool:
    return any(term in message for term in SPOILER_TERMS) and any(term in message for term in ["什么", "是谁", "为何", "真相", "告诉我"])


def spoiler_response(mode: str) -> dict[str, Any]:
    answer = "这涉及可能的剧本秘密。我不能直接透露真相。你可以从已发现线索、当前位置和异常现象出发继续调查；如果需要，我可以帮你整理已经发现的信息。"
    return {"answer": answer, "citations": [], "retrieval_debug": {"queries": [], "result_count": 0}, "spoiler_blocked": True, "mode": mode}


def collections_for_mode(mode: str, session: models.GameSession | None) -> list[str]:
    if mode == "session_help" and session is not None:
        return ["session_memory_chunks", "rule_chunks"]
    return ["rule_chunks"]


def filter_visible_rows(rows: list[dict[str, Any]], session: models.GameSession | None) -> list[dict[str, Any]]:
    visible: list[dict[str, Any]] = []
    known_clues = {clue.clue_key for clue in session.clues} | {clue.name for clue in session.clues} if session else set()
    for row in rows:
        metadata = row.get("metadata") or {}
        visibility = metadata.get("visibility") or metadata.get("secret_level")
        if visibility in {"keeper_only", "主持人秘密"}:
            continue
        clue_key = metadata.get("clue_key")
        if clue_key and metadata.get("source_type") == "clue" and session and clue_key not in known_clues:
            continue
        visible.append(row)
    return visible


def rank_and_dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    for row in rows:
        metadata = row.get("metadata") or {}
        key = str(metadata.get("memory_id") or metadata.get("chunk_id") or row.get("id") or f"{metadata.get('source_path')}:{metadata.get('chunk_index')}")
        existing = by_key.get(key)
        if existing is None or score_row(row) > score_row(existing):
            by_key[key] = row
    return sorted(by_key.values(), key=score_row, reverse=True)


def score_row(row: dict[str, Any]) -> float:
    metadata = row.get("metadata") or {}
    distance = row.get("distance")
    base = 1.0 - float(distance or 0.0)
    if metadata.get("source_type") in {"rulebook", "investigator_handbook"}:
        base += 0.2
    if metadata.get("visibility") in {"public", "player_visible"}:
        base += 0.1
    return base


def build_citations(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    citations: list[dict[str, Any]] = []
    for row in rows:
        metadata = row.get("metadata") or {}
        citations.append(
            {
                "id": row.get("id", ""),
                "title": metadata.get("title") or metadata.get("source_name") or row.get("id", ""),
                "source_type": metadata.get("source_type") or metadata.get("collection_type") or "unknown",
                "citation": metadata.get("citation") or metadata.get("title") or "未命名来源",
                "snippet": str(row.get("document") or "")[:240],
            }
        )
    return citations


def build_session_context(session: models.GameSession | None) -> str:
    if session is None:
        return "未绑定当前会话。"
    clue_text = "；".join(f"{clue.name}: {clue.content[:120]}" for clue in session.clues[:12]) or "暂无已发现线索。"
    inventory_text = "；".join(f"{item.name}×{item.quantity}" for item in session.inventory_items[:12]) or "暂无物品。"
    return f"当前位置：{session.current_location}\n当前场景：{session.current_scene}\n摘要：{session.summary}\n已发现线索：{clue_text}\n物品：{inventory_text}"


def build_fallback_answer(message: str, rows: list[dict[str, Any]], mode: str) -> str:
    if not rows:
        return "我暂时没有检索到可靠资料。你可以换一种问法，或询问更具体的规则术语。"
    first = rows[0]
    citation = (first.get("metadata") or {}).get("citation") or (first.get("metadata") or {}).get("title") or "资料片段"
    return f"根据可见资料，相关内容可参考：{citation}。简要来说，这个问题需要结合检索片段判断；如果你需要，我可以继续解释具体规则。"


def sanitize_assistant_answer(answer: str, session: models.GameSession | None) -> tuple[str, bool]:
    known = "\n".join(clue.name for clue in session.clues) if session else ""
    blocked = False
    sanitized = answer
    for term in ["达贡", "深潜者", "邪教", "幕后", "最终真相", "结局条件"]:
        if term in sanitized and term not in known:
            sanitized = sanitized.replace(term, "尚未确认的秘密")
            blocked = True
    if blocked:
        sanitized += "\n\n这部分可能涉及未发现内容，我已改为非剧透表述。"
    return sanitized, blocked
