# =============================================================================
# 【PlannerAgent：回合计划生成 Agent】
# =============================================================================
# 这是回合流程的第二个 Agent，相当于"作战参谋"。
# 它根据 ContextAgent 收集的情报，制定本回合的行动计划。
#
# 具体做三件事：
#
# 1. 调用 LLM 生成回合计划
#    - 输入：玩家可见的上下文 + 解析后的意图
#    - 输出：{action_type: "调查", allowed_tools: ["ContextSearch", "RuleCheck"], ...}
#    - 如果 LLM 调用失败，使用预设的回退计划（fallback）
#
# 2. 校验白名单（安全检查）
#    - LLM 可能会"幻觉"出不存在的 Tool 或 Skill
#    - PlannerAgent 会把计划中的 Tool/Skill 和系统白名单对比
#    - 不在白名单中的项会被移除，防止执行不存在的操作
#
# 3. 自动补充缺失项
#    - 如果 LLM 没有指定 Skill，根据 action_type 自动选择默认 Skill
#    - 如果 Skill 需要的 Tool 不在计划中，自动补充
#    - 确保计划总是完整可执行的
#
# 特殊处理：
# - 如果计划标记 needs_clarification=True，表示玩家输入太模糊
# - Supervisor 会跳过后续 Agent，直接向玩家追问
# =============================================================================
from __future__ import annotations

from contextlib import contextmanager
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
from app.services.agent_monitor import AgentTraceRecorder
from app.services.debug_events import DebugEmitter, emit_debug
from app.services.prompt_config import build_turn_plan_prompt
from app.services.skills import SKILL_SPECS


