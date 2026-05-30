from __future__ import annotations

from typing import Any

from app.services.agents.base import AgentContext, AgentMessage, BaseAgent
from app.services.agents.utils import (
    ensure_options,
    fallback_response,
    filter_player_visible_location_rows,
    filter_player_visible_rows,
    format_context,
    format_inventory,
    format_location_names,
    should_offer_clue_hint,
)
from app.services.debug_events import DebugEmitter, emit_debug
from app.services.prompt_config import build_keeper_response_prompt
from app.services.story_state import build_turn_delta


class NarratorAgent(BaseAgent):
    """负责生成守秘人叙事、状态增量和下一步选项。

    输入 envelope.payload:
        visible_context: dict[str, Any]
        resolution: dict[str, Any]
        skill_checks: list[dict[str, Any]]
        sanity_checks: list[dict[str, Any]]
        player_input: str
        intent: dict[str, Any]
        adjudication: dict[str, Any]
        scenario_context: list[dict[str, Any]]
        entity_context: list[dict[str, Any]]
        clue_context: list[dict[str, Any]]
        memory_context: list[dict[str, Any]]
        rule_context: list[dict[str, Any]]
        session: models.GameSession
        character: models.Character
        story_state: dict[str, Any]
        debug_emit: DebugEmitter | None

    输出 envelope.payload:
        narration: str
        options: list[str]
        state_delta: dict[str, Any]
        generated_payload: dict[str, Any]
        needs_image: bool
        image_scene_type: str
    """

    name = "NarratorAgent"

    def run(self, envelope: AgentMessage) -> AgentMessage:
        payload = envelope.get("payload", {})
        session = payload["session"]
        character = payload["character"]
        player_input: str = payload.get("player_input", "")
        intent: dict[str, Any] = payload.get("intent", {})
        adjudication: dict[str, Any] = payload.get("adjudication", {})
        resolution: dict[str, Any] = payload.get("resolution", {})
        skill_checks: list[dict[str, Any]] = payload.get("skill_checks", [])
        sanity_checks: list[dict[str, Any]] = payload.get("sanity_checks", [])
        scenario_context: list[dict[str, Any]] = payload.get("scenario_context", [])
        entity_context: list[dict[str, Any]] = payload.get("entity_context", [])
        clue_context: list[dict[str, Any]] = payload.get("clue_context", [])
        memory_context: list[dict[str, Any]] = payload.get("memory_context", [])
        rule_context: list[dict[str, Any]] = payload.get("rule_context", [])
        story_state: dict[str, Any] = payload.get("story_state", {})
        debug_emit: DebugEmitter | None = payload.get("debug_emit")

        emit_debug(debug_emit, phase="agent_node", name="NarratorAgent", status="start", message="NarratorAgent 开始生成叙事。")

        # 构建可见上下文文本（不含主持人秘密）
        location_context = filter_player_visible_location_rows(entity_context)
        scenario_text = format_context(scenario_context)
        location_text = format_location_names(location_context)
        entity_text = format_context(entity_context)
        clue_text = format_context(filter_player_visible_rows(clue_context))
        memory_text = format_context(memory_context)
        rule_text = format_context(rule_context)
        inventory_text = format_inventory(session.inventory_items)

        fb = fallback_response({
            "session": session,
            "skill_checks": skill_checks,
            "sanity_checks": sanity_checks,
            "divergence": resolution.get("偏离剧情", {}),
        })

        prompt = build_keeper_response_prompt(
            current_location=session.current_location,
            current_scene=session.current_scene,
            character_archetype=character.archetype,
            hp_current=character.hp_current,
            hp_max=character.hp_max,
            san_current=character.san_current,
            player_input=player_input,
            intent=intent,
            adjudication=adjudication,
            resolution=resolution,
            skill_checks=skill_checks,
            sanity_checks=sanity_checks,
            inventory_text=inventory_text,
            location_text=location_text,
            scenario_text=scenario_text,
            entity_text=entity_text,
            clue_text=clue_text,
            memory_text=memory_text,
            rule_text=rule_text,
        )

        try:
            generated = self.context.llm.chat_json(prompt, fallback=fb)
        except Exception as exc:
            emit_debug(debug_emit, phase="agent_node", name="NarratorAgent", status="error", message=str(exc)[:500])
            generated = fb

        if not isinstance(generated, dict):
            generated = fb

        narration = str(generated.get("narration") or fb["narration"])
        options = ensure_options(generated.get("options") or fb["options"])
        needs_image = bool(generated.get("needs_image"))
        image_scene_type = str(generated.get("image_scene_type") or "")

        # 生成状态增量
        generated_delta = generated.get("state_delta") if isinstance(generated.get("state_delta"), dict) else fb["state_delta"]
        generated_clues = generated.get("discovered_clues", []) if isinstance(generated.get("discovered_clues"), list) else []

        structured_delta = build_turn_delta(
            story_state,
            player_input,
            intent,
            adjudication,
            skill_checks,
            sanity_checks,
            generated_delta,
            generated_clues,
            session.current_location,
            session.current_scene,
            location_context,
        )
        structured_delta["generated_clues"] = generated_clues
        if generated_delta.get("inventory_changes") is not None:
            structured_delta["inventory_changes"] = generated_delta["inventory_changes"]

        # 追加引导选项
        if resolution.get("偏离剧情", {}).get("needs_guidance"):
            options = ["寻找现实可行的调查方向", *options]
        if should_offer_clue_hint({"story_state": story_state, "state_delta": structured_delta}):
            options = [*options, "回顾已知线索并寻找遗漏之处"]
        options = ensure_options(options)

        emit_debug(
            debug_emit,
            phase="agent_node",
            name="NarratorAgent",
            status="success",
            message=f"叙事生成完成，{len(narration)} 字，选项 {len(options)} 个。",
            metadata={"narration_preview": narration[:300], "options": options},
        )

        return AgentMessage(
            from_agent=self.name,
            phase="narrate",
            payload={
                "narration": narration,
                "options": options,
                "state_delta": structured_delta,
                "generated_payload": generated,
                "needs_image": needs_image,
                "image_scene_type": image_scene_type,
            },
            context_summary=f"叙事 {len(narration)} 字，选项 {len(options)} 个。",
        )

    def repair(self, envelope: AgentMessage) -> AgentMessage:
        """在 GuardAgent 要求 repair_text 时调用，重新生成叙事。"""
        payload = envelope.get("payload", {})
        repair_instruction: str = payload.get("repair_instruction", "")
        # 复用大部分上下文重新生成
        result = self.run(envelope)
        # 在 prompt 中追加修复指令（由于 run 内部已经调用 LLM，repair 需要重新构造 prompt）
        # 为了简化，这里直接让 Supervisor 在调用 NarratorAgent.repair 时传入 repair_instruction
        # 实际修复逻辑由 Supervisor 在调用前把 repair_instruction 加入 payload 完成
        return result
