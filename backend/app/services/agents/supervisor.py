# =============================================================================
# 【KeeperSupervisor：多 Agent 调度器】
# =============================================================================
# 这是整个回合执行流程的"大脑"——负责按顺序调度各个 Agent 并处理异常。
# 你可以把它理解为"项目经理"：自己不干活，但确保每个专业工人按时完成任务。
#
# 完整的回合流程（run_turn）：
#
# Phase 1: ContextAgent（情报收集）
#   → 加载会话状态、解析玩家意图、RAG 检索
#
# Phase 2: PlannerAgent（制定计划）
#   → 生成回合计划、校验白名单
#   → 如果需要追问 → 跳过后续 Phase，直接追问玩家
#
# Phase 3: ExecutorAgent（执行计划）
#   → 执行 Skill、规则裁定、掷骰检定、综合裁定
#
# Phase 4: NarratorAgent（生成叙事）
#   → 生成守秘人叙事文本、玩家选项、状态增量
#
# Phase 5: GuardAgent（质量检查）
#   → 确定性校验、Reflection 自检、防剧透清洗、修复判断
#
# Phase 5.5: Repair Loop（修复循环，最多 2 次）
#   → 如果 GuardAgent 发现问题，根据修复类型执行相应操作
#   → repair_text: 让 NarratorAgent 重新生成叙事
#   → repair_state_delta: 修复状态增量
#   → ask_clarification/fail_safe: 构造追问/兜底响应
#   → replan_once: 重新规划一次
#
# Phase 6: _commit_state（落库保存）
#   → 更新 PostgreSQL（状态、线索、物品、回合日志）
#   → 写入 ChromaDB（会话记忆向量）
#
# 对外接口与旧 KeeperAgent.run_turn 完全兼容。
# =============================================================================
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app import models
from app.services.agents.base import AgentContext, AgentMessage, BaseAgent
from app.services.agents.context_agent import ContextAgent
from app.services.agents.executor_agent import ExecutorAgent
from app.services.agents.guard_agent import GuardAgent
from app.services.agents.narrator_agent import NarratorAgent
from app.services.agents.planner_agent import PlannerAgent
from app.services.agents.utils import (
    build_session_memory_chunk,
    ensure_options,
    update_no_clue_counter,
)
from app.services.chunking import DocumentChunk
from app.services.debug_events import DebugEmitter, emit_debug
from app.services.guardrails import validate_state_delta
from app.services.inventory import apply_inventory_changes
from app.services.llm import LLMClient
from app.services.retrieval import RetrievalService
from app.services.story_state import apply_turn_delta
from app.services.summary import apply_summary_to_session, build_summary_memory_chunk, build_turn_summary
from app.utils import safe_key


