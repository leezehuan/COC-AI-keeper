from __future__ import annotations

from typing import Any

from app.services.chunking import DocumentChunk
from app.services.llm import LLMClient
from app.services.prompt_config import build_turn_summary_prompt


SUMMARY_KEYS = ["当前剧情摘要", "玩家已知线索", "玩家当前目标", "重要NPC状态", "未解决问题", "当前危险", "下一步可能方向"]


def build_turn_summary(session: Any, state: dict[str, Any], llm: LLMClient) -> dict[str, Any]:
    fallback = fallback_summary(session, state)
    prompt = build_turn_summary_prompt(session, state)
    generated = llm.chat_json(prompt, fallback=fallback)
    return normalize_summary(generated, fallback)


def apply_summary_to_session(session: Any, state: dict[str, Any], summary: dict[str, Any]) -> None:
    session.summary = str(summary.get("当前剧情摘要") or "")[:4000]
    session_state = dict(getattr(session, "state", {}) or {})
    memory = dict(session_state.get("记忆") or {})
    long_summaries = memory.get("长期记忆摘要") if isinstance(memory.get("长期记忆摘要"), list) else []
    long_summaries = [*long_summaries, compact_summary_line(summary)][-12:]
    memory["当前场景摘要"] = session.summary
    memory["长期记忆摘要"] = long_summaries
    memory["玩家当前目标"] = ensure_string_list(summary.get("玩家当前目标"))[:6]
    memory["未解决问题"] = ensure_string_list(summary.get("未解决问题"))[:8]
    memory["下一步可能方向"] = ensure_string_list(summary.get("下一步可能方向"))[:6]
    session_state["记忆"] = memory
    session_state["last_summary"] = summary
    state["summary"] = summary
    session.state = session_state


def build_summary_memory_chunk(session_id: str, turn_index: int, summary: dict[str, Any]) -> DocumentChunk:
    text = (
        f"会话：{session_id}\n"
        f"回合范围：1-{turn_index}\n"
        f"记忆类型：summary\n"
        f"当前剧情摘要：{summary.get('当前剧情摘要', '')}\n"
        f"玩家已知线索：{'；'.join(ensure_string_list(summary.get('玩家已知线索')))}\n"
        f"玩家当前目标：{'；'.join(ensure_string_list(summary.get('玩家当前目标')))}\n"
        f"未解决问题：{'；'.join(ensure_string_list(summary.get('未解决问题')))}\n"
        f"下一步可能方向：{'；'.join(ensure_string_list(summary.get('下一步可能方向')))}"
    )
    return DocumentChunk(
        id=f"memory-summary:{session_id}:{turn_index}",
        text=text,
        metadata={
            "collection_type": "session_memory",
            "session_id": session_id,
            "turn_range": f"1-{turn_index}",
            "memory_type": "summary",
            "source_name": "长期记忆摘要",
            "title": f"第 {turn_index} 回合总结",
            "secret_level": "玩家可见",
            "rag_namespace": "session_memory",
            "source_type": "memory",
            "visibility": "player_visible",
            "is_rag_data": False,
            "data_source": "session_summary",
            "citation": f"长期记忆摘要 · 第 {turn_index} 回合",
        },
    )


def fallback_summary(session: Any, state: dict[str, Any]) -> dict[str, Any]:
    delta = state.get("state_delta", {}) if isinstance(state.get("state_delta"), dict) else {}
    recent_action = state.get("player_input", "玩家继续调查。")
    narration = str(state.get("narration") or "守秘人推进了当前场景。")[:240]
    clues = delta.get("generated_clues") if isinstance(delta.get("generated_clues"), list) else []
    clue_names = [str(item.get("name") or item.get("clue_key")) for item in clues if isinstance(item, dict)]
    existing_clues = [getattr(clue, "name", "") for clue in getattr(session, "clues", []) if getattr(clue, "name", "")]
    return {
        "当前剧情摘要": f"在{getattr(session, 'current_location', '当前地点')}，玩家行动为：{recent_action}。{narration}",
        "玩家已知线索": [*existing_clues, *clue_names][-12:],
        "玩家当前目标": ["继续调查当前场景", "整理已知线索"],
        "重要NPC状态": [],
        "未解决问题": ["灯塔熄灭的原因仍需确认", "岛上的异常来源仍不明确"],
        "当前危险": [f"危险等级 {getattr(session, 'danger_level', 1)}"],
        "下一步可能方向": ["检查附近可疑物", "前往新的可达地点", "整理线索并判断下一步"],
    }


def normalize_summary(value: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key in SUMMARY_KEYS:
        item = value.get(key, fallback.get(key))
        if key in {"玩家已知线索", "玩家当前目标", "重要NPC状态", "未解决问题", "当前危险", "下一步可能方向"}:
            normalized[key] = ensure_string_list(item)
        else:
            normalized[key] = str(item or fallback.get(key) or "")[:4000]
    return normalized


def ensure_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def compact_summary_line(summary: dict[str, Any]) -> str:
    text = str(summary.get("当前剧情摘要") or "").strip()
    return text[:240] if text else "本回合已记录。"
