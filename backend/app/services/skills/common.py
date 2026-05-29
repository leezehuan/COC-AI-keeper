from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.services.debug_events import DebugEmitter, emit_debug, detail_tool_observation
from app.services.skills.base import SkillResult, SkillSpec
from app.services.tools.clue_eligibility import run_clue_eligibility
from app.services.tools.context_search import run_context_search
from app.services.tools.inventory_lookup import run_inventory_lookup
from app.services.tools.memory_recall import run_memory_recall
from app.services.tools.rule_check import run_rule_check
from app.services.tools.scene_affordance import run_scene_affordance


DEFAULT_COLLECTIONS = ["scenario_chunks", "scenario_entities", "clue_index", "rule_chunks"]


def run_tool_with_debug(
    debug_emit: DebugEmitter | None,
    observations: list[dict[str, Any]],
    tool_name: str,
    start_message: str,
    handler: Callable[[], Any],
    start_metadata: dict[str, Any] | None = None,
) -> None:
    emit_debug(debug_emit, phase="tool", name=tool_name, status="start", message=start_message, metadata=start_metadata)
    try:
        observation = handler()
    except Exception as exc:
        emit_debug(debug_emit, phase="tool", name=tool_name, status="error", message=str(exc)[:500])
        raise
    payload = observation.as_dict()
    observations.append(payload)
    status = "success" if payload.get("success", True) else "warning"
    msg, detail = detail_tool_observation(payload)
    emit_debug(debug_emit, phase="tool", name=tool_name, status=status, message=msg, metadata=detail)


def run_generic_skill(*, spec: SkillSpec, state: dict[str, Any], runtime: dict[str, Any]) -> SkillResult:
    allowed_tools = set(runtime.get("allowed_tools") or [])
    observations: list[dict[str, Any]] = []
    session = state["session"]
    character = state["character"]
    intent = state.get("intent", {})
    query = build_skill_query(state)
    debug_emit = runtime.get("debug_emit")

    if "ContextSearchTool" in allowed_tools and runtime.get("retrieval") is not None:
        run_tool_with_debug(
            debug_emit,
            observations,
            "ContextSearchTool",
            "开始检索上下文。",
            lambda: run_context_search(
                retrieval=runtime["retrieval"],
                query=query,
                collections=runtime.get("collections") or DEFAULT_COLLECTIONS,
                n_results=int(runtime.get("n_results") or 3),
            ),
            start_metadata={"query": query, "collections": runtime.get("collections") or DEFAULT_COLLECTIONS},
        )

    if "InventoryLookupTool" in allowed_tools:
        run_tool_with_debug(
            debug_emit,
            observations,
            "InventoryLookupTool",
            "开始查询物品栏。",
            lambda: run_inventory_lookup(items=getattr(session, "inventory_items", []), query=str(intent.get("target") or "")),
            start_metadata={"target": str(intent.get("target") or "")},
        )

    if "SceneAffordanceTool" in allowed_tools:
        run_tool_with_debug(
            debug_emit,
            observations,
            "SceneAffordanceTool",
            "开始读取场景可交互信息。",
            lambda: run_scene_affordance(location_context=state.get("entity_context", []), story_state=state.get("story_state", {})),
            start_metadata={"location": getattr(session, "current_location", "")},
        )

    if "ClueEligibilityTool" in allowed_tools:
        known_keys = [str(getattr(clue, "clue_key", "")) for clue in getattr(session, "clues", [])]
        run_tool_with_debug(
            debug_emit,
            observations,
            "ClueEligibilityTool",
            "开始判断线索候选资格。",
            lambda: run_clue_eligibility(target=str(intent.get("target") or ""), clue_context=state.get("clue_context", []), known_clue_keys=known_keys),
            start_metadata={"target": str(intent.get("target") or ""), "known_clue_count": len(known_keys)},
        )

    if "MemoryRecallTool" in allowed_tools and runtime.get("retrieval") is not None:
        run_tool_with_debug(
            debug_emit,
            observations,
            "MemoryRecallTool",
            "开始召回会话记忆。",
            lambda: run_memory_recall(retrieval=runtime["retrieval"], query=query, session_id=session.id, n_results=3),
            start_metadata={"query": query, "session_id": session.id},
        )

    if should_run_rule_check(spec.name, state) and "RuleCheckTool" in allowed_tools:
        run_tool_with_debug(
            debug_emit,
            observations,
            "RuleCheckTool",
            "开始执行规则检定。",
            lambda: run_rule_check(
                message=state.get("player_input", ""),
                intent=intent,
                character_skills=character.skills,
                character_attributes=character.attributes,
                scenario_context=state.get("scenario_context", []),
                default_skill=str(intent.get("skill") or runtime.get("default_skill") or "侦查"),
                current_san=character.san_current,
                luck=character.luck,
            ),
            start_metadata={"default_skill": str(intent.get("skill") or runtime.get("default_skill") or "侦查"), "current_san": character.san_current},
        )

    return SkillResult(
        skill=spec.name,
        input={"player_input": state.get("player_input", ""), "intent": intent},
        observations=observations,
        result={
            "decision_summary": build_decision_summary(spec, observations),
            "candidate_resolution": build_candidate_resolution(spec, observations),
            "used_tools": [item.get("tool") for item in observations],
        },
    )


def build_skill_query(state: dict[str, Any]) -> str:
    session = state.get("session")
    intent = state.get("intent", {})
    return " ".join(
        [
            str(getattr(session, "current_location", "")),
            str(getattr(session, "current_scene", "")),
            str(state.get("player_input", "")),
            str(intent.get("target", "")),
            str(intent.get("skill", "")),
        ]
    ).strip()


def should_run_rule_check(skill_name: str, state: dict[str, Any]) -> bool:
    action_type = str((state.get("intent") or {}).get("action_type") or "")
    if skill_name in {"DangerAndSanitySkill", "CombatLiteSkill"}:
        return True
    return action_type in {"战斗", "说服", "恐吓", "潜行"} or bool((state.get("intent") or {}).get("skill"))


def build_decision_summary(spec: SkillSpec, observations: list[dict[str, Any]]) -> str:
    tool_names = [str(item.get("tool")) for item in observations if item.get("tool")]
    if not tool_names:
        return f"{spec.name} 未调用工具，仅保留行动意图供后续节点处理。"
    return f"{spec.name} 调用了 {'、'.join(tool_names)}，形成候选裁定。"


def build_candidate_resolution(spec: SkillSpec, observations: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "skill": spec.name,
        "observation_count": len(observations),
        "requires_synthesis": True,
        "no_direct_state_write": True,
    }