class KeeperSupervisor:
    """多 Agent 调度器（可以理解为"项目经理/指挥中心"）。

    【中文名称】守秘人调度器 / 主管 Agent

    【功能说明】
    这是整个回合流程的中央控制器。它不执行具体的业务逻辑，
    而是负责：
    1. 按顺序调度 5 个专业 Agent
    2. 在 Agent 之间传递数据（组装 AgentMessage 信封）
    3. 处理修复循环（最多 2 次）
    4. 最终将结果写入数据库

    【为什么需要调度器】
    5 个 Agent 各司其职，但需要有人协调它们的工作顺序和数据传递。
    就像建筑工地需要工头来协调电工、水管工、木工的工作顺序。
    没有 Supervisor，Agent 们不知道谁先谁后、数据怎么传递。

    【对外接口】
    run_turn(db, session_id, player_input, debug_emit) → dict
    与旧版 KeeperAgent.run_turn 保持完全兼容。
    """

    def __init__(self) -> None:
        """初始化调度器（__init__ = 构造函数/初始化方法）。

        【中文名称】初始化

        【功能说明】
        创建 KeeperSupervisor 实例时自动调用。完成以下初始化工作：
        1. 创建 LLM 客户端（用于调用大语言模型）
        2. 创建检索服务（用于查询 ChromaDB）
        3. 创建共享上下文（AgentContext，注入 LLM 和检索服务）
        4. 创建 5 个专业 Agent 实例（注入共享上下文）

        【创建的 Agent 列表】
        - context_agent: ContextAgent → 上下文加载与意图解析
        - planner_agent: PlannerAgent → 回合计划生成
        - executor_agent: ExecutorAgent → 计划执行与规则检定
        - narrator_agent: NarratorAgent → 守秘人叙事生成
        - guard_agent: GuardAgent → 守卫校验与防剧透

        【参数说明】无参数
        【返回值】无（返回 None）
        """
        self.llm = LLMClient()  # 创建 LLM 客户端
        self.retrieval = RetrievalService()  # 创建向量检索服务
        self.context = AgentContext(llm=self.llm, retrieval=self.retrieval)  # 创建共享服务上下文
        # 创建五个专业 Agent，注入共享上下文
        self.context_agent = ContextAgent(self.context)  # 上下文加载与意图解析
        self.planner_agent = PlannerAgent(self.context)  # 回合计划生成
        self.executor_agent = ExecutorAgent(self.context)  # 计划执行与规则检定
        self.narrator_agent = NarratorAgent(self.context)  # 守秘人叙事生成
        self.guard_agent = GuardAgent(self.context)  # 守卫校验与防剧透

    def run_turn(self, db: Session, session_id: str, player_input: str, debug_emit: DebugEmitter | None = None) -> dict[str, Any]:
        """执行一个完整的游戏回合（run_turn = 运行回合）。

        【中文名称】运行回合

        【功能说明】
        这是 Supervisor 最重要的方法，也是外部调用的唯一入口。
        接收玩家输入，调度 5 个 Agent 依次处理，最终返回包含叙事、
        选项、状态变化等信息的字典。

        【完整的 6 个阶段】
        Phase 1: ContextAgent → 加载状态、解析意图、RAG 检索
        Phase 2: PlannerAgent → 生成计划、校验白名单
          （如果需要追问，跳到 _clarify_and_commit）
        Phase 3: ExecutorAgent → 执行 Skill、规则检定
        Phase 4: NarratorAgent → 生成叙事和选项
        Phase 5: GuardAgent → 校验、自检、防剧透
        Phase 5.5: Repair Loop → 最多 2 次修复循环
        Phase 6: _commit_state → 写入数据库

        【参数说明】
        - db: Session → SQLAlchemy 数据库会话，用于查询和写入 PostgreSQL
        - session_id: str → 游戏会话的唯一标识符
        - player_input: str → 玩家输入的自然语言文本
        - debug_emit: DebugEmitter | None → 调试事件发射器（可选）

        【返回值】
        - dict: 兼容旧版 KeeperState 的字典，包含：
          - narration: 守秘人叙事文本
          - options: 玩家可选行动列表
          - state_delta: 状态变化
          - discovered_clues: 新发现的线索
          - skill_checks / sanity_checks: 检定结果
          - 等等...
        """
        emit_debug(debug_emit, phase="stream", name="supervisor", status="start", message="Supervisor 开始调度回合。")

        # === Phase 1: 加载上下文 ===
        ctx_envelope = AgentMessage(
            payload={"db": db, "session_id": session_id, "player_input": player_input, "debug_emit": debug_emit}
        )
        ctx_result = self.context_agent.run(ctx_envelope)
        ctx = ctx_result["payload"]

        # === Phase 2: 生成计划 ===
        plan_envelope = AgentMessage(
            payload={
                "visible_context": ctx["visible_context"],
                "intent": ctx["intent"],
                "player_input": player_input,
                "debug_emit": debug_emit,
            }
        )
        plan_result = self.planner_agent.run(plan_envelope)
        plan = plan_result["payload"]

        # 若需要追问，跳过后续 Phase，直接构造澄清结果并落库
        if plan["needs_clarification"]:
            emit_debug(debug_emit, phase="agent_node", name="Supervisor", status="success", message="玩家输入模糊，进入澄清分支。")
            return self._clarify_and_commit(
                db, ctx["session"], ctx["character"], player_input, ctx["intent"],
                plan["turn_plan"], ctx["story_state"], debug_emit
            )

        # === Phase 3: 执行计划（Skill + 规则检定） ===
        exec_envelope = AgentMessage(
            payload={
                "turn_plan": plan["turn_plan"],
                "visible_context": ctx["visible_context"],
                "keeper_only_context": ctx["keeper_only_context"],
                "player_input": player_input,
                "intent": ctx["intent"],
                "session": ctx["session"],
                "character": ctx["character"],
                "scenario_context": ctx["scenario_context"],
                "entity_context": ctx["entity_context"],
                "clue_context": ctx["clue_context"],
                "memory_context": ctx["memory_context"],
                "rule_context": ctx["rule_context"],
                "debug_emit": debug_emit,
            }
        )
        exec_result = self.executor_agent.run(exec_envelope)
        exec_data = exec_result["payload"]

        # === Phase 4: 生成叙事（守秘人回应 + 状态增量） ===
        narr_envelope = AgentMessage(
            payload={
                "visible_context": ctx["visible_context"],
                "resolution": exec_data["resolution"],
                "skill_checks": exec_data["skill_checks"],
                "sanity_checks": exec_data["sanity_checks"],
                "player_input": player_input,
                "intent": ctx["intent"],
                "adjudication": exec_data["adjudication"],
                "scenario_context": ctx["scenario_context"],
                "entity_context": ctx["entity_context"],
                "clue_context": ctx["clue_context"],
                "memory_context": ctx["memory_context"],
                "rule_context": ctx["rule_context"],
                "session": ctx["session"],
                "character": ctx["character"],
                "story_state": ctx["story_state"],
                "debug_emit": debug_emit,
            }
        )
        narr_result = self.narrator_agent.run(narr_envelope)
        narr_data = narr_result["payload"]

        # === Phase 5: 校验与 Reflection（确定性校验 + 自检 + 防剧透） ===
        guard_envelope = AgentMessage(
            payload={
                "narration": narr_data["narration"],
                "options": narr_data["options"],
                "state_delta": narr_data["state_delta"],
                "visible_context": ctx["visible_context"],
                "keeper_only_context": ctx["keeper_only_context"],
                "turn_plan": plan["turn_plan"],
                "story_state": ctx["story_state"],
                "react_trace": exec_data["react_trace"],
                "tool_observations": exec_data["tool_observations"],
                "skill_results": exec_data["skill_results"],
                "resolution": exec_data["resolution"],
                "player_input": player_input,
                "session": ctx["session"],
                "character": ctx["character"],
                "intent": ctx["intent"],
                "adjudication": exec_data["adjudication"],
                "skill_checks": exec_data["skill_checks"],
                "sanity_checks": exec_data["sanity_checks"],
                "scenario_context": ctx["scenario_context"],
                "entity_context": ctx["entity_context"],
                "clue_context": ctx["clue_context"],
                "memory_context": ctx["memory_context"],
                "rule_context": ctx["rule_context"],
                "debug_emit": debug_emit,
            }
        )
        guard_result = self.guard_agent.run(guard_envelope)
        guard_data = guard_result["payload"]

        # === Phase 5.5: Repair Loop（最多 2 次）===
        # 当 GuardAgent 判定需要修复时，根据修复类型执行相应操作
        # repair_text: 让 NarratorAgent 重新生成叙事
        # repair_state_delta: 修复状态增量
        # ask_clarification/fail_safe: 构造追问/兤底响应，不再继续修复
        # replan_once: 重新规划一次
        repair_attempts = 0
        while guard_data["needs_repair"] and repair_attempts < 2:
            repair_attempts += 1
            emit_debug(
                debug_emit,
                phase="agent_node",
                name="Supervisor",
                status="start",
                message=f"触发修复循环 #{repair_attempts}，类型 {guard_data['repair_type']}。",
            )

            repair_type = guard_data["repair_type"]
            if repair_type == "repair_text":
                # 让 NarratorAgent 重新生成叙事，注入修复指令
                repair_envelope = AgentMessage(
                    payload={**narr_envelope["payload"], "repair_instruction": guard_data["repair_instruction"]}
                )
                narr_result = self.narrator_agent.repair(repair_envelope)
                narr_data = narr_result["payload"]
            elif repair_type == "repair_state_delta":
                # 合并修复的状态增量并重新校验
                merged = {**narr_data["state_delta"], **guard_data.get("repair_state_delta", {})}
                validated_delta, _ = validate_state_delta(merged, ctx["story_state"])
                narr_data = {**narr_data, "state_delta": validated_delta}
            elif repair_type in ("ask_clarification", "fail_safe"):
                # 追问或兤底：构造固定响应，不再继续修复循环
                narr_data["narration"] = guard_data["repair_instruction"] or "这个行动还需要更多明确目标。你可以说明想调查的对象、使用的物品或采取的方式。"
                narr_data["options"] = ["说明具体目标", "换一种调查方式", "回顾已知线索", "自定义行动"]
                narr_data["state_delta"] = {"clarification": True, "time_cost_minutes": 0, "danger_delta": 0}
                break  # 一旦进入 clarify/fail_safe，不再继续修复
            elif repair_type == "replan_once":
                # 重新规划一次：重新调用 PlannerAgent
                plan_envelope = AgentMessage(
                    payload={
                        "visible_context": ctx["visible_context"],
                        "intent": ctx["intent"],
                        "player_input": player_input,
                        "debug_emit": debug_emit,
                    }
                )
                plan_result = self.planner_agent.run(plan_envelope)
                plan = plan_result["payload"]
                if plan["needs_clarification"]:
                    return self._clarify_and_commit(
                        db, ctx["session"], ctx["character"], player_input, ctx["intent"],
                        plan["turn_plan"], ctx["story_state"], debug_emit
                    )
                # 简化处理：直接 break 避免无限递归
                break

            # 修复后重新 Guard 校验
            guard_envelope = AgentMessage(
                payload={**guard_envelope["payload"], **narr_data}  # 用修复后的数据更新信封
            )
            guard_result = self.guard_agent.run(guard_envelope)
            guard_data = guard_result["payload"]

        # === Phase 6: 最终落库 ===
        # 使用 GuardAgent 清洗后的安全叙事和校验后的状态增量
        emit_debug(debug_emit, phase="stream", name="supervisor", status="success", message="Supervisor 完成调度，准备落库。")
        return self._commit_state(
            db=db,
            session=ctx["session"],
            character=ctx["character"],
            player_input=player_input,
            intent=ctx["intent"],
            turn_plan=plan["turn_plan"],
            plan_validation=plan["plan_validation"],
            react_trace=exec_data["react_trace"],
            tool_observations=exec_data["tool_observations"],
            skill_results=exec_data["skill_results"],
            reflection_report=guard_data["reflection_report"],
            final_guardrail_report=guard_data["final_guardrail_report"],
            adjudication=exec_data["adjudication"],
            dice_results=exec_data["dice_results"],
            skill_checks=exec_data["skill_checks"],
            sanity_checks=exec_data["sanity_checks"],
            resolution=exec_data["resolution"],
            narration=guard_data["safe_narration"],
            options=guard_data["safe_options"],
            state_delta=guard_data["validated_delta"],
            story_state=ctx["story_state"],
            needs_image=narr_data.get("needs_image", False),
            image_scene_type=narr_data.get("image_scene_type", ""),
            debug_emit=debug_emit,
        )

    def _clarify_and_commit(
        self,
        db: Session,
        session: models.GameSession,
        character: models.Character,
        player_input: str,
        intent: dict[str, Any],
        turn_plan: dict[str, Any],
        story_state: dict[str, Any],
        debug_emit: DebugEmitter | None,
    ) -> dict[str, Any]:
        """处理追问回合（_clarify_and_commit = 追问并落库）。

        【中文名称】追问并落库

        【功能说明】
        当 PlannerAgent 判定玩家输入太模糊、需要追问时调用。
        不执行 Skill 和规则检定，直接构造一个追问问题作为叙事，
        然后调用 _commit_state 落库。

        【追问机制】
        1. PlannerAgent 发现玩家输入模糊 → needs_clarification=True
        2. Supervisor 调用 _clarify_and_commit
        3. 生成追问问题（如"你想具体调查哪里？"）
        4. 落库（不消耗游戏时间）
        5. 下一轮 ContextAgent 会识别追问回合，正确理解玩家回答

        【参数说明】
        - db: 数据库会话
        - session: 游戏会话对象
        - character: 角色对象
        - player_input: 玩家输入
        - intent: 解析后的意图
        - turn_plan: 回合计划（包含 clarification_question）
        - story_state: 剧情状态
        - debug_emit: 调试事件发射器

        【返回值】
        - dict: 兼容旧版 KeeperState 的字典
        """
        question = turn_plan.get("clarification_question") or intent.get("clarification_question") or "你想具体调查哪里，或以什么方式行动？"
        narration = str(question)  # 叙事就是追问问题
        options = ["检查附近明显可疑之处", "询问同伴的看法", "观察环境", "自定义行动"]
        state_delta = {"clarification": True, "time_cost_minutes": 0, "danger_delta": 0}  # 追问回合不消耗时间

        audit = {
            "意图": intent,
            "裁定": {},
            "偏离剧情": {},
            "检索": {},
            "状态校验": {},
            "防剧透": {},
        }

        return self._commit_state(
            db=db,
            session=session,
            character=character,
            player_input=player_input,
            intent=intent,
            turn_plan=turn_plan,
            plan_validation={},
            react_trace=[],
            tool_observations=[],
            skill_results=[],
            reflection_report={"result": "pass", "reason": "澄清回合，跳过 Reflection。"},
            final_guardrail_report={},
            adjudication={},
            dice_results=[],
            skill_checks=[],
            sanity_checks=[],
            resolution={},
            narration=narration,
            options=options,
            state_delta=state_delta,
            story_state=story_state,
            needs_image=False,
            image_scene_type="",
            debug_emit=debug_emit,
        )

    def _commit_state(
        self,
        db: Session,
        session: models.GameSession,
        character: models.Character,
        player_input: str,
        intent: dict[str, Any],
        turn_plan: dict[str, Any],
        plan_validation: dict[str, Any],
        react_trace: list[dict[str, Any]],
        tool_observations: list[dict[str, Any]],
        skill_results: list[dict[str, Any]],
        reflection_report: dict[str, Any],
        final_guardrail_report: dict[str, Any],
        adjudication: dict[str, Any],
        dice_results: list[dict[str, Any]],
        skill_checks: list[dict[str, Any]],
        sanity_checks: list[dict[str, Any]],
        resolution: dict[str, Any],
        narration: str,
        options: list[str],
        state_delta: dict[str, Any],
        story_state: dict[str, Any],
        needs_image: bool,
        image_scene_type: str,
        debug_emit: DebugEmitter | None,
    ) -> dict[str, Any]:
        """唯一集中落库方法（_commit_state = 提交状态/保存存档）。

        【中文名称】提交状态 / 保存存档

        【功能说明】
        这是整个回合流程的最后一步。将本回合的所有结果写入数据库。
        不管是正常回合还是追问回合，最终都通过这个方法落库。

        【落库的 10 个步骤（按顺序）】
        1. 应用理智变化 → 更新 character.san_current
        2. 应用状态增量 → 更新地点、场景、时间、危险等级
        3. 保存元数据 → 意图、裁定、审计记录写入 session.state
        4. 处理线索发现 → 新线索写入 clues 表（自动去重）
        5. 处理物品变化 → 物品增减写入 inventory_items 表
        6. 更新线索计数器 → 用于判断是否给玩家线索提示
        7. 生成会话摘要 → 用 LLM 生成摘要，维护长期记忆
        8. 写 TurnLog → 记录本回合完整信息到 turn_logs 表
        9. 写入向量记忆 → 将回合记忆写入 ChromaDB
        10. 提交数据库事务 → db.commit()

        【参数说明】
        - db: 数据库会话
        - session: 游戏会话对象
        - character: 角色对象
        - player_input: 玩家输入
        - intent: 解析后的意图
        - turn_plan: 回合计划
        - plan_validation: 计划校验结果
        - react_trace: ReAct 执行轨迹
        - tool_observations: Tool 观察结果
        - skill_results: Skill 执行结果
        - reflection_report: Reflection 自检报告
        - final_guardrail_report: 综合守卫报告
        - adjudication: 规则裁定
        - dice_results: 骰点结果
        - skill_checks: 技能检定结果
        - sanity_checks: 理智检定结果
        - resolution: 综合裁定结果
        - narration: 清洗后的安全叙事
        - options: 清洗后的安全选项
        - state_delta: 校验后的状态增量
        - story_state: 剧情状态
        - needs_image: 是否需要配图
        - image_scene_type: 配图场景类型
        - debug_emit: 调试事件发射器

        【返回值】
        - dict: 兼容旧版 KeeperState 的字典，供 API 层使用
        """
        turn_index = len(session.turn_logs) + 1  # 回合序号
        discovered: list[models.Clue] = []  # 本回合发现的线索

        # ===== 1. 应用理智变化 =====
        for san in sanity_checks:
            character.san_current = int(san["san_after"])  # 更新角色当前理智值

        # ===== 2. 应用状态增量 =====
        # 将 state_delta 合并到 session.state，更新地点、场景、时间、危险等级
        session.state = apply_turn_delta(
            story_state, state_delta,
            session.current_location, session.current_scene, session.current_time,
        )
        scene_state = session.state.get("场景", {}) if isinstance(session.state.get("场景"), dict) else {}
        if isinstance(scene_state.get("当前地点"), str) and scene_state["当前地点"]:
            session.current_location = scene_state["当前地点"][:200]  # 更新地点（截断）
        if isinstance(scene_state.get("当前场景"), str) and scene_state["当前场景"]:
            session.current_scene = scene_state["当前场景"][:200]  # 更新场景（截断）
        session.current_time = session.state.get("场景", {}).get("当前时间", session.current_time)  # 更新时间
        session.danger_level = int(session.state.get("剧情", {}).get("敌对势力警觉", session.danger_level))  # 更新危险等级

        # ===== 3. 保存元数据 =====
        # 将本回合的关键数据保存到 session.state，供调试和回溯使用
        session.state = {
            **session.state,
            "last_intent": intent,  # 最后意图
            "last_delta": state_delta,  # 最后状态增量
            "last_audit": {  # 审计记录
                "意图": intent,
                "裁定": adjudication,
                "偏离剧情": resolution.get("偏离剧情", {}),
                "检索": {
                    "剧本片段数": len(session.state.get("last_scenario_context", [])),
                    "结构化实体数": len(session.state.get("last_entity_context", [])),
                    "线索索引数": len(session.state.get("last_clue_context", [])),
                    "会话记忆数": len(session.state.get("last_memory_context", [])),
                    "规则片段数": len(session.state.get("last_rule_context", [])),
                },
                "状态校验": final_guardrail_report.get("validation", {}),
                "防剧透": final_guardrail_report.get("leak", {}),
            },
            "last_options": options,  # 最后选项
            "last_turn_plan": turn_plan,  # 最后回合计划
            "last_react_trace": react_trace,  # 最后 ReAct 轨迹
            "last_tool_observations": tool_observations,  # 最后 Tool 观察
            "last_reflection_report": reflection_report,  # 最后 Reflection 报告
            "last_final_guardrail_report": final_guardrail_report,  # 最后守卫报告
        }

        # ===== 4. 处理线索发现 =====
        # 将 LLM 生成的线索写入数据库（去重：已存在的线索不重复创建）
        for clue_payload in state_delta.get("generated_clues", []):
            if not isinstance(clue_payload, dict):
                continue
            clue_key = safe_key(str(clue_payload.get("clue_key") or clue_payload.get("name") or "clue"))
            existing = db.query(models.Clue).filter(
                models.Clue.session_id == session.id, models.Clue.clue_key == clue_key
            ).one_or_none()
            if existing:
                discovered.append(existing)  # 已存在的线索直接引用
                continue
            clue = models.Clue(
                session_id=session.id,
                clue_key=clue_key,
                name=str(clue_payload.get("name") or clue_key),
                content=str(clue_payload.get("content") or "玩家发现了一条新的线索。"),
                source_location=clue_payload.get("source_location") or session.current_location,
                discovered_turn=turn_index,
                metadata_={"来源": "守秘人代理"},
            )
            db.add(clue)
            discovered.append(clue)

        # ===== 5. 处理物品变化 =====
        inventory_results = apply_inventory_changes(db, session, state_delta.get("inventory_changes", []), turn_index)
        if inventory_results.get("applied") or inventory_results.get("ignored"):
            state_delta["inventory_results"] = inventory_results
            session.state["last_inventory_changes"] = inventory_results

        # ===== 6. 更新线索计数器 =====
        # 用于判断是否应该给玩家提供线索提示
        update_no_clue_counter(session.state, bool(discovered))

        # ===== 7. 生成并应用会话摘要 =====
        # 摘要用于维护会话的长期记忆，避免上下文过长
        summary_state = {
            "player_input": player_input,
            "narration": narration,
            "state_delta": state_delta,
            "story_state": story_state,
        }
        summary = build_turn_summary(session, summary_state, self.llm)
        apply_summary_to_session(session, summary_state, summary)

        # ===== 8. 写 TurnLog =====
        # 记录本回合的完整信息，供调试和回溯使用
        log = models.TurnLog(
            session_id=session.id,
            turn_index=turn_index,
            player_input=player_input,
            intent=intent,
            retrieval={
                "剧本": [],
                "结构化实体": [],
                "线索索引": [],
                "会话记忆": [],
                "规则": [],
                "裁定": adjudication,
                "审计": session.state.get("last_audit", {}),
                "回合计划": turn_plan,
                "计划校验": plan_validation,
                "ReAct轨迹": react_trace,
                "Tool观察": tool_observations,
                "Skill结果": skill_results,
                "Reflection": reflection_report,
                "最终校验": final_guardrail_report,
            },
            dice_results=dice_results,
            keeper_response=narration,
            state_delta=state_delta,
            image_url=None,
            image_metadata={
                "needs_image": needs_image,
                "scene_type": image_scene_type,
                "prompt_raw": "",
                "prompt_optimized": "",
            },
        )
        db.add(log)

        # ===== 9. 写入向量记忆 =====
        # 将本回合的关键信息写入 ChromaDB，供后续回合的 RAG 检索使用
        memory_chunks: list[DocumentChunk] = []
        mem_chunk = build_session_memory_chunk(session.id, turn_index, {  # 回合记忆
            "player_input": player_input,
            "narration": narration,
            "state_delta": state_delta,
            "adjudication": adjudication,
        })
        if mem_chunk:
            memory_chunks.append(mem_chunk)
        summary_chunk = build_summary_memory_chunk(session.id, turn_index, summary)  # 摘要记忆
        if summary_chunk:
            memory_chunks.append(summary_chunk)
        if memory_chunks:
            self.retrieval.upsert_chunks("session_memory_chunks", memory_chunks)

        # ===== 10. 提交数据库事务 =====
        db.commit()
        db.refresh(session)  # 刷新会话对象
        for clue in discovered:
            db.refresh(clue)  # 刷新线索对象

        emit_debug(debug_emit, phase="agent_node", name="commit_state", status="success", message="状态已落库。", metadata={"session_id": session.id, "turn_index": turn_index})

        # 返回兼容旧 KeeperState 的字典，供 API 层使用
        return {
            "db": db,
            "session_id": session.id,
            "player_input": player_input,
            "session": session,
            "character": character,
            "intent": intent,
            "turn_plan": turn_plan,
            "plan_validation": plan_validation,
            "react_trace": react_trace,
            "tool_observations": tool_observations,
            "skill_results": skill_results,
            "reflection_report": reflection_report,
            "final_guardrail_report": final_guardrail_report,
            "adjudication": adjudication,
            "dice_results": dice_results,
            "skill_checks": skill_checks,
            "sanity_checks": sanity_checks,
            "resolution": resolution,
            "narration": narration,
            "options": options,
            "state_delta": state_delta,
            "story_state": story_state,
            "discovered_clues": discovered,
            "needs_clarification": state_delta.get("clarification", False),
            "visible_context": {},
            "keeper_only_context": {},
            "needs_image": needs_image,
            "image_scene_type": image_scene_type,
            "image_url": None,
            "image_prompt_raw": "",
            "image_prompt_optimized": "",
            "image_metadata": {},
        }
