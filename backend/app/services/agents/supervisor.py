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
    """多 Agent 调度器，负责回合流程编排、修复循环控制与最终落库。

    对外接口保持与旧 KeeperAgent 一致，内部通过消息信封在各 Agent 之间传递数据。
    """

    def __init__(self) -> None:
        self.llm = LLMClient()
        self.retrieval = RetrievalService()
        self.context = AgentContext(llm=self.llm, retrieval=self.retrieval)
        self.context_agent = ContextAgent(self.context)
        self.planner_agent = PlannerAgent(self.context)
        self.executor_agent = ExecutorAgent(self.context)
        self.narrator_agent = NarratorAgent(self.context)
        self.guard_agent = GuardAgent(self.context)

    def run_turn(self, db: Session, session_id: str, player_input: str, debug_emit: DebugEmitter | None = None) -> dict[str, Any]:
        """与旧 KeeperAgent.run_turn 保持完全兼容的对外接口。"""
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

        # 若需要澄清，直接构造澄清结果并落库
        if plan["needs_clarification"]:
            emit_debug(debug_emit, phase="agent_node", name="Supervisor", status="success", message="玩家输入模糊，进入澄清分支。")
            return self._clarify_and_commit(
                db, ctx["session"], ctx["character"], player_input, ctx["intent"],
                plan["turn_plan"], ctx["story_state"], debug_emit
            )

        # === Phase 3: 执行计划 ===
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

        # === Phase 4: 生成叙事 ===
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

        # === Phase 5: 校验与 Reflection ===
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
                merged = {**narr_data["state_delta"], **guard_data.get("repair_state_delta", {})}
                validated_delta, _ = validate_state_delta(merged, ctx["story_state"])
                narr_data = {**narr_data, "state_delta": validated_delta}
            elif repair_type in ("ask_clarification", "fail_safe"):
                narr_data["narration"] = guard_data["repair_instruction"] or "这个行动还需要更多明确目标。你可以说明想调查的对象、使用的物品或采取的方式。"
                narr_data["options"] = ["说明具体目标", "换一种调查方式", "回顾已知线索", "自定义行动"]
                narr_data["state_delta"] = {"clarification": True, "time_cost_minutes": 0, "danger_delta": 0}
                # 一旦进入 clarify/fail_safe，不再继续 repair
                break
            elif repair_type == "replan_once":
                # 重新规划一次
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
                # 重新执行（简化：直接 break，避免无限递归）
                break

            # 修复后重新 Guard
            guard_envelope = AgentMessage(
                payload={**guard_envelope["payload"], **narr_data}
            )
            guard_result = self.guard_agent.run(guard_envelope)
            guard_data = guard_result["payload"]

        # === Phase 6: 最终落库 ===
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
        """当计划判定需要澄清时，直接构造澄清问题并落库。"""
        question = turn_plan.get("clarification_question") or intent.get("clarification_question") or "你想具体调查哪里，或以什么方式行动？"
        narration = str(question)
        options = ["检查附近明显可疑之处", "询问同伴的看法", "观察环境", "自定义行动"]
        state_delta = {"clarification": True, "time_cost_minutes": 0, "danger_delta": 0}

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
        """唯一集中落库方法，保持与旧 agent.py commit_state 逻辑一致。"""
        turn_index = len(session.turn_logs) + 1
        discovered: list[models.Clue] = []

        # 应用理智变化
        for san in sanity_checks:
            character.san_current = int(san["san_after"])

        # 应用状态增量
        session.state = apply_turn_delta(
            story_state,
            state_delta,
            session.current_location,
            session.current_scene,
            session.current_time,
        )
        scene_state = session.state.get("场景", {}) if isinstance(session.state.get("场景"), dict) else {}
        if isinstance(scene_state.get("当前地点"), str) and scene_state["当前地点"]:
            session.current_location = scene_state["当前地点"][:200]
        if isinstance(scene_state.get("当前场景"), str) and scene_state["当前场景"]:
            session.current_scene = scene_state["当前场景"][:200]
        session.current_time = session.state.get("场景", {}).get("当前时间", session.current_time)
        session.danger_level = int(session.state.get("剧情", {}).get("敌对势力警觉", session.danger_level))

        # 保存元数据
        session.state = {
            **session.state,
            "last_intent": intent,
            "last_delta": state_delta,
            "last_audit": {
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
            "last_options": options,
            "last_turn_plan": turn_plan,
            "last_react_trace": react_trace,
            "last_tool_observations": tool_observations,
            "last_reflection_report": reflection_report,
            "last_final_guardrail_report": final_guardrail_report,
        }

        # 处理线索
        for clue_payload in state_delta.get("generated_clues", []):
            if not isinstance(clue_payload, dict):
                continue
            clue_key = safe_key(str(clue_payload.get("clue_key") or clue_payload.get("name") or "clue"))
            existing = db.query(models.Clue).filter(
                models.Clue.session_id == session.id, models.Clue.clue_key == clue_key
            ).one_or_none()
            if existing:
                discovered.append(existing)
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

        # 处理物品变化
        inventory_results = apply_inventory_changes(db, session, state_delta.get("inventory_changes", []), turn_index)
        if inventory_results.get("applied") or inventory_results.get("ignored"):
            state_delta["inventory_results"] = inventory_results
            session.state["last_inventory_changes"] = inventory_results

        # 更新线索计数器
        update_no_clue_counter(session.state, bool(discovered))

        # 生成并应用会话摘要
        summary_state = {
            "player_input": player_input,
            "narration": narration,
            "state_delta": state_delta,
            "story_state": story_state,
        }
        summary = build_turn_summary(session, summary_state, self.llm)
        apply_summary_to_session(session, summary_state, summary)

        # 写 TurnLog
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

        # 写入向量记忆
        memory_chunks: list[DocumentChunk] = []
        mem_chunk = build_session_memory_chunk(session.id, turn_index, {
            "player_input": player_input,
            "narration": narration,
            "state_delta": state_delta,
            "adjudication": adjudication,
        })
        if mem_chunk:
            memory_chunks.append(mem_chunk)
        summary_chunk = build_summary_memory_chunk(session.id, turn_index, summary)
        if summary_chunk:
            memory_chunks.append(summary_chunk)
        if memory_chunks:
            self.retrieval.upsert_chunks("session_memory_chunks", memory_chunks)

        db.commit()
        db.refresh(session)
        for clue in discovered:
            db.refresh(clue)

        emit_debug(debug_emit, phase="agent_node", name="commit_state", status="success", message="状态已落库。", metadata={"session_id": session.id, "turn_index": turn_index})

        # 返回兼容旧 KeeperState 的字典
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
