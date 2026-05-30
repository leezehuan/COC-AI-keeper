from __future__ import annotations

from typing import Any

from app.services.agents.base import AgentContext, AgentMessage, BaseAgent
from app.services.agents.utils import (
    available_tool_names,
    choose_skill_name,
    clamp_int,
    ensure_list,
    fallback_turn_plan,
    normalize_turn_plan,
    to_int,
)
from app.services.debug_events import DebugEmitter, emit_debug
from app.services.prompt_config import build_turn_plan_prompt
from app.services.skills import SKILL_SPECS


class PlannerAgent(BaseAgent):
    """负责生成回合计划并校验白名单。

    输入 envelope.payload:
        visible_context: dict[str, Any]
        intent: dict[str, Any]
        player_input: str

    输出 envelope.payload:
        turn_plan: dict[str, Any]
        needs_clarification: bool
        plan_validation: dict[str, Any]
    """

    name = "PlannerAgent"

    def run(self, envelope: AgentMessage) -> AgentMessage:
        payload = envelope.get("payload", {})
        visible_context: dict[str, Any] = payload.get("visible_context", {})
        intent: dict[str, Any] = payload.get("intent", {})
        player_input: str = payload.get("player_input", "")
        debug_emit: DebugEmitter | None = payload.get("debug_emit")

        emit_debug(debug_emit, phase="agent_node", name="PlannerAgent", status="start", message="PlannerAgent 开始生成回合计划。")

        # 构建 fallback 以兼容异常场景
        partial_state = {
            "intent": intent,
            "player_input": player_input,
        }
        fallback = fallback_turn_plan(partial_state)

        prompt = build_turn_plan_prompt(
            current_location=str(visible_context.get("current_location") or ""),
            current_scene=str(visible_context.get("current_scene") or ""),
            current_time=str(visible_context.get("current_time") or ""),
            character_archetype=str(visible_context.get("character_archetype") or ""),
            inventory_text=str(visible_context.get("inventory_text") or ""),
            known_clues="；".join(str(item) for item in visible_context.get("known_clues", [])),
            summary=str(visible_context.get("summary") or ""),
            player_input=player_input,
            available_tools=available_tool_names(),
            available_skills=list(SKILL_SPECS.keys()),
        )

        try:
            generated = self.context.llm.chat_json(prompt, fallback=fallback)
        except Exception as exc:
            emit_debug(debug_emit, phase="agent_node", name="PlannerAgent", status="error", message=str(exc)[:500])
            generated = fallback

        plan = normalize_turn_plan(generated if isinstance(generated, dict) else {}, fallback)
        needs_clarification = bool(plan.get("needs_clarification"))

        # 校验计划白名单
        valid_tools = set(available_tool_names())
        valid_skills = set(SKILL_SPECS.keys())
        requested_tools = [str(item) for item in ensure_list(plan.get("allowed_tools"))]
        requested_skills = [str(item) for item in ensure_list(plan.get("allowed_skills"))]
        allowed_tools = [item for item in requested_tools if item in valid_tools]
        allowed_skills = [item for item in requested_skills if item in valid_skills]
        issues: list[str] = []
        if len(allowed_tools) != len(requested_tools):
            issues.append("移除了计划外或未知 Tool。")
        if len(allowed_skills) != len(requested_skills):
            issues.append("移除了计划外或未知 Skill。")
        if not allowed_skills:
            allowed_skills = [choose_skill_name(str(plan.get("action_type") or intent.get("action_type") or "调查"))]
            issues.append("补充了默认 Skill。")
        for skill_name in allowed_skills:
            for tool_name in SKILL_SPECS[skill_name].allowed_tools:
                if tool_name not in allowed_tools:
                    allowed_tools.append(tool_name)
        risk_level = clamp_int(to_int(plan.get("risk_level"), 1), 1, 5)
        plan["allowed_tools"] = allowed_tools
        plan["allowed_skills"] = allowed_skills
        plan["risk_level"] = risk_level
        plan_validation = {
            "valid": True,
            "issues": issues,
            "allowed_tools": allowed_tools,
            "allowed_skills": allowed_skills,
            "risk_level": risk_level,
        }
        if plan.get("needs_clarification"):
            needs_clarification = True

        emit_debug(
            debug_emit,
            phase="agent_node",
            name="PlannerAgent",
            status="success",
            message=f"计划生成完成：{plan.get('action_type', '未知')}，Skill {len(allowed_skills)} 个，Tool {len(allowed_tools)} 个。",
            metadata={"turn_plan": plan, "plan_validation": plan_validation},
        )

        return AgentMessage(
            from_agent=self.name,
            phase="plan",
            payload={
                "turn_plan": plan,
                "needs_clarification": needs_clarification,
                "plan_validation": plan_validation,
            },
            context_summary=f"计划：{plan.get('goal', '')}，需要澄清：{needs_clarification}",
        )
