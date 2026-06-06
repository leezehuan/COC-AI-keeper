# =============================================================================
# 【GuardAgent：守卫/校验 Agent】
# =============================================================================
# 这是回合流程的第五个（也是最后一个）Agent，相当于"质检员/守门员"。
# 它是最后一道防线，确保输出给玩家的内容安全、一致、无剧透。
#
# 具体做四件事（按顺序）：
#
# 1. 确定性校验（validate_state_delta）
#    - 用代码规则检查状态增量是否合法
#    - 比如：HP 不能为负数、地点名称不能为空、时间不能倒退
#    - 这是"硬校验"——不需要 LLM，完全由代码逻辑判断
#
# 2. Reflection 自检（_run_reflection）
#    - 让 LLM 审查本回合的叙事和执行结果
#    - 检查：是否剧透？是否与已知事实矛盾？逻辑是否合理？
#    - 这是"软校验"——需要 LLM 的理解能力来判断
#
# 3. 防剧透清洗（sanitize_player_output / sanitize_options）
#    - 移除叙事和选项中不应向玩家透露的信息
#    - 比如：玩家还没发现的线索细节、NPC 的真实身份
#
# 4. 修复判断
#    - 根据 Reflection 结果决定是否需要修复
#    - 五种结果：pass（通过）/ repair_text（修复叙事）/ repair_state_delta（修复状态）
#      / ask_clarification（追问玩家）/ fail_safe（兜底处理）
# =============================================================================
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
    """守卫/校验 Agent（可以理解为"质检员/守门员"）。

    【中文名称】守卫 Agent / 校验 Agent

    【功能说明】
    回合流程的最后一道防线。对 NarratorAgent 的输出进行全方位检查，
    确保发给玩家的内容安全、一致、无剧透。如果发现问题，触发修复流程。

    【为什么需要它】
    如果把一次回合比作"出版一本书"：
    - ContextAgent = 资料收集
    - PlannerAgent = 大纲规划
    - ExecutorAgent = 内容创作
    - NarratorAgent = 文字润色
    - GuardAgent = 编辑审校（检查错别字、事实错误、敏感内容）
    没有 GuardAgent，就像没有编辑审校就出版——可能出大问题。

    【输入（envelope.payload）】
    - narration: str            → NarratorAgent 生成的叙事
    - options: list[str]        → 玩家选项
    - state_delta: dict         → 状态增量
    - visible_context: dict     → 玩家可见上下文
    - keeper_only_context: dict → 守秘人专用上下文
    - turn_plan: dict           → 回合计划
    - story_state: dict         → 剧情状态
    - react_trace: list         → ReAct 执行轨迹
    - tool_observations: list   → Tool 观察结果
    - skill_results: list       → Skill 执行结果
    - resolution: dict          → 综合裁定结果
    - player_input: str         → 玩家输入
    - session: GameSession      → 游戏会话
    - character: Character      → 角色
    - intent: dict              → 结构化意图
    - adjudication: dict        → 规则裁定
    - skill_checks: list        → 技能检定结果
    - sanity_checks: list       → 理智检定结果
    - scenario_context: list    → 剧本检索结果
    - entity_context: list      → 实体检索结果
    - clue_context: list        → 线索检索结果
    - memory_context: list      → 记忆检索结果
    - rule_context: list        → 规则检索结果

    【输出（envelope.payload）】
    - safe_narration: str           → 清洗后的安全叙事
    - safe_options: list[str]       → 清洗后的安全选项
    - validated_delta: dict         → 校验后的状态增量
    - validation_report: dict       → 确定性校验报告
    - leak_report: dict             → 剧透泄漏报告
    - reflection_report: dict       → Reflection 自检报告
    - final_guardrail_report: dict  → 综合守卫报告
    - needs_repair: bool            → 是否需要修复
    - repair_type: str              → 修复类型
    - repair_instruction: str       → 修复指令
    """

    name = "GuardAgent"

    def run(self, envelope: AgentMessage) -> AgentMessage:
        """执行校验与守卫（run = 运行/执行）。

        【中文名称】运行

        【功能说明】
        GuardAgent 的主入口方法。按顺序执行四步：
        1. 确定性校验（代码规则检查）
        2. Reflection 自检（LLM 审查）
        3. 防剧透清洗（移除敏感信息）
        4. 修复判断（决定是否需要修复）

        【执行流程】
        state_delta → validate_state_delta（确定性校验）
          → _run_reflection（LLM 自检）
          → sanitize_player_output（清洗叙事）
          → sanitize_options（清洗选项）
          → 判断 needs_repair
          → 打包返回 AgentMessage

        【参数说明】
        - envelope: 输入信封，payload 需包含 narration、options、state_delta 等

        【返回值】
        - AgentMessage: 输出信封，payload 包含清洗后的内容和校验报告
        """
        payload = envelope.get("payload", {})
        debug_emit: DebugEmitter | None = payload.get("debug_emit")

        emit_debug(debug_emit, phase="agent_node", name="GuardAgent", status="start", message="GuardAgent 开始执行校验与 Reflection。")

        # ===== 1. 确定性校验 =====
        # 验证 state_delta 的合法性：数值范围、逻辑一致性等
        state_delta: dict[str, Any] = payload.get("state_delta", {})
        story_state: dict[str, Any] = payload.get("story_state", {})
        validated_delta, validation_report = validate_state_delta(state_delta, story_state)

        # ===== 2. Reflection 自检 =====
        # 让 LLM 审查本回合的执行结果，检测叙事不一致、逻辑错误等问题
        reflection_state = {
            "turn_plan": payload.get("turn_plan", {}),
            "react_trace": payload.get("react_trace", []),
            "narration": payload.get("narration", ""),
            "state_delta": validated_delta,
            "validation_report": validation_report,
            "leak_report": {},
        }
        reflection_report = self._run_reflection(reflection_state, debug_emit)

        # ===== 3. 防剧透清洗 =====
        # 移除叙事和选项中不应向玩家透露的信息（如未发现线索的细节）
        session = payload["session"]
        known_clues = [clue.name for clue in session.clues] + validated_delta.get("generated_clues", [])  # 已知线索
        safe_text, text_report = sanitize_player_output(payload.get("narration", ""), known_clues)  # 清洗叙事
        safe_options, option_report = sanitize_options(payload.get("options", []), known_clues)  # 清洗选项
        leak_report = {"叙事": text_report, "选项": option_report}

        # ===== 4. 修复判断 =====
        # 根据 Reflection 结果决定是否需要修复
        result = str(reflection_report.get("result") or "pass")  # pass/repair_text/repair_state_delta/ask_clarification/fail_safe
        needs_repair = result in {"repair_text", "repair_state_delta", "ask_clarification", "fail_safe"} or bool(
            reflection_report.get("ask_clarification") or reflection_report.get("fail_safe")
        )
        repair_type = result if needs_repair else ""  # 修复类型
        repair_instruction = str(reflection_report.get("repair_text") or "")  # 修复指令

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
        """执行 Reflection 自检（_run_reflection = 运行自检/反思）。

        【中文名称】运行自检

        【功能说明】
        让 LLM 审查本回合的执行结果，检测叙事中的问题。
        这是"软校验"——利用 LLM 的理解能力来判断内容质量。

        【Reflection 的五种结果】
        - pass（通过）：一切正常，无需修复
        - repair_text（修复叙事）：叙事有问题，需要 NarratorAgent 重新生成
        - repair_state_delta（修复状态）：状态增量有问题，需要修正
        - ask_clarification（追问）：需要向玩家追问更多信息
        - fail_safe（兜底）：严重问题，使用预设的安全回复

        【参数说明】
        - state: 包含 turn_plan、react_trace、narration 等的字典
        - debug_emit: 调试事件发射器

        【返回值】
        - dict: Reflection 报告，包含 result、issues、repair_text 等字段
        """
        fallback = {
            "result": "pass",  # 默认通过
            "issues": [],  # 发现的问题列表
            "repair_text": "",  # 修复指令
            "repair_state_delta": {},  # 状态增量修复
            "rerun_tool": "",  # 需要重新运行的 Tool
            "replan_once": False,  # 是否需要重新规划
            "ask_clarification": False,  # 是否需要追问
            "fail_safe": False,  # 是否需要兜底
            "reason": "未发现需要修复的问题。",
        }
        prompt = build_reflection_prompt(state)
        try:
            report = self.context.llm.chat_json(prompt, fallback=fallback)  # LLM 自检
        except Exception as exc:
            emit_debug(debug_emit, phase="agent_step", name="reflection_review", status="error", message=str(exc)[:500])
            report = fallback  # LLM 失败时默认通过
        if not isinstance(report, dict):
            report = fallback
        return {**fallback, **report}  # 合并结果，确保所有字段都有值
