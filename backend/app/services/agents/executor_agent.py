# =============================================================================
# 【ExecutorAgent：计划执行 Agent】
# =============================================================================
# 这是回合流程的第三个 Agent，相当于"执行者/行动派"。
# 它按照 PlannerAgent 制定的计划，实际执行 Skill 和规则检定。
#
# 具体做四件事（按顺序）：
#
# 1. 执行 Skill（技能模板）
#    - 从计划中取出 Skill 名称（如 "InvestigateSkill"）
#    - 调用 run_skill() 执行该 Skill，Skill 内部会按顺序调用多个 Tool
#    - 收集每个 Tool 的观察结果（tool_observations）
#    - 如果 Skill 不在注册表中，标记 plan_gap=True（计划缺口）
#
# 2. 规则裁定（判断需要什么检定）
#    - 如果 Skill 执行过程中没有调用 RuleCheckTool
#    - 则独立调用 adjudicate_action() 判断：需要技能检定？理智检定？
#    - 裁定结果包含：needs_roll、difficulty、suggested_skill 等
#
# 3. 规则检定（掷骰子）
#    - 如果裁定需要掷骰（needs_roll=True）且 Skill 没有执行检定
#    - 则调用 execute_rule_tools() 实际掷骰
#    - 重要：骰点结果由 Python 代码生成，不让 LLM 编造！
#
# 4. 综合裁定（汇总结果）
#    - 汇总技能检定结果、理智检定结果
#    - 判断剧情偏离度（玩家行动是否偏离主线）
#    - 生成 resolution 字典，供 NarratorAgent 叙事使用
# =============================================================================
from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from app.services.agents.base import AgentContext, AgentMessage, BaseAgent
from app.services.agents.utils import (
    apply_rule_observation_to_state,
    infer_skill,
    summarize_skill_outcome,
    summarize_sanity_outcome,
)
from app.services.agent_monitor import AgentTraceRecorder
from app.services.debug_events import DebugEmitter, emit_debug
from app.services.guardrails import classify_divergence
from app.services.rules import (
    adjudicate_action,
    as_adjudication_dict,
    execute_rule_tools,
)
from app.services.skills import SKILL_SPECS, choose_skill_name, run_skill


