# =============================================================================
# 【NarratorAgent：守秘人叙事生成 Agent】
# =============================================================================
# 这是回合流程的第四个 Agent，相当于"说书人/旁白"。
# 它把 ExecutorAgent 的执行结果变成生动的叙事文字。
#
# 具体做四件事：
#
# 1. 生成守秘人叙事（narration）
#    - 输入：上下文 + 执行结果 + 检定数据
#    - 调用 LLM 生成一段生动的叙事文字
#    - 比如："你小心翼翼地靠近码头，潮湿的木板上隐约可见几道泥泞的脚印..."
#
# 2. 生成玩家选项（options）
#    - 根据当前情况，生成 3-5 个推荐的下一步行动
#    - 比如：["追踪脚印方向", "检查脚印大小", "观察周围环境", "自定义行动"]
#
# 3. 构建状态增量（state_delta）
#    - 把本回合的变化整理成结构化数据
#    - 包括：地点变化、时间流逝、线索发现、物品增减、剧情推进
#
# 4. 追加引导选项
#    - 如果玩家偏离主线，追加引导选项帮助回归
#    - 如果有线索可发现，追加线索回顾选项
#
# 还支持 repair 方法：当 GuardAgent 发现叙事有问题时，重新生成。
# =============================================================================
from __future__ import annotations

from contextlib import contextmanager
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
from app.services.agent_monitor import AgentTraceRecorder
from app.services.debug_events import DebugEmitter, emit_debug
from app.services.prompt_config import build_keeper_response_prompt
from app.services.story_state import build_turn_delta


