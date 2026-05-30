from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session, selectinload

from app import models
from app.services.agents.base import AgentContext, AgentMessage, BaseAgent
from app.services.agents.utils import (
    format_inventory,
    heuristic_intent,
)
from app.services.debug_events import DebugEmitter, emit_debug
from app.services.prompt_config import build_intent_prompt
from app.services.story_state import ensure_story_state


class ContextAgent(BaseAgent):
    """负责加载会话状态、解析意图、构建可见上下文和 RAG 检索。

    输入 envelope.payload:
        db: Session
        session_id: str
        player_input: str
        debug_emit: DebugEmitter | None

    输出 envelope.payload:
        session: models.GameSession
        character: models.Character
        story_state: dict[str, Any]
        visible_context: dict[str, Any]
        keeper_only_context: dict[str, Any]
        intent: dict[str, Any]
        scenario_context: list[dict[str, Any]]
        entity_context: list[dict[str, Any]]
        clue_context: list[dict[str, Any]]
        memory_context: list[dict[str, Any]]
        rule_context: list[dict[str, Any]]
    """

    name = "ContextAgent"

    def run(self, envelope: AgentMessage) -> AgentMessage:
        payload = envelope.get("payload", {})
        db: Session = payload["db"]
        session_id: str = payload["session_id"]
        player_input: str = payload["player_input"]
        debug_emit: DebugEmitter | None = payload.get("debug_emit")

        emit_debug(debug_emit, phase="agent_node", name="ContextAgent", status="start", message="ContextAgent 开始加载状态与检索。")

        # 1. 加载会话与关联数据
        session = (
            db.query(models.GameSession)
            .options(
                selectinload(models.GameSession.character),
                selectinload(models.GameSession.clues),
                selectinload(models.GameSession.inventory_items),
                selectinload(models.GameSession.flags),
                selectinload(models.GameSession.turn_logs),
            )
            .filter(models.GameSession.id == session_id)
            .one()
        )
        character = session.character
        story_state = ensure_story_state(
            session.state, session.current_location, session.current_scene, session.current_time
        )

        # 2. 解析意图
        intent = self._parse_intent(session, player_input, debug_emit)

        # 3. 构建可见上下文
        visible_context = {
            "current_location": session.current_location,
            "current_scene": session.current_scene,
            "current_time": session.current_time,
            "character_archetype": character.archetype,
            "inventory_text": format_inventory(session.inventory_items),
            "known_clues": [clue.name for clue in session.clues],
            "summary": session.summary,
        }
        keeper_only_context = {"story_state": story_state}

        # 4. RAG 检索
        scenario_context, entity_context, clue_context, memory_context, rule_context = self._retrieve_context(
            session, player_input, intent, debug_emit
        )

        emit_debug(
            debug_emit,
            phase="agent_node",
            name="ContextAgent",
            status="success",
            message=(
                f"ContextAgent 完成：地点 {session.current_location}，"
                f"检索 剧本 {len(scenario_context)} 实体 {len(entity_context)} 线索 {len(clue_context)} 记忆 {len(memory_context)} 规则 {len(rule_context)}"
            ),
            metadata={"intent": intent},
        )

        return AgentMessage(
            from_agent=self.name,
            phase="context",
            payload={
                "session": session,
                "character": character,
                "story_state": story_state,
                "visible_context": visible_context,
                "keeper_only_context": keeper_only_context,
                "intent": intent,
                "scenario_context": scenario_context,
                "entity_context": entity_context,
                "clue_context": clue_context,
                "memory_context": memory_context,
                "rule_context": rule_context,
            },
            context_summary=f"会话 {session_id}，地点 {session.current_location}，意图 {intent.get('action_type', '未知')}",
        )

    def _parse_intent(self, session: models.GameSession, player_input: str, debug_emit: DebugEmitter | None) -> dict[str, Any]:
        fallback = heuristic_intent(player_input)
        clarification_context = self._build_clarification_context(session)
        prompt = build_intent_prompt(session.current_location, session.current_scene, player_input, clarification_context)
        try:
            parsed = self.context.llm.chat_json(prompt, fallback=fallback)
        except Exception as exc:
            emit_debug(debug_emit, phase="agent_step", name="parse_intent", status="error", message=str(exc)[:500])
            parsed = fallback
        if not isinstance(parsed, dict):
            parsed = fallback
        parsed = {**fallback, **{k: v for k, v in parsed.items() if v is not None}}
        return parsed

    def _build_clarification_context(self, session: models.GameSession) -> str:
        if not session.turn_logs:
            return ""
        latest_log = max(session.turn_logs, key=lambda log: log.turn_index)
        intent = latest_log.intent if isinstance(latest_log.intent, dict) else {}
        if not intent.get("needs_clarification"):
            return ""
        return (
            f"【上一轮是追问回合】\n"
            f"玩家原动作：{latest_log.player_input}\n"
            f"系统追问：{intent.get('clarification_question', '')}\n"
            f"请结合以上内容，将本轮玩家输入视为对追问的回答，推断完整意图。"
        )

    def _retrieve_context(
        self,
        session: models.GameSession,
        player_input: str,
        intent: dict[str, Any],
        debug_emit: DebugEmitter | None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        query = " ".join([
            session.current_location,
            session.current_scene,
            player_input,
            str(intent.get("target", "")),
            str(intent.get("skill", "")),
        ])
        emit_debug(debug_emit, phase="agent_step", name="retrieve_context", status="start", message="开始检索剧本、规则与会话记忆。", metadata={"query": query})

        retrieval = self.context.retrieval
        scenario_context: list[dict[str, Any]] = []
        entity_context: list[dict[str, Any]] = []
        clue_context: list[dict[str, Any]] = []
        memory_context: list[dict[str, Any]] = []
        rule_context: list[dict[str, Any]] = []

        try:
            scenario_context = retrieval.query("scenario_chunks", query, n_results=6)
        except Exception as exc:
            scenario_context = [{"id": "retrieval-error", "document": f"剧本检索暂不可用：{exc}", "metadata": {}, "distance": None}]
        try:
            entity_context = retrieval.query("scenario_entities", query, n_results=4)
        except Exception:
            entity_context = []
        try:
            clue_context = retrieval.query("clue_index", query, n_results=4)
        except Exception:
            clue_context = []
        try:
            memory_context = retrieval.query("session_memory_chunks", query, n_results=3, where={"session_id": session.id})
        except Exception:
            memory_context = []
        try:
            rule_context = retrieval.query("rule_chunks", query, n_results=3)
        except Exception:
            rule_context = []

        emit_debug(
            debug_emit,
            phase="agent_step",
            name="retrieve_context",
            status="success",
            message=(
                f"检索完成：剧本 {len(scenario_context)}、实体 {len(entity_context)}、"
                f"线索 {len(clue_context)}、记忆 {len(memory_context)}、规则 {len(rule_context)}。"
            ),
            metadata={
                "scenario_context": scenario_context,
                "entity_context": entity_context,
                "clue_context": clue_context,
                "memory_context": memory_context,
                "rule_context": rule_context,
            },
        )
        return scenario_context, entity_context, clue_context, memory_context, rule_context