class ExecutorAgent(BaseAgent):
    """计划执行 Agent（可以理解为"行动派/执行者"）。

    【中文名称】执行 Agent / 行动 Agent

    【功能说明】
    按照 PlannerAgent 制定的计划，实际执行 Skill、进行规则检定、掷骰子。
    这是真正"干活"的 Agent——前面的 Agent 都在准备，只有它动手。

    【为什么骰子由代码掷而不是 LLM】
    LLM 是语言模型，不擅长生成真正的随机数。如果让 LLM 掷骰子，
    它可能会"作弊"（总是给出有利结果）或产生不合理的分布。
    所以本系统所有骰点都由 Python 的 random 模块生成，确保公平。

    【输入（envelope.payload）】
    - turn_plan: dict           → 回合计划
    - visible_context: dict     → 玩家可见上下文
    - keeper_only_context: dict → 守秘人专用上下文
    - player_input: str         → 玩家输入
    - intent: dict              → 结构化意图
    - session: GameSession      → 游戏会话
    - character: Character      → 角色
    - scenario_context: list    → 剧本检索结果
    - entity_context: list      → 实体检索结果
    - clue_context: list        → 线索检索结果
    - memory_context: list      → 记忆检索结果
    - rule_context: list        → 规则检索结果

    【输出（envelope.payload）】
    - react_trace: list         → ReAct 执行轨迹（记录每一步做了什么）
    - tool_observations: list   → Tool 观察结果列表
    - skill_results: list       → Skill 执行结果列表
    - adjudication: dict        → 规则裁定结果
    - dice_results: list        → 骰点结果
    - skill_checks: list        → 技能检定结果
    - sanity_checks: list       → 理智检定结果
    - resolution: dict          → 综合裁定结果
    - plan_gap: bool            → 计划是否有缺口（无法执行时为 True）
    """

    name = "ExecutorAgent"

    def run(self, envelope: AgentMessage) -> AgentMessage:
        """执行回合计划（run = 运行/执行）。

        【中文名称】运行

        【功能说明】
        ExecutorAgent 的主入口方法。按顺序执行四步：
        1. 执行 Skill（调用 run_skill，收集 Tool 观察结果）
        2. 规则裁定（判断需要什么检定）
        3. 规则检定（掷骰子）
        4. 综合裁定（汇总所有结果）

        【什么是 plan_gap（计划缺口）】
        如果计划中指定的 Skill 在系统中不存在（比如 LLM 幻觉了一个
        不存在的 Skill），ExecutorAgent 无法执行，会设置 plan_gap=True。
        Supervisor 检测到 plan_gap 后会触发修复流程。

        【执行流程】
        turn_plan → 取出 Skill 名称
          → 检查 Skill 是否在 SKILL_SPECS 中
          → 不在 → 返回 plan_gap=True
          → 在 → run_skill(skill_name, payload, runtime)
          → 收集 tool_observations
          → 检查是否需要独立裁定（adjudicate_action）
          → 检查是否需要独立检定（execute_rule_tools）
          → 汇总 resolution
          → 打包返回 AgentMessage

        【参数说明】
        - envelope: 输入信封，payload 需包含 turn_plan、session、character 等

        【返回值】
        - AgentMessage: 输出信封，payload 包含执行结果和检定数据
        """
        payload = envelope.get("payload", {})
        plan: dict[str, Any] = payload.get("turn_plan", {})
        player_input: str = payload.get("player_input", "")
        intent: dict[str, Any] = payload.get("intent", {})
        session = payload["session"]
        character = payload["character"]
        scenario_context: list[dict[str, Any]] = payload.get("scenario_context", [])
        debug_emit: DebugEmitter | None = payload.get("debug_emit")
        trace_recorder: AgentTraceRecorder | None = payload.get("trace_recorder")

        with (trace_recorder.step(agent_name=self.name, step_name="run", phase="execute", input_payload=payload) if trace_recorder else null_trace_step()) as trace_step:
            result = self._run_impl(payload, plan, player_input, intent, session, character, scenario_context, debug_emit, trace_recorder)
            trace_step["output"] = result
            return result

    def _run_impl(
        self,
        payload: dict[str, Any],
        plan: dict[str, Any],
        player_input: str,
        intent: dict[str, Any],
        session: Any,
        character: Any,
        scenario_context: list[dict[str, Any]],
        debug_emit: DebugEmitter | None,
        trace_recorder: AgentTraceRecorder | None,
    ) -> AgentMessage:
        emit_debug(debug_emit, phase="agent_node", name="ExecutorAgent", status="start", message="ExecutorAgent 开始执行计划。")

        # ===== 1. 执行 Skill =====
        # 从计划中取出第一个 Skill 名称，如果计划中没有则根据 action_type 选择
        allowed_skills = [str(item) for item in plan.get("allowed_skills", []) if item]
        skill_name = str(allowed_skills[0] if allowed_skills else choose_skill_name(str(plan.get("action_type") or "调查")))

        if skill_name not in SKILL_SPECS:
            # Skill 不在注册表中，标记 plan_gap=True，Supervisor 会触发修复流程
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
                    "plan_gap": True,  # 标记计划缺口
                },
                context_summary="计划引用了未知 Skill，无法执行。",
            )

        # 构建 Skill 运行时参数
        runtime = {
            "allowed_tools": [str(item) for item in plan.get("allowed_tools", []) if item],  # 白名单 Tool
            "retrieval": self.context.retrieval,  # 检索服务
            "default_skill": intent.get("skill") or infer_skill(player_input),  # 默认技能
            "debug_emit": debug_emit,  # 调试事件发射器
            "trace_recorder": trace_recorder,  # Agent 监控记录器
        }

        emit_debug(debug_emit, phase="skill", name=skill_name, status="start", message="开始执行 Skill。", metadata={"allowed_tools": plan.get("allowed_tools", []), "action_type": plan.get("action_type", "")})
        try:
            with (trace_recorder.step(agent_name=self.name, step_name=f"run_skill:{skill_name}", phase="skill", input_payload={"skill_name": skill_name, "payload": payload, "runtime": runtime}) if trace_recorder else null_trace_step()) as trace_step:
                result = run_skill(skill_name, payload, runtime).as_dict()  # 执行 Skill
                trace_step["output"] = result
        except Exception as exc:
            emit_debug(debug_emit, phase="skill", name=skill_name, status="error", message=str(exc)[:500])
            raise

        observations = [item for item in result.get("observations", []) if isinstance(item, dict)]  # Tool 观察结果
        skill_meta: dict[str, Any] = {
            "used_tools": [item.get("tool") for item in observations],
            "decision_summary": result.get("result", {}).get("decision_summary", ""),
        }
        emit_debug(debug_emit, phase="skill", name=skill_name, status="success", message=f"Skill 完成，调用 {len(observations)} 个 Tool。", metadata=skill_meta)

        # 构建 ReAct 执行轨迹
        react_trace = [
            {
                "step": "run_skill",
                "skill": skill_name,
                "used_tools": [item.get("tool") for item in observations],
                "decision_summary": result.get("result", {}).get("decision_summary", ""),
            }
        ]

        # 从 Tool 观察结果中提取规则检定相关数据
        exec_state: dict[str, Any] = {
            "adjudication": {},
            "dice_results": [],
            "skill_checks": [],
            "sanity_checks": [],
        }
        apply_rule_observation_to_state(exec_state, observations)  # 提取 RuleCheckTool 的结果

        # ===== 2. 规则裁定 =====
        # 如果 Skill 中没有调用 RuleCheckTool，则独立执行裁定
        adjudication = exec_state.get("adjudication", {})
        if not adjudication:
            skill_name_norm = str(intent.get("skill") or infer_skill(player_input))
            adj = adjudicate_action(
                player_input, intent, character.skills, character.attributes,
                scenario_context, skill_name_norm, character.luck,
            )
            adjudication = as_adjudication_dict(adj)  # 转为可序列化字典
            exec_state["adjudication"] = adjudication

        # ===== 3. 规则检定 =====
        # 如果裁定需要掷骰且 Skill 中没有执行检定，则独立执行
        dice_results: list[dict[str, Any]] = exec_state.get("dice_results", [])
        skill_checks: list[dict[str, Any]] = exec_state.get("skill_checks", [])
        sanity_checks: list[dict[str, Any]] = exec_state.get("sanity_checks", [])

        if adjudication.get("needs_roll") and not skill_checks:
            emit_debug(debug_emit, phase="tool", name="RuleCheckTool", status="start", message="开始执行规则检定。", metadata={"adjudication": adjudication})
            try:
                with (trace_recorder.step(agent_name=self.name, step_name="RuleCheckTool", phase="tool", input_payload={"adjudication": adjudication, "san_current": character.san_current}) if trace_recorder else null_trace_step()) as trace_step:
                    results = execute_rule_tools(adjudication, character.san_current)
                    trace_step["output"] = results
            except Exception as exc:
                emit_debug(debug_emit, phase="tool", name="RuleCheckTool", status="error", message=str(exc)[:500])
                raise
            dice_results = results["dice_results"]
            skill_checks = results["skill_checks"]
            sanity_checks = results["sanity_checks"]
            emit_debug(debug_emit, phase="tool", name="RuleCheckTool", status="success", message=f"规则检定完成：技能 {len(skill_checks)} 次，理智 {len(sanity_checks)} 次。", metadata={"dice_results": dice_results, "skill_checks": skill_checks, "sanity_checks": sanity_checks})

        # ===== 4. 综合裁定结果 =====
        # 汇总技能结果、理智结果、剧情偏离度，供 NarratorAgent 使用
        divergence = classify_divergence(player_input, payload.get("story_state", {}))  # 剧情偏离度
        resolution = {
            "技能结果": summarize_skill_outcome(skill_checks),  # 技能检定摘要
            "理智结果": summarize_sanity_outcome(sanity_checks),  # 理智检定摘要
            "偏离剧情": divergence,  # 剧情偏离分类
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


@contextmanager
def null_trace_step():
    state: dict[str, Any] = {}
    yield state