class PlannerAgent(BaseAgent):
    """回合计划生成 Agent（可以理解为"作战参谋"）。

    【中文名称】计划 Agent / 规划 Agent

    【功能说明】
    根据 ContextAgent 提供的上下文和意图，生成本回合的执行计划。
    计划内容包括：使用哪个 Skill、调用哪些 Tool、风险等级等。

    【为什么需要它】
    如果把一次回合比作"做菜"：
    - ContextAgent = 查看冰箱里有什么食材
    - PlannerAgent = 决定做什么菜、需要哪些厨具
    - ExecutorAgent = 实际动手炒菜
    没有计划就直接执行，就像没有菜谱就开火——容易翻车。

    【输入（envelope.payload）】
    - visible_context: dict  → 玩家可见的上下文
    - intent: dict           → 解析后的意图
    - player_input: str      → 玩家输入文本

    【输出（envelope.payload）】
    - turn_plan: dict           → 回合计划（action_type、allowed_tools、allowed_skills 等）
    - needs_clarification: bool → 是否需要追问玩家
    - plan_validation: dict     → 计划校验结果（包含发现的问题列表）
    """

    name = "PlannerAgent"

    def run(self, envelope: AgentMessage) -> AgentMessage:
        """生成回合计划并校验白名单（run = 运行/执行）。

        【中文名称】运行

        【功能说明】
        PlannerAgent 的主入口方法。按顺序执行三步：
        1. 调用 LLM 生成回合计划（JSON 格式）
        2. 校验计划中的 Tool 和 Skill 是否在白名单内
        3. 自动补充缺失的 Skill 和 Tool

        【什么是白名单校验】
        LLM 有时会"幻觉"——生成不存在的 Tool 名称。
        比如 LLM 可能说用 "MagicDetectTool"，但系统里根本没有这个 Tool。
        白名单校验就是把计划中的每一项和系统实际支持的列表对比，
        只保留合法的项。

        【执行流程】
        visible_context + intent → build_turn_plan_prompt → LLM.chat_json
          → normalize_turn_plan（规范化字段）
          → 过滤 allowed_tools（只保留白名单中的）
          → 过滤 allowed_skills（只保留白名单中的）
          → 补充缺失的 Skill（根据 action_type）
          → 补充 Skill 需要的 Tool
          → 打包返回 AgentMessage

        【参数说明】
        - envelope: 输入信封，payload 需包含 visible_context、intent、player_input

        【返回值】
        - AgentMessage: 输出信封，payload 包含 turn_plan、needs_clarification、plan_validation
        """
        payload = envelope.get("payload", {})  # payload = 负载数据
        visible_context: dict[str, Any] = payload.get("visible_context", {})  # visible_context = 可见上下文
        intent: dict[str, Any] = payload.get("intent", {})  # intent = 结构化意图
        player_input: str = payload.get("player_input", "")  # player_input = 玩家输入
        debug_emit: DebugEmitter | None = payload.get("debug_emit")  # debug_emit = 调试发射器
        trace_recorder: AgentTraceRecorder | None = payload.get("trace_recorder")

        with (trace_recorder.step(agent_name=self.name, step_name="run", phase="plan", input_payload=payload) if trace_recorder else null_trace_step()) as trace_step:
            result = self._run_impl(visible_context, intent, player_input, debug_emit, trace_recorder)
            trace_step["output"] = result
            return result

    def _run_impl(
        self,
        visible_context: dict[str, Any],
        intent: dict[str, Any],
        player_input: str,
        debug_emit: DebugEmitter | None,
        trace_recorder: AgentTraceRecorder | None,
    ) -> AgentMessage:
        emit_debug(debug_emit, phase="agent_node", name="PlannerAgent", status="start", message="PlannerAgent 开始生成回合计划。")

        # 构建回退计划：LLM 失败时使用
        partial_state = {  # partial_state = 部分状态：用于构建回退计划
            "intent": intent,
            "player_input": player_input,
        }
        fallback = fallback_turn_plan(partial_state)  # fallback = 回退计划：LLM失败时使用的默认计划

        prompt = build_turn_plan_prompt(  # prompt = 提示词：发给LLM的计划生成指令
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
            with (trace_recorder.step(agent_name=self.name, step_name="generate_plan", phase="agent_step", input_payload={"prompt": prompt, "fallback": fallback, "visible_context": visible_context, "intent": intent, "player_input": player_input}) if trace_recorder else null_trace_step()) as trace_step:
                generated = self.context.llm.chat_json(prompt, fallback=fallback)  # generated = LLM生成结果：LLM返回的回合计划
                trace_step["output"] = generated
        except Exception as exc:
            emit_debug(debug_emit, phase="agent_node", name="PlannerAgent", status="error", message=str(exc)[:500])
            generated = fallback  # LLM 失败时使用回退计划

        # 规范化计划：确保字段完整，缺失字段用回退值填充
        plan = normalize_turn_plan(generated if isinstance(generated, dict) else {}, fallback)  # plan = 回合计划：规范化后的执行计划
        needs_clarification = bool(plan.get("needs_clarification"))  # needs_clarification = 需要追问：玩家输入是否太模糊

        # ===== 校验计划白名单 =====
        # 确保 LLM 生成的 Tool 和 Skill 都是系统允许的，防止幻觉
        valid_tools = set(available_tool_names())  # valid_tools = 合法Tool集合：系统中所有可用的Tool名称
        valid_skills = set(SKILL_SPECS.keys())  # valid_skills = 合法Skill集合：系统中所有可用的Skill名称
        requested_tools = [str(item) for item in ensure_list(plan.get("allowed_tools"))]  # requested_tools = 请求的Tool：LLM计划中指定的Tool
        requested_skills = [str(item) for item in ensure_list(plan.get("allowed_skills"))]  # requested_skills = 请求的Skill：LLM计划中指定的Skill
        allowed_tools = [item for item in requested_tools if item in valid_tools]  # allowed_tools = 合法Tool：白名单过滤后的Tool列表
        allowed_skills = [item for item in requested_skills if item in valid_skills]  # allowed_skills = 合法Skill：白名单过滤后的Skill列表
        issues: list[str] = []  # issues = 问题列表：白名单校验中发现的问题
        if len(allowed_tools) != len(requested_tools):
            issues.append("移除了计划外或未知 Tool。")  # LLM 幻觉了不存在的 Tool
        if len(allowed_skills) != len(requested_skills):
            issues.append("移除了计划外或未知 Skill。")  # LLM 幻觉了不存在的 Skill
        if not allowed_skills:
            # 如果没有合法 Skill，根据 action_type 自动补充默认 Skill
            allowed_skills = [choose_skill_name(str(plan.get("action_type") or intent.get("action_type") or "调查"))]
            issues.append("补充了默认 Skill。")
        # 补充 Skill 所需的 Tool：如果 Skill 的 allowed_tools 中有 Tool 不在计划中，自动添加
        for skill_name in allowed_skills:
            for tool_name in SKILL_SPECS[skill_name].allowed_tools:
                if tool_name not in allowed_tools:
                    allowed_tools.append(tool_name)
        risk_level = clamp_int(to_int(plan.get("risk_level"), 1), 1, 5)  # risk_level = 风险等级：1-5，数值越高越危险
        plan["allowed_tools"] = allowed_tools
        plan["allowed_skills"] = allowed_skills
        plan["risk_level"] = risk_level
        plan_validation = {  # plan_validation = 计划校验结果：包含校验问题和最终Tool/Skill列表
            "valid": True,
            "issues": issues,  # 校验中发现的问题
            "allowed_tools": allowed_tools,
            "allowed_skills": allowed_skills,
            "risk_level": risk_level,
        }
        if plan.get("needs_clarification"):
            needs_clarification = True  # 需要追问玩家

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


@contextmanager
def null_trace_step():
    state: dict[str, Any] = {}
    yield state