class NarratorAgent(BaseAgent):
    """守秘人叙事生成 Agent（可以理解为"说书人/旁白"）。

    【中文名称】叙事 Agent / 旁白 Agent

    【功能说明】
    把 ExecutorAgent 的执行结果（骰点、检定、裁定）变成玩家能读懂的
    叙事文字。同时生成推荐的下一步选项和状态变化记录。

    【为什么需要它】
    如果把一次回合比作"拍电影"：
    - ContextAgent = 场记（记录当前场景信息）
    - PlannerAgent = 导演（决定这场戏怎么拍）
    - ExecutorAgent = 摄影师+灯光（实际执行拍摄）
    - NarratorAgent = 剪辑师+旁白（把素材变成观众看到的画面）
    没有 NarratorAgent，玩家看到的只是一堆骰点数据，不是故事。

    【输入（envelope.payload）】
    - visible_context: dict     → 玩家可见上下文
    - resolution: dict          → 综合裁定结果
    - skill_checks: list        → 技能检定结果
    - sanity_checks: list       → 理智检定结果
    - player_input: str         → 玩家输入
    - intent: dict              → 结构化意图
    - adjudication: dict        → 规则裁定
    - scenario_context: list    → 剧本检索结果
    - entity_context: list      → 实体检索结果
    - clue_context: list        → 线索检索结果
    - memory_context: list      → 记忆检索结果
    - rule_context: list        → 规则检索结果
    - session: GameSession      → 游戏会话
    - character: Character      → 角色
    - story_state: dict         → 剧情状态

    【输出（envelope.payload）】
    - narration: str            → 守秘人叙事文本
    - options: list[str]        → 玩家可选行动列表
    - state_delta: dict         → 结构化状态增量
    - generated_payload: dict   → LLM 生成的完整载荷
    - needs_image: bool         → 是否需要生成配图
    - image_scene_type: str     → 配图场景类型
    """

    name = "NarratorAgent"

    def run(self, envelope: AgentMessage) -> AgentMessage:
        """生成守秘人叙事（run = 运行/执行）。

        【中文名称】运行

        【功能说明】
        NarratorAgent 的主入口方法。按顺序执行四步：
        1. 格式化上下文文本（过滤不可见内容）
        2. 调用 LLM 生成叙事和选项
        3. 构建结构化状态增量
        4. 追加引导选项

        【为什么需要过滤不可见内容】
        在格式化上下文时，会调用 filter_player_visible_rows 过滤掉
        标记为 keeper_only 的内容。这确保 LLM 在生成叙事时不会
        不小心把秘密泄露给玩家。

        【执行流程】
        上下文 + 执行结果 → 格式化文本（过滤不可见内容）
          → build_keeper_response_prompt → LLM.chat_json
          → 提取 narration、options
          → build_turn_delta（构建状态增量）
          → 追加引导选项（如果需要）
          → 打包返回 AgentMessage

        【参数说明】
        - envelope: 输入信封，payload 需包含 session、character、resolution 等

        【返回值】
        - AgentMessage: 输出信封，payload 包含叙事文本、选项和状态增量
        """
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
        trace_recorder: AgentTraceRecorder | None = payload.get("trace_recorder")

        with (trace_recorder.step(agent_name=self.name, step_name="run", phase="narrate", input_payload=payload) if trace_recorder else null_trace_step()) as trace_step:
            result = self._run_impl(payload, session, character, player_input, intent, adjudication, resolution, skill_checks, sanity_checks, scenario_context, entity_context, clue_context, memory_context, rule_context, story_state, debug_emit, trace_recorder)
            trace_step["output"] = result
            return result

    def _run_impl(
        self,
        payload: dict[str, Any],
        session: Any,
        character: Any,
        player_input: str,
        intent: dict[str, Any],
        adjudication: dict[str, Any],
        resolution: dict[str, Any],
        skill_checks: list[dict[str, Any]],
        sanity_checks: list[dict[str, Any]],
        scenario_context: list[dict[str, Any]],
        entity_context: list[dict[str, Any]],
        clue_context: list[dict[str, Any]],
        memory_context: list[dict[str, Any]],
        rule_context: list[dict[str, Any]],
        story_state: dict[str, Any],
        debug_emit: DebugEmitter | None,
        trace_recorder: AgentTraceRecorder | None,
    ) -> AgentMessage:
        emit_debug(debug_emit, phase="agent_node", name="NarratorAgent", status="start", message="NarratorAgent 开始生成叙事。")

        # ===== 格式化上下文文本 =====
        # 过滤掉 keeper_only 的内容，确保 LLM 不会在叙事中泄露秘密
        location_context = filter_player_visible_location_rows(entity_context)  # 过滤地点实体
        scenario_text = format_context(scenario_context)  # 剧本文本
        location_text = format_location_names(location_context)  # 地点名称
        entity_text = format_context(entity_context)  # 实体文本
        clue_text = format_context(filter_player_visible_rows(clue_context))  # 可见线索文本
        memory_text = format_context(memory_context)  # 记忆文本
        rule_text = format_context(rule_context)  # 规则文本
        inventory_text = format_inventory(session.inventory_items)  # 物品栏文本

        # 构建回退响应：LLM 失败时使用
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
            with (trace_recorder.step(agent_name=self.name, step_name="generate_narration", phase="agent_step", input_payload={"prompt": prompt, "fallback": fb, "player_input": player_input, "intent": intent, "resolution": resolution}) if trace_recorder else null_trace_step()) as trace_step:
                generated = self.context.llm.chat_json(prompt, fallback=fb)  # LLM 生成叙事
                trace_step["output"] = generated
        except Exception as exc:
            emit_debug(debug_emit, phase="agent_node", name="NarratorAgent", status="error", message=str(exc)[:500])
            generated = fb  # LLM 失败时使用回退

        if not isinstance(generated, dict):
            generated = fb  # 非字典结果使用回退

        narration = str(generated.get("narration") or fb["narration"])  # 叙事文本
        options = ensure_options(generated.get("options") or fb["options"])  # 选项列表
        needs_image = bool(generated.get("needs_image"))  # 是否需要配图
        image_scene_type = str(generated.get("image_scene_type") or "")  # 配图场景类型

        # ===== 构建结构化状态增量 =====
        # state_delta 包含：线索发现、物品变更、剧情推进、位置变化等
        generated_delta = generated.get("state_delta") if isinstance(generated.get("state_delta"), dict) else fb["state_delta"]
        generated_clues = generated.get("discovered_clues", []) if isinstance(generated.get("discovered_clues"), list) else []

        structured_delta = build_turn_delta(
            story_state, player_input, intent, adjudication,
            skill_checks, sanity_checks, generated_delta, generated_clues,
            session.current_location, session.current_scene, location_context,
        )
        structured_delta["generated_clues"] = generated_clues  # LLM 生成的线索列表
        if generated_delta.get("inventory_changes") is not None:
            structured_delta["inventory_changes"] = generated_delta["inventory_changes"]  # 物品变更

        # ===== 追加引导选项 =====
        # 剧情偏离时追加引导选项，帮助玩家回到主线
        if resolution.get("偏离剧情", {}).get("needs_guidance"):
            options = ["寻找现实可行的调查方向", *options]
        # 有线索可发现时追加线索回顾选项
        if should_offer_clue_hint({"story_state": story_state, "state_delta": structured_delta}):
            options = [*options, "回顾已知线索并寻找遗漏之处"]
        options = ensure_options(options)  # 确保至少有一个选项

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
        """修复叙事（repair = 修复/修理）。

        【中文名称】修复

        【功能说明】
        当 GuardAgent 检测到叙事有问题（如剧透、逻辑矛盾）时，
        Supervisor 会调用此方法重新生成叙事。

        【修复机制】
        GuardAgent 在 Reflection 自检中发现问题时，会设置
        repair_type="repair_text" 并提供 repair_instruction（修复指令）。
        Supervisor 将修复指令注入 payload，然后调用此方法。
        当前实现为简化版：直接重新调用 run()。

        【参数说明】
        - envelope: 输入信封，payload 中可能包含 repair_instruction

        【返回值】
        - AgentMessage: 重新生成的叙事信封
        """
        payload = envelope.get("payload", {})
        repair_instruction: str = payload.get("repair_instruction", "")
        trace_recorder: AgentTraceRecorder | None = payload.get("trace_recorder")
        # 复用大部分上下文重新生成
        with (trace_recorder.step(agent_name=self.name, step_name="repair", phase="narrate", input_payload={"repair_instruction": repair_instruction, "payload": payload}) if trace_recorder else null_trace_step()) as trace_step:
            result = self.run(envelope)
            trace_step["output"] = result
            return result


@contextmanager
def null_trace_step():
    state: dict[str, Any] = {}
    yield state
