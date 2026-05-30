from __future__ import annotations

from typing import Any

from app.services.agents.base import AgentContext, AgentMessage, BaseAgent
from app.services.agents.utils import ensure_options, fallback_response
from app.services.debug_events import DebugEmitter, emit_debug
from app.services.guardrails import (
    build_audit_record,
    sanitize_options,
    sanitize_player_output,
    validate_state_delta,
)
from app.services.prompt_config import build_keeper_response_prompt, build_reflection_prompt


class GuardAgent(BaseAgent):
    """负责确定性校验、Reflection 自检、修复/兜底与防剧透。

    输入 envelope.payload:
        narration: str
        options: list[str]
        state_delta: dict[str, Any]
        visible_context: dict[str, Any]
        keeper_only_context: dict[str, Any]
        turn_plan: dict[str, Any]
        story_state: dict[str, Any]
        react_trace: list[dict[str, Any]]
        tool_observations: list[dict[str, Any]]
        skill_results: list[dict[str, Any]]
        resolution: dict[str, Any]
        player_input: str
        session: models.GameSession
        character: models.Character
        intent: dict[str, Any]
        adjudication: dict[str, Any]
        skill_checks: list[dict[str, Any]]
        sanity_checks: list[dict[str, Any]]
        scenario_context: list[dict[str, Any]]
        entity_context: list[dict[str, Any]]
        clue_context: list[dict[str, Any]]
        memory_context: list[dict[str, Any]]
        rule_context: list[dict[str, Any]]
        debug_emit: DebugEmitter | None

    输出 envelope.payload:
        safe_narration: str
        safe_options: list[str]
        validated_delta: dict[str, Any]
        validation_report: dict[str, Any]
        leak_report: dict[str, Any]
        reflection_report: dict[str, Any]
        final_guardrail_report: dict[str, Any]
        needs_repair: bool
        repair_type: str
        repair_instruction: str
    """

    name = "GuardAgent"

    def run(self, envelope: AgentMessage) -> AgentMessage:
        payload = envelope.get("payload", {})
        debug_emit: DebugEmitter | None = payload.get("debug_emit")

        emit_debug(debug_emit, phase="agent_node", name="GuardAgent", status="start", message="GuardAgent 开始执行校验与 Reflection。")

        # 1. 确定性校验（状态增量合法性）
        state_delta: dict[str, Any] = payload.get("state_delta", {})
        story_state: dict[str, Any] = payload.get("story_state", {})
        validated_delta, validation_report = validate_state_delta(state_delta, story_state)

        # 2. Reflection 自检
        reflection_state = {
            "turn_plan": payload.get("turn_plan", {}),
            "react_trace": payload.get("react_trace", []),
            "narration": payload.get("narration", ""),
            "state_delta": validated_delta,
            "validation_report": validation_report,
            "leak_report": {},
        }
        reflection_report = self._run_reflection(reflection_state, debug_emit)

        # 3. 防剧透清洗
        session = payload["session"]
        known_clues = [clue.name for clue in session.clues] + validated_delta.get("generated_clues", [])
        safe_text, text_report = sanitize_player_output(payload.get("narration", ""), known_clues)
        safe_options, option_report = sanitize_options(payload.get("options", []), known_clues)
        leak_report = {"叙事": text_report, "选项": option_report}

        # 4. 修复判断
        result = str(reflection_report.get("result") or "pass")
        needs_repair = result in {"repair_text", "repair_state_delta", "ask_clarification", "fail_safe"} or bool(
            reflection_report.get("ask_clarification") or reflection_report.get("fail_safe")
        )
        repair_type = result if needs_repair else ""
        repair_instruction = str(reflection_report.get("repair_text") or "")

        final_guardrail_report = {
            "validation": validation_report,
            "leak": leak_report,
            "reflection": reflection_report,
        }

        emit_debug(
            debug_emit,
            phase="agent_node",
            name="GuardAgent",
            status="success",
            message=f"校验完成：Reflection {result}，需要修复 {needs_repair}。",
            metadata={"reflection_report": reflection_report, "final_guardrail_report": final_guardrail_report},
        )

        return AgentMessage(
            from_agent=self.name,
            phase="guard",
            payload={
                "safe_narration": safe_text,
                "safe_options": safe_options,
                "validated_delta": validated_delta,
                "validation_report": validation_report,
                "leak_report": leak_report,
                "reflection_report": reflection_report,
                "final_guardrail_report": final_guardrail_report,
                "needs_repair": needs_repair,
                "repair_type": repair_type,
                "repair_instruction": repair_instruction,
            },
            context_summary=f"Reflection {result}，校验 {'通过' if not needs_repair else '需要修复'}。",
        )

    def _run_reflection(self, state: dict[str, Any], debug_emit: DebugEmitter | None) -> dict[str, Any]:
        fallback = {
            "result": "pass",
            "issues": [],
            "repair_text": "",
            "repair_state_delta": {},
            "rerun_tool": "",
            "replan_once": False,
            "ask_clarification": False,
            "fail_safe": False,
            "reason": "未发现需要修复的问题。",
        }
        prompt = build_reflection_prompt(state)
        try:
            report = self.context.llm.chat_json(prompt, fallback=fallback)
        except Exception as exc:
            emit_debug(debug_emit, phase="agent_step", name="reflection_review", status="error", message=str(exc)[:500])
            report = fallback
        if not isinstance(report, dict):
            report = fallback
        return {**fallback, **report}
