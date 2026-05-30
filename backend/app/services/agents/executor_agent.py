from __future__ import annotations

from typing import Any

from app.services.agents.base import AgentContext, AgentMessage, BaseAgent
from app.services.agents.utils import (
    apply_rule_observation_to_state,
    infer_skill,
    summarize_skill_outcome,
    summarize_sanity_outcome,
)
from app.services.debug_events import DebugEmitter, emit_debug
from app.services.guardrails import classify_divergence
from app.services.rules import (
    adjudicate_action,
    as_adjudication_dict,
    execute_rule_tools,
)
from app.services.skills import SKILL_SPECS, choose_skill_name, run_skill


class ExecutorAgent(BaseAgent):
    """负责在计划白名单内执行 Skill、规则检定与结果汇总。

    输入 envelope.payload:
        turn_plan: dict[str, Any]
        visible_context: dict[str, Any]
        keeper_only_context: dict[str, Any]
        player_input: str
        intent: dict[str, Any]
        session: models.GameSession
        character: models.Character
        scenario_context: list[dict[str, Any]]
        entity_context: list[dict[str, Any]]
        clue_context: list[dict[str, Any]]
        memory_context: list[dict[str, Any]]
        rule_context: list[dict[str, Any]]
        debug_emit: DebugEmitter | None

    输出 envelope.payload:
        react_trace: list[dict[str, Any]]
        tool_observations: list[dict[str, Any]]
        skill_results: list[dict[str, Any]]
        adjudication: dict[str, Any]
        dice_results: list[dict[str, Any]]
        skill_checks: list[dict[str, Any]]
        sanity_checks: list[dict[str, Any]]
        resolution: dict[str, Any]
        plan_gap: bool
    """

    name = "ExecutorAgent"

    def run(self, envelope: AgentMessage) -> AgentMessage:
        payload = envelope.get("payload", {})
        plan: dict[str, Any] = payload.get("turn_plan", {})
        player_input: str = payload.get("player_input", "")
        intent: dict[str, Any] = payload.get("intent", {})
        session = payload["session"]
        character = payload["character"]
        scenario_context: list[dict[str, Any]] = payload.get("scenario_context", [])
        debug_emit: DebugEmitter | None = payload.get("debug_emit")

        emit_debug(debug_emit, phase="agent_node", name="ExecutorAgent", status="start", message="ExecutorAgent 开始执行计划。")

        # 1. 执行 ReAct / Skill
        allowed_skills = [str(item) for item in plan.get("allowed_skills", []) if item]
        skill_name = str(allowed_skills[0] if allowed_skills else choose_skill_name(str(plan.get("action_type") or "调查")))

        if skill_name not in SKILL_SPECS:
            emit_debug(debug_emit, phase="skill", name=skill_name, status="warning", message="计划引用了未知 Skill。")
            return AgentMessage(
                from_agent=self.name,
                phase="execute",
                payload={
                    "react_trace": [{"step": "plan_gap", "reason": f"未知 Skill：{skill_name}"}],
                    "tool_observations": [],
                    "skill_results": [],
                    "adjudication": {},
                    "dice_results": [],
                    "skill_checks": [],
                    "sanity_checks": [],
                    "resolution": {},
                    "plan_gap": True,
                },
                context_summary="计划引用了未知 Skill，无法执行。",
            )

        runtime = {
            "allowed_tools": [str(item) for item in plan.get("allowed_tools", []) if item],
            "retrieval": self.context.retrieval,
            "default_skill": intent.get("skill") or infer_skill(player_input),
            "debug_emit": debug_emit,
        }

        emit_debug(debug_emit, phase="skill", name=skill_name, status="start", message="开始执行 Skill。", metadata={"allowed_tools": plan.get("allowed_tools", []), "action_type": plan.get("action_type", "")})
        try:
            result = run_skill(skill_name, payload, runtime).as_dict()
        except Exception as exc:
            emit_debug(debug_emit, phase="skill", name=skill_name, status="error", message=str(exc)[:500])
            raise

        observations = [item for item in result.get("observations", []) if isinstance(item, dict)]
        skill_meta: dict[str, Any] = {
            "used_tools": [item.get("tool") for item in observations],
            "decision_summary": result.get("result", {}).get("decision_summary", ""),
        }
        emit_debug(debug_emit, phase="skill", name=skill_name, status="success", message=f"Skill 完成，调用 {len(observations)} 个 Tool。", metadata=skill_meta)

        react_trace = [
            {
                "step": "run_skill",
                "skill": skill_name,
                "used_tools": [item.get("tool") for item in observations],
                "decision_summary": result.get("result", {}).get("decision_summary", ""),
            }
        ]

        # 收集技能执行产生的 adjudication / dice / checks
        exec_state: dict[str, Any] = {
            "adjudication": {},
            "dice_results": [],
            "skill_checks": [],
            "sanity_checks": [],
        }
        apply_rule_observation_to_state(exec_state, observations)

        # 2. 规则裁定（如果 Skill 没有产生 adjudication）
        adjudication = exec_state.get("adjudication", {})
        if not adjudication:
            skill_name_norm = str(intent.get("skill") or infer_skill(player_input))
            adj = adjudicate_action(
                player_input,
                intent,
                character.skills,
                character.attributes,
                scenario_context,
                skill_name_norm,
                character.luck,
            )
            adjudication = as_adjudication_dict(adj)
            exec_state["adjudication"] = adjudication

        # 3. 规则检定（如果裁定需要掷骰且尚未执行）
        dice_results: list[dict[str, Any]] = exec_state.get("dice_results", [])
        skill_checks: list[dict[str, Any]] = exec_state.get("skill_checks", [])
        sanity_checks: list[dict[str, Any]] = exec_state.get("sanity_checks", [])

        if adjudication.get("needs_roll") and not skill_checks:
            emit_debug(debug_emit, phase="tool", name="RuleCheckTool", status="start", message="开始执行规则检定。", metadata={"adjudication": adjudication})
            try:
                results = execute_rule_tools(adjudication, character.san_current)
            except Exception as exc:
                emit_debug(debug_emit, phase="tool", name="RuleCheckTool", status="error", message=str(exc)[:500])
                raise
            dice_results = results["dice_results"]
            skill_checks = results["skill_checks"]
            sanity_checks = results["sanity_checks"]
            emit_debug(debug_emit, phase="tool", name="RuleCheckTool", status="success", message=f"规则检定完成：技能 {len(skill_checks)} 次，理智 {len(sanity_checks)} 次。", metadata={"dice_results": dice_results, "skill_checks": skill_checks, "sanity_checks": sanity_checks})

        # 4. 综合裁定结果
        divergence = classify_divergence(player_input, payload.get("story_state", {}))
        resolution = {
            "技能结果": summarize_skill_outcome(skill_checks),
            "理智结果": summarize_sanity_outcome(sanity_checks),
            "偏离剧情": divergence,
            "裁定依据": adjudication.get("reason", "根据规则工具和当前场景裁定。"),
            "回合计划": {
                "goal": plan.get("goal", ""),
                "action_type": plan.get("action_type", ""),
                "allowed_tools": plan.get("allowed_tools", []),
                "allowed_skills": plan.get("allowed_skills", []),
                "risk_level": plan.get("risk_level", 1),
            },
            "ReAct执行": react_trace,
            "技能结果": [result],
        }

        emit_debug(
            debug_emit,
            phase="agent_node",
            name="ExecutorAgent",
            status="success",
            message=f"执行完成：Tool 观察 {len(observations)} 条，技能检定 {len(skill_checks)} 次。",
            metadata={"react_trace": react_trace, "skill_results": [result]},
        )

        return AgentMessage(
            from_agent=self.name,
            phase="execute",
            payload={
                "react_trace": react_trace,
                "tool_observations": observations,
                "skill_results": [result],
                "adjudication": adjudication,
                "dice_results": dice_results,
                "skill_checks": skill_checks,
                "sanity_checks": sanity_checks,
                "resolution": resolution,
                "plan_gap": False,
            },
            context_summary=f"Skill {skill_name} 完成，检定 {len(skill_checks)} 次，理智 {len(sanity_checks)} 次。",
        )
