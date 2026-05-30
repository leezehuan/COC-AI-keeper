from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph
from sqlalchemy.orm import Session, selectinload

from app import models
from app.services.chunking import DocumentChunk
from app.services.debug_events import DebugEmitter, emit_debug, detail_tool_observation
from app.services.guardrails import (
    build_audit_record,
    classify_divergence,
    sanitize_options,
    sanitize_player_output,
    validate_state_delta,
)
from app.services.inventory import apply_inventory_changes
from app.services.llm import LLMClient
from app.services.prompt_config import build_intent_prompt, build_keeper_response_prompt, build_reflection_prompt, build_turn_plan_prompt
from app.services.retrieval import RetrievalService
from app.services.rules import adjudicate_action, as_adjudication_dict, execute_rule_tools
from app.services.skills import SKILL_SPECS, choose_skill_name, run_skill
from app.services.story_state import apply_turn_delta, build_turn_delta, ensure_story_state
from app.services.summary import apply_summary_to_session, build_summary_memory_chunk, build_turn_summary
from app.utils import safe_key


# 【阅读顺序 5：LangGraph 守秘人核心】
# 如果你是 LangGraph 初学者，建议按下面顺序阅读：
# 1. KeeperState：理解“图里的共享状态字典”。
# 2. KeeperAgent._build_graph：理解节点如何串成流程图。
# 3. run_turn：理解 API 如何启动一次图执行。
# 4. load_state -> parse_intent -> retrieve_context -> adjudicate -> roll_tools -> resolve_action。
# 5. generate_response -> generate_state_delta -> validate_state_delta_node -> secret_leak_check。
# 6. generate_next_options -> commit_state：理解最终如何落库并返回给前端。
# LangGraph 的核心思想：每个节点都是一个函数，输入 state，补充/修改 state，再交给下一个节点。
class KeeperState(TypedDict, total=False):
    # LangGraph 在各节点之间传递的共享状态，包含数据库对象、检索上下文和本回合产物。
    # 对初学者来说，可以把它看成“本回合的工作台”：每个节点都把自己的结果放到这里。
    db: Session
    session_id: str
    player_input: str
    session: models.GameSession
    character: models.Character
    intent: dict[str, Any]
    scenario_context: list[dict[str, Any]]
    rule_context: list[dict[str, Any]]
    entity_context: list[dict[str, Any]]
    clue_context: list[dict[str, Any]]
    memory_context: list[dict[str, Any]]
    adjudication: dict[str, Any]
    dice_results: list[dict[str, Any]]
    skill_checks: list[dict[str, Any]]
    sanity_checks: list[dict[str, Any]]
    divergence: dict[str, Any]
    resolution: dict[str, Any]
    generated_payload: dict[str, Any]
    narration: str
    options: list[str]
    state_delta: dict[str, Any]
    story_state: dict[str, Any]
    validation_report: dict[str, Any]
    leak_report: dict[str, Any]
    audit: dict[str, Any]
    summary: dict[str, Any]
    discovered_clues: list[models.Clue]
    needs_clarification: bool
    visible_context: dict[str, Any]
    keeper_only_context: dict[str, Any]
    turn_plan: dict[str, Any]
    plan_validation: dict[str, Any]
    react_trace: list[dict[str, Any]]
    tool_observations: list[dict[str, Any]]
    skill_results: list[dict[str, Any]]
    plan_gap: bool
    reflection_report: dict[str, Any]
    repair_attempts: int
    final_guardrail_report: dict[str, Any]
    debug_emit: DebugEmitter
    needs_image: bool
    image_scene_type: str
    image_url: str | None
    image_prompt_raw: str
    image_prompt_optimized: str
    image_metadata: dict[str, Any]


class KeeperAgent:
    def __init__(self) -> None:
        # LLMClient 负责调用大模型；RetrievalService 负责向量检索；graph 是 LangGraph 编译后的执行图。
        self.llm = LLMClient()
        self.retrieval = RetrievalService()
        self.graph = self._build_graph()

    def run_turn(self, db: Session, session_id: str, player_input: str, debug_emit: DebugEmitter | None = None) -> KeeperState:
        # 每次玩家输入都会启动一次完整守秘人回合，并返回最终状态供 API 序列化。
        # 【LangGraph 启动点】API 层只需要传入数据库会话、游戏会话 id 和玩家输入，剩余步骤由图自动执行。
        initial: KeeperState = {"db": db, "session_id": session_id, "player_input": player_input}
        if debug_emit is not None:
            initial["debug_emit"] = debug_emit
        return self.graph.invoke(initial)

    def _build_graph(self):
        # 回合流程：载入状态 -> 理解意图 -> 检索资料 -> 规则裁定 -> 生成叙事 -> 校验并落库。
        # 【LangGraph 结构说明】StateGraph(KeeperState) 表示这张图的每个节点都共享 KeeperState。
        # add_node 注册“节点名称 -> Python 函数”；add_edge 定义节点之间的执行顺序。
        # add_conditional_edges 是条件分支：这里根据意图是否清晰，决定追问玩家还是继续裁定。
        graph = StateGraph(KeeperState)
        graph.add_node("load_state", self._debug_node("load_state", self.load_state))
        graph.add_node("build_visible_context", self._debug_node("build_visible_context", self.build_visible_context))
        graph.add_node("plan_turn", self._debug_node("plan_turn", self.plan_turn))
        graph.add_node("validate_plan", self._debug_node("validate_plan", self.validate_plan))
        graph.add_node("clarify_action", self._debug_node("clarify_action", self.clarify_action))
        graph.add_node("execute_plan_react", self._debug_node("execute_plan_react", self.execute_plan_react))
        graph.add_node("synthesize_resolution", self._debug_node("synthesize_resolution", self.synthesize_resolution))
        graph.add_node("generate_response", self._debug_node("generate_response", self.generate_response))
        graph.add_node("generate_state_delta", self._debug_node("generate_state_delta", self.generate_state_delta))
        graph.add_node("deterministic_guardrails", self._debug_node("deterministic_guardrails", self.deterministic_guardrails))
        graph.add_node("reflection_review", self._debug_node("reflection_review", self.reflection_review))
        graph.add_node("repair_or_replan", self._debug_node("repair_or_replan", self.repair_or_replan))
        graph.add_node("final_guardrails", self._debug_node("final_guardrails", self.final_guardrails))
        graph.add_node("generate_next_options", self._debug_node("generate_next_options", self.generate_next_options))
        graph.add_node("commit_state", self._debug_node("commit_state", self.commit_state))
        graph.set_entry_point("load_state")
        graph.add_edge("load_state", "build_visible_context")
        graph.add_edge("build_visible_context", "plan_turn")
        graph.add_edge("plan_turn", "validate_plan")
        graph.add_conditional_edges("validate_plan", self.route_after_plan, {"clarify": "clarify_action", "continue": "execute_plan_react"})
        graph.add_edge("clarify_action", "commit_state")
        graph.add_edge("execute_plan_react", "synthesize_resolution")
        graph.add_edge("synthesize_resolution", "generate_response")
        graph.add_edge("generate_response", "generate_state_delta")
        graph.add_edge("generate_state_delta", "deterministic_guardrails")
        graph.add_edge("deterministic_guardrails", "reflection_review")
        graph.add_edge("reflection_review", "repair_or_replan")
        graph.add_edge("repair_or_replan", "final_guardrails")
        graph.add_edge("final_guardrails", "generate_next_options")
        graph.add_edge("generate_next_options", "commit_state")
        graph.add_edge("commit_state", END)
        return graph.compile()

    def _debug_node(self, name: str, handler: Callable[[KeeperState], KeeperState]) -> Callable[[KeeperState], KeeperState]:
        def wrapped(state: KeeperState) -> KeeperState:
            emit_debug(state.get("debug_emit"), phase="agent_node", name=name, status="start", message=agent_node_start_message(name))
            try:
                next_state = handler(state)
                msg, meta = agent_node_success_detail(name, next_state)
                emit_debug(next_state.get("debug_emit") or state.get("debug_emit"), phase="agent_node", name=name, status="success", message=msg, metadata=meta or None)
                return next_state
            except Exception as exc:
                emit_debug(state.get("debug_emit"), phase="agent_node", name=name, status="error", message=str(exc)[:500])
                raise

        wrapped.__name__ = f"debug_{name}"
        return wrapped

    def load_state(self, state: KeeperState) -> KeeperState:
        # 先加载会话及其关联数据，保证后续节点不触发额外懒加载或拿到不完整上下文。
        # 【LangGraph 节点 1】读取数据库，把 GameSession、Character、线索、物品等放进 state。
        db = state["db"]
        session = (
            db.query(models.GameSession)
            .options(
                selectinload(models.GameSession.character),
                selectinload(models.GameSession.clues),
                selectinload(models.GameSession.inventory_items),
                selectinload(models.GameSession.flags),
                selectinload(models.GameSession.turn_logs),
            )
            .filter(models.GameSession.id == state["session_id"])
            .one()
        )
        state["session"] = session
        state["character"] = session.character
        state["story_state"] = ensure_story_state(session.state, session.current_location, session.current_scene, session.current_time)
        state["repair_attempts"] = 0
        return state

    def build_visible_context(self, state: KeeperState) -> KeeperState:
        session = state["session"]
        state = self.parse_intent(state)
        visible_context = {
            "current_location": session.current_location,
            "current_scene": session.current_scene,
            "current_time": session.current_time,
            "character_archetype": state["character"].archetype,
            "inventory_text": format_inventory(session.inventory_items),
            "known_clues": [clue.name for clue in session.clues],
            "summary": session.summary,
        }
        state["visible_context"] = visible_context
        state["keeper_only_context"] = {"story_state": state.get("story_state", {})}
        return state

    def plan_turn(self, state: KeeperState) -> KeeperState:
        session = state["session"]
        visible = state.get("visible_context", {})
        fallback = fallback_turn_plan(state)
        prompt = build_turn_plan_prompt(
            current_location=session.current_location,
            current_scene=session.current_scene,
            current_time=session.current_time,
            character_archetype=state["character"].archetype,
            inventory_text=str(visible.get("inventory_text") or ""),
            known_clues="；".join(str(item) for item in visible.get("known_clues", [])),
            summary=str(visible.get("summary") or ""),
            player_input=state["player_input"],
            available_tools=available_tool_names(),
            available_skills=list(SKILL_SPECS.keys()),
        )
        generated = self.llm.chat_json(prompt, fallback=fallback)
        plan = normalize_turn_plan(generated, fallback)
        state["turn_plan"] = plan
        state["intent"] = normalize_plan_intent(state.get("intent", {}), plan)
        state["needs_clarification"] = bool(plan.get("needs_clarification"))
        return state

    def validate_plan(self, state: KeeperState) -> KeeperState:
        plan = dict(state.get("turn_plan", {}))
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
            allowed_skills = [choose_skill_name(str(plan.get("action_type") or state.get("intent", {}).get("action_type") or "调查"))]
            issues.append("补充了默认 Skill。")
        for skill_name in allowed_skills:
            for tool_name in SKILL_SPECS[skill_name].allowed_tools:
                if tool_name not in allowed_tools:
                    allowed_tools.append(tool_name)
        risk_level = clamp_int(to_int(plan.get("risk_level"), 1), 1, 5)
        plan["allowed_tools"] = allowed_tools
        plan["allowed_skills"] = allowed_skills
        plan["risk_level"] = risk_level
        state["turn_plan"] = plan
        state["plan_validation"] = {"valid": True, "issues": issues, "allowed_tools": allowed_tools, "allowed_skills": allowed_skills, "risk_level": risk_level}
        if plan.get("needs_clarification"):
            state["needs_clarification"] = True
        return state

    def route_after_plan(self, state: KeeperState) -> str:
        return "clarify" if state.get("needs_clarification") else "continue"

    def execute_plan_react(self, state: KeeperState) -> KeeperState:
        state = self.retrieve_context(state)
        plan = state.get("turn_plan", {})
        allowed_skills = ensure_list(plan.get("allowed_skills"))
        skill_name = str(allowed_skills[0] if allowed_skills else choose_skill_name(str(plan.get("action_type") or "调查")))
        debug_emit = state.get("debug_emit")
        if skill_name not in SKILL_SPECS:
            emit_debug(debug_emit, phase="skill", name=skill_name, status="warning", message="计划引用了未知 Skill。")
            state["plan_gap"] = True
            state["react_trace"] = [{"step": "plan_gap", "reason": f"未知 Skill：{skill_name}"}]
            state["tool_observations"] = []
            state["skill_results"] = []
            return state
        runtime = {
            "allowed_tools": ensure_list(plan.get("allowed_tools")),
            "retrieval": self.retrieval,
            "default_skill": state.get("intent", {}).get("skill") or infer_skill(state["player_input"]),
            "debug_emit": debug_emit,
        }
        emit_debug(debug_emit, phase="skill", name=skill_name, status="start", message="开始执行 Skill。", metadata={"allowed_tools": ensure_list(plan.get("allowed_tools")), "action_type": plan.get("action_type", "")})
        try:
            result = run_skill(skill_name, state, runtime).as_dict()
        except Exception as exc:
            emit_debug(debug_emit, phase="skill", name=skill_name, status="error", message=str(exc)[:500])
            raise
        observations = [item for item in result.get("observations", []) if isinstance(item, dict)]
        skill_meta: dict[str, Any] = {"used_tools": [item.get("tool") for item in observations], "decision_summary": result.get("result", {}).get("decision_summary", "")}
        emit_debug(debug_emit, phase="skill", name=skill_name, status="success", message=f"Skill 完成，调用 {len(observations)} 个 Tool。", metadata=skill_meta)
        state["skill_results"] = [result]
        state["tool_observations"] = observations
        state["react_trace"] = [
            {
                "step": "run_skill",
                "skill": skill_name,
                "used_tools": [item.get("tool") for item in observations],
                "decision_summary": result.get("result", {}).get("decision_summary", ""),
            }
        ]
        apply_rule_observation_to_state(state, observations)
        state["plan_gap"] = False
        return state

    def synthesize_resolution(self, state: KeeperState) -> KeeperState:
        if not state.get("adjudication"):
            state = self.adjudicate(state)
        if state.get("adjudication", {}).get("needs_roll") and not state.get("skill_checks"):
            state = self.roll_tools(state)
        state = self.resolve_action(state)
        state["resolution"] = {
            **state.get("resolution", {}),
            "回合计划": summarize_turn_plan(state.get("turn_plan", {})),
            "ReAct执行": state.get("react_trace", []),
            "技能结果": state.get("skill_results", []),
        }
        return state

    def parse_intent(self, state: KeeperState) -> KeeperState:
        # LLM 解析失败时使用启发式结果兜底，确保流程始终能继续或提出澄清。
        # 【LangGraph 节点 2】把“我检查灯塔门口”这类自然语言，转成 action_type/target/skill 等结构化字段。
        session = state["session"]
        message = state["player_input"]
        fallback = heuristic_intent(message)
        clarification_context = self._build_clarification_context(session)
        prompt = build_intent_prompt(session.current_location, session.current_scene, message, clarification_context)
        parsed = self.llm.chat_json(prompt, fallback=fallback)
        parsed = {**fallback, **{k: v for k, v in parsed.items() if v is not None}}
        state["intent"] = parsed
        state["needs_clarification"] = bool(parsed.get("needs_clarification"))
        return state

    def _build_clarification_context(self, session: models.GameSession) -> str:
        # 如果上一轮是追问（needs_clarification），将原动作、追问内容纳入本轮意图解析上下文。
        if not session.turn_logs:
            return ""
        latest_log = max(session.turn_logs, key=lambda log: log.turn_index)
        intent = latest_log.intent if isinstance(latest_log.intent, dict) else {}
        if not intent.get("needs_clarification"):
            return ""
        return (
            f"【上一轮是追问回合】\n"
            f"玩家原动作：{latest_log.player_input}\n"
            f"系统追问：{intent.get('clarification_question', '')}\n"
            f"请结合以上内容，将本轮玩家输入视为对追问的回答，推断完整意图。"
        )

    def route_after_intent(self, state: KeeperState) -> str:
        # 【LangGraph 条件分支】返回值必须匹配 _build_graph 里的映射键：clarify 或 continue。
        return "clarify" if state.get("needs_clarification") else "continue"

    def clarify_action(self, state: KeeperState) -> KeeperState:
        # 【LangGraph 节点 3A】如果玩家输入太模糊，不推进剧情，只返回一个澄清问题。
        question = state["intent"].get("clarification_question") or "你想具体调查哪里，或以什么方式行动？"
        state["narration"] = str(question)
        state["options"] = ["检查附近明显可疑之处", "询问同伴的看法", "观察环境", "自定义行动"]
        state["state_delta"] = {"clarification": True, "time_cost_minutes": 0, "danger_delta": 0}
        state["audit"] = build_audit_record(state)
        return state

    def retrieve_context(self, state: KeeperState) -> KeeperState:
        # 用玩家输入、当前位置和意图拼接检索查询，同时拉取剧本、实体、线索、记忆与规则。
        # 【LangGraph 节点 3B】这是 RAG 检索步骤：从向量库找“本回合可能用得上的剧本资料和规则资料”。
        # 初学者注意：检索失败不会让整个回合崩溃，而是写入空列表或错误提示，让后续节点继续执行。
        session = state["session"]
        intent = state["intent"]
        debug_emit = state.get("debug_emit")
        query = " ".join([session.current_location, session.current_scene, state["player_input"], str(intent.get("target", "")), str(intent.get("skill", ""))])
        emit_debug(debug_emit, phase="agent_step", name="retrieve_context", status="start", message="开始检索剧本、规则与会话记忆。", metadata={"query": query})
        try:
            state["scenario_context"] = self.retrieval.query("scenario_chunks", query, n_results=6)
        except Exception as exc:
            state["scenario_context"] = [{"id": "retrieval-error", "document": f"剧本检索暂不可用：{exc}", "metadata": {}, "distance": None}]
        try:
            state["entity_context"] = self.retrieval.query("scenario_entities", query, n_results=4)
        except Exception:
            state["entity_context"] = []
        try:
            state["clue_context"] = self.retrieval.query("clue_index", query, n_results=4)
        except Exception:
            state["clue_context"] = []
        try:
            state["memory_context"] = self.retrieval.query("session_memory_chunks", query, n_results=3, where={"session_id": session.id})
        except Exception:
            state["memory_context"] = []
        try:
            state["rule_context"] = self.retrieval.query("rule_chunks", query, n_results=3)
        except Exception:
            state["rule_context"] = []
        emit_debug(
            debug_emit,
            phase="agent_step",
            name="retrieve_context",
            status="success",
            message=(
                f"检索完成：剧本 {len(state.get('scenario_context', []))}、实体 {len(state.get('entity_context', []))}、"
                f"线索 {len(state.get('clue_context', []))}、记忆 {len(state.get('memory_context', []))}、规则 {len(state.get('rule_context', []))}。"
            ),
            metadata={
                "scenario_context": state.get("scenario_context", []),
                "entity_context": state.get("entity_context", []),
                "clue_context": state.get("clue_context", []),
                "memory_context": state.get("memory_context", []),
                "rule_context": state.get("rule_context", []),
            },
        )
        return state

    def adjudicate(self, state: KeeperState) -> KeeperState:
        # 根据角色技能、场景上下文和推断技能决定本轮是否需要检定及其难度。
        # 【LangGraph 节点 4】规则裁定：决定是否要掷骰、用什么技能、难度和风险是多少。
        intent = state["intent"]
        character = state["character"]
        skill_name = normalize_skill(intent.get("skill") or infer_skill(state["player_input"]))
        adjudication = adjudicate_action(
            state["player_input"],
            intent,
            character.skills,
            character.attributes,
            state.get("scenario_context", []),
            skill_name,
            character.luck,
        )
        state["adjudication"] = as_adjudication_dict(adjudication)
        return state

    def roll_tools(self, state: KeeperState) -> KeeperState:
        # 规则工具统一处理掷骰、技能检定和理智检定，避免 LLM 自行编造结果。
        # 【LangGraph 节点 5】真正的随机结果在工具里生成，LLM 后面只能根据这个结果写叙事。
        debug_emit = state.get("debug_emit")
        emit_debug(debug_emit, phase="tool", name="RuleCheckTool", status="start", message="开始执行规则检定。", metadata={"adjudication": state.get("adjudication", {})})
        try:
            results = execute_rule_tools(state["adjudication"], state["character"].san_current)
        except Exception as exc:
            emit_debug(debug_emit, phase="tool", name="RuleCheckTool", status="error", message=str(exc)[:500])
            raise
        state["dice_results"] = results["dice_results"]
        state["skill_checks"] = results["skill_checks"]
        state["sanity_checks"] = results["sanity_checks"]
        emit_debug(debug_emit, phase="tool", name="RuleCheckTool", status="success", message=f"规则检定完成：技能 {len(state['skill_checks'])} 次，理智 {len(state['sanity_checks'])} 次。", metadata={"dice_results": state["dice_results"], "skill_checks": state["skill_checks"], "sanity_checks": state["sanity_checks"]})
        return state

    def resolve_action(self, state: KeeperState) -> KeeperState:
        # 将规则结果和偏离剧情判断整理成 LLM 可引用的“裁定摘要”。
        # 【LangGraph 节点 6】把掷骰结果、理智结果、偏离剧情程度合并成一段机器可读的结果说明。
        divergence = classify_divergence(state["player_input"], state.get("story_state", {}))
        state["divergence"] = divergence
        state["resolution"] = {
            "技能结果": summarize_skill_outcome(state.get("skill_checks", [])),
            "理智结果": summarize_sanity_outcome(state.get("sanity_checks", [])),
            "偏离剧情": divergence,
            "裁定依据": state.get("adjudication", {}).get("reason", "根据规则工具和当前场景裁定。"),
        }
        return state

    def generate_response(self, state: KeeperState) -> KeeperState:
        # 只把玩家可见信息送入回应提示词，隐藏线索和主持人秘密会在后续再次过滤。
        # 【LangGraph 节点 7】调用 LLM 生成守秘人叙事和下一步选项；这里仍不会直接落库。
        if state.get("needs_clarification"):
            question = state["intent"].get("clarification_question") or "你想具体调查哪里，或以什么方式行动？"
            state["narration"] = question
            state["options"] = ["检查附近明显可疑之处", "询问同伴的看法", "观察环境", "自定义行动"]
            state["state_delta"] = {"clarification": True}
            return state

        fallback = fallback_response(state)
        scenario_text = format_context(state.get("scenario_context", []))
        location_context = filter_player_visible_location_rows(state.get("entity_context", []))
        entity_text = format_context(state.get("entity_context", []))
        location_text = format_location_names(location_context)
        clue_text = format_context(filter_player_visible_rows(state.get("clue_context", [])))
        memory_text = format_context(state.get("memory_context", []))
        rule_text = format_context(state.get("rule_context", []))
        inventory_text = format_inventory(state["session"].inventory_items)
        prompt = build_keeper_response_prompt(
            current_location=state["session"].current_location,
            current_scene=state["session"].current_scene,
            character_archetype=state["character"].archetype,
            hp_current=state["character"].hp_current,
            hp_max=state["character"].hp_max,
            san_current=state["character"].san_current,
            player_input=state["player_input"],
            intent=state["intent"],
            adjudication=state["adjudication"],
            resolution=state.get("resolution", {}),
            skill_checks=state.get("skill_checks", []),
            sanity_checks=state.get("sanity_checks", []),
            inventory_text=inventory_text,
            location_text=location_text,
            scenario_text=scenario_text,
            entity_text=entity_text,
            clue_text=clue_text,
            memory_text=memory_text,
            rule_text=rule_text,
        )
        generated = self.llm.chat_json(prompt, fallback=fallback)
        state["generated_payload"] = generated
        state["narration"] = str(generated.get("narration") or fallback["narration"])
        state["options"] = ensure_options(generated.get("options") or fallback["options"])
        state["needs_image"] = bool(generated.get("needs_image"))
        state["image_scene_type"] = str(generated.get("image_scene_type") or "")
        state["image_url"] = None
        state["image_prompt_raw"] = ""
        state["image_prompt_optimized"] = ""
        state["image_metadata"] = {}
        return state

    def generate_state_delta(self, state: KeeperState) -> KeeperState:
        # 把 LLM 给出的自由格式 state_delta 收敛为项目内部稳定的结构化增量。
        # 【LangGraph 节点 8】把“本回合造成的变化”整理成统一结构，例如地点变化、耗时、危险变化、新线索。
        payload = state.get("generated_payload", {})
        fallback = fallback_response(state)
        generated_delta = payload.get("state_delta") if isinstance(payload.get("state_delta"), dict) else fallback["state_delta"]
        generated_clues = payload.get("discovered_clues", []) if isinstance(payload.get("discovered_clues"), list) else []
        structured_delta = build_turn_delta(
            state["story_state"],
            state["player_input"],
            state["intent"],
            state["adjudication"],
            state.get("skill_checks", []),
            state.get("sanity_checks", []),
            generated_delta,
            generated_clues,
            state["session"].current_location,
            state["session"].current_scene,
            filter_player_visible_location_rows(state.get("entity_context", [])),
        )
        structured_delta["generated_clues"] = generated_clues
        if generated_delta.get("inventory_changes") is not None:
            structured_delta["inventory_changes"] = generated_delta["inventory_changes"]
        state["state_delta"] = structured_delta
        return state

    def validate_state_delta_node(self, state: KeeperState) -> KeeperState:
        # 校验并修正状态增量，防止非法地点、危险值或剧情字段污染会话状态。
        # 【LangGraph 节点 9】不要完全信任 LLM 输出；状态写入数据库前必须经过规则校验。
        validated_delta, report = validate_state_delta(state.get("state_delta", {}), state.get("story_state", {}))
        state["state_delta"] = validated_delta
        state["validation_report"] = report
        return state

    def deterministic_guardrails(self, state: KeeperState) -> KeeperState:
        return self.validate_state_delta_node(state)

    def secret_leak_check(self, state: KeeperState) -> KeeperState:
        # 最后一道玩家可见文本防线：屏蔽尚未发现的线索和可能剧透的选项。
        # 【LangGraph 节点 10】玩家能看到的文字在这里做防剧透过滤，避免直接暴露主持人秘密。
        known_clues = [clue.name for clue in state["session"].clues] + state.get("state_delta", {}).get("generated_clues", [])
        safe_text, text_report = sanitize_player_output(state.get("narration", ""), known_clues)
        safe_options, option_report = sanitize_options(state.get("options", []), known_clues)
        state["narration"] = safe_text
        state["options"] = safe_options
        state["leak_report"] = {"叙事": text_report, "选项": option_report}
        return state

    def reflection_review(self, state: KeeperState) -> KeeperState:
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
        report = self.llm.chat_json(prompt, fallback=fallback)
        if not isinstance(report, dict):
            report = fallback
        state["reflection_report"] = {**fallback, **report}
        return state

    def repair_or_replan(self, state: KeeperState) -> KeeperState:
        report = state.get("reflection_report", {})
        result = str(report.get("result") or "pass")
        if result == "repair_text" and report.get("repair_text"):
            fallback = fallback_response(state)
            scenario_text = format_context(state.get("scenario_context", []))
            location_context = filter_player_visible_location_rows(state.get("entity_context", []))
            entity_text = format_context(state.get("entity_context", []))
            location_text = format_location_names(location_context)
            clue_text = format_context(filter_player_visible_rows(state.get("clue_context", [])))
            memory_text = format_context(state.get("memory_context", []))
            rule_text = format_context(state.get("rule_context", []))
            inventory_text = format_inventory(state["session"].inventory_items)
            prompt = build_keeper_response_prompt(
                current_location=state["session"].current_location,
                current_scene=state["session"].current_scene,
                character_archetype=state["character"].archetype,
                hp_current=state["character"].hp_current,
                hp_max=state["character"].hp_max,
                san_current=state["character"].san_current,
                player_input=state["player_input"],
                intent=state["intent"],
                adjudication=state["adjudication"],
                resolution=state.get("resolution", {}),
                skill_checks=state.get("skill_checks", []),
                sanity_checks=state.get("sanity_checks", []),
                inventory_text=inventory_text,
                location_text=location_text,
                scenario_text=scenario_text,
                entity_text=entity_text,
                clue_text=clue_text,
                memory_text=memory_text,
                rule_text=rule_text,
            )
            repair_instruction = str(report.get("repair_text"))
            prompt.append({"role": "user", "content": f"【叙事修正要求】\n{repair_instruction}\n\n请根据以上修正要求，重新生成守秘人叙事和选项，确保叙事符合玩家语言规范。"})
            generated = self.llm.chat_json(prompt, fallback=fallback)
            state["generated_payload"] = generated
            state["narration"] = str(generated.get("narration") or fallback["narration"])
            state["options"] = ensure_options(generated.get("options") or fallback["options"])
            state["repair_attempts"] = int(state.get("repair_attempts") or 0) + 1
        elif result == "repair_state_delta" and isinstance(report.get("repair_state_delta"), dict):
            merged = {**state.get("state_delta", {}), **report["repair_state_delta"]}
            state["state_delta"], state["validation_report"] = validate_state_delta(merged, state.get("story_state", {}))
            state["repair_attempts"] = int(state.get("repair_attempts") or 0) + 1
        elif result in {"ask_clarification", "fail_safe"} or report.get("ask_clarification") or report.get("fail_safe"):
            state["narration"] = str(report.get("repair_text") or "这个行动还需要更多明确目标。你可以说明想调查的对象、使用的物品或采取的方式。")
            state["options"] = ["说明具体目标", "换一种调查方式", "回顾已知线索", "自定义行动"]
            state["state_delta"] = {"clarification": True, "time_cost_minutes": 0, "danger_delta": 0}
            state["needs_clarification"] = True
            state["repair_attempts"] = int(state.get("repair_attempts") or 0) + 1
        return state

    def final_guardrails(self, state: KeeperState) -> KeeperState:
        state = self.validate_state_delta_node(state)
        state = self.secret_leak_check(state)
        state["final_guardrail_report"] = {"validation": state.get("validation_report", {}), "leak": state.get("leak_report", {})}
        return state

    def generate_next_options(self, state: KeeperState) -> KeeperState:
        # 在 LLM 选项基础上追加引导项，并保证最终始终保留“自定义行动”。
        # 【LangGraph 节点 11】整理前端按钮选项；如果玩家卡住太久，会追加线索回顾提示。
        options = ensure_options(state.get("options", []))
        if state.get("divergence", {}).get("needs_guidance"):
            options = ["寻找现实可行的调查方向", *options]
        if should_offer_clue_hint(state):
            options = [*options, "回顾已知线索并寻找遗漏之处"]
        state["options"] = ensure_options(options)
        state["audit"] = build_audit_record(state)
        return state

    def commit_state(self, state: KeeperState) -> KeeperState:
        # 统一提交本回合副作用：角色状态、剧情状态、线索、物品、日志和向量记忆。
        # 【LangGraph 节点 12】这是唯一集中落库的节点，前面节点主要是在 state 中准备数据。
        # 初学者可重点区分：state 是“本次运行中的临时数据”，db.commit 后才变成数据库里的长期状态。
        db = state["db"]
        session = state["session"]
        character = state["character"]
        turn_index = len(session.turn_logs) + 1
        discovered: list[models.Clue] = []

        for san in state.get("sanity_checks", []):
            # 理智检定结果由规则工具生成，落库时只接受该确定结果。
            character.san_current = int(san["san_after"])

        delta = state.get("state_delta", {})
        session.state = apply_turn_delta(
            state.get("story_state", {}),
            delta,
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
        session.state = {
            **session.state,
            "last_intent": state.get("intent", {}),
            "last_delta": delta,
            "last_audit": state.get("audit", {}),
            "last_options": state.get("options", []),
            "last_turn_plan": state.get("turn_plan", {}),
            "last_react_trace": state.get("react_trace", []),
            "last_tool_observations": state.get("tool_observations", []),
            "last_reflection_report": state.get("reflection_report", {}),
            "last_final_guardrail_report": state.get("final_guardrail_report", {}),
        }

        for clue_payload in delta.get("generated_clues", []):
            # clue_key 用于幂等去重，防止同一线索在重复回合中反复创建。
            if not isinstance(clue_payload, dict):
                continue
            clue_key = safe_key(str(clue_payload.get("clue_key") or clue_payload.get("name") or "clue"))
            existing = db.query(models.Clue).filter(models.Clue.session_id == session.id, models.Clue.clue_key == clue_key).one_or_none()
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

        inventory_results = apply_inventory_changes(db, session, delta.get("inventory_changes", []), turn_index)
        # 物品变更结果写回 state，便于前端展示“获得/使用/移除”摘要。
        if inventory_results.get("applied") or inventory_results.get("ignored"):
            delta["inventory_results"] = inventory_results
            session.state["last_inventory_changes"] = inventory_results
        state["discovered_clues"] = discovered
        update_no_clue_counter(session.state, bool(discovered))
        summary = build_turn_summary(session, state, self.llm)
        apply_summary_to_session(session, state, summary)
        log = models.TurnLog(
            session_id=session.id,
            turn_index=turn_index,
            player_input=state["player_input"],
            intent=state.get("intent", {}),
            retrieval={
                "剧本": state.get("scenario_context", []),
                "结构化实体": state.get("entity_context", []),
                "线索索引": state.get("clue_context", []),
                "会话记忆": state.get("memory_context", []),
                "规则": state.get("rule_context", []),
                "裁定": state.get("adjudication", {}),
                "审计": state.get("audit", {}),
                "回合计划": state.get("turn_plan", {}),
                "计划校验": state.get("plan_validation", {}),
                "ReAct轨迹": state.get("react_trace", []),
                "Tool观察": state.get("tool_observations", []),
                "Skill结果": state.get("skill_results", []),
                "Reflection": state.get("reflection_report", {}),
                "最终校验": state.get("final_guardrail_report", {}),
            },
            dice_results=state.get("dice_results", []),
            keeper_response=state.get("narration", ""),
            state_delta=delta,
            image_url=state.get("image_url"),
            image_metadata={
                "needs_image": state.get("needs_image", False),
                "scene_type": state.get("image_scene_type", ""),
                "prompt_raw": state.get("image_prompt_raw", ""),
                "prompt_optimized": state.get("image_prompt_optimized", ""),
            },
        )
        db.add(log)
        memory_chunks = [chunk for chunk in [build_session_memory_chunk(session.id, turn_index, state), build_summary_memory_chunk(session.id, turn_index, summary)] if chunk is not None]
        if memory_chunks:
            # 会话记忆写入向量库，后续检索可引用玩家已经历过的内容。
            self.retrieval.upsert_chunks("session_memory_chunks", memory_chunks)
        db.commit()
        db.refresh(session)
        for clue in discovered:
            db.refresh(clue)
        return state


AGENT_NODE_LABELS = {
    "load_state": "加载会话状态",
    "build_visible_context": "构建玩家可见上下文",
    "plan_turn": "生成回合计划",
    "validate_plan": "校验计划白名单",
    "clarify_action": "生成澄清问题",
    "execute_plan_react": "执行计划与 Skill",
    "synthesize_resolution": "综合裁定结果",
    "generate_response": "生成守秘人叙事",
    "generate_state_delta": "整理状态增量",
    "deterministic_guardrails": "执行确定性校验",
    "reflection_review": "执行 Reflection 自检",
    "repair_or_replan": "应用修复或兜底",
    "final_guardrails": "执行最终防线",
    "generate_next_options": "生成下一步选项",
    "commit_state": "提交会话状态",
}


def agent_node_start_message(name: str) -> str:
    label = AGENT_NODE_LABELS.get(name, name)
    return f"{label}开始。"


def agent_node_success_detail(name: str, state: KeeperState) -> tuple[str, dict[str, Any]]:
    label = AGENT_NODE_LABELS.get(name, name)
    if name == "load_state":
        session = state.get("session")
        char = state.get("character")
        return f"{label}完成", {"session_id": state.get("session_id"), "location": getattr(session, "current_location", ""), "character": getattr(char, "archetype", "")}
    if name == "build_visible_context":
        intent = state.get("intent", {})
        return f"{label}完成：{intent.get('action_type', '未知行动')}", {"intent": intent}
    if name == "plan_turn":
        plan = state.get("turn_plan", {})
        skills = ensure_list(plan.get("allowed_skills"))
        tools = ensure_list(plan.get("allowed_tools"))
        return f"{label}完成：{plan.get('action_type', '未知行动')}，Skill {len(skills)} 个", {"turn_plan": plan}
    if name == "validate_plan":
        validation = state.get("plan_validation", {})
        return f"{label}完成：Tool {len(ensure_list(validation.get('allowed_tools')))} 个，Skill {len(ensure_list(validation.get('allowed_skills')))} 个", {"plan_validation": validation}
    if name == "clarify_action":
        return f"{label}完成", {"clarification": state.get("turn_plan", {}).get("clarification_question", "")}
    if name == "execute_plan_react":
        observations = state.get("tool_observations", [])
        react_trace = state.get("react_trace", [])
        return f"{label}完成：Tool 观察 {len(observations)} 条", {"react_trace": react_trace, "tool_observations": observations, "skill_results": state.get("skill_results", [])}
    if name == "synthesize_resolution":
        resolution = state.get("resolution", {})
        return f"{label}完成", {"resolution": resolution}
    if name == "generate_response":
        narration = state.get("narration", "")
        return f"{label}完成，叙事 {len(narration)} 字", {"narration_preview": narration[:300]}
    if name == "generate_state_delta":
        delta = state.get("state_delta", {})
        return f"{label}完成", {"state_delta": delta}
    if name == "deterministic_guardrails":
        audit = state.get("audit", {})
        return f"{label}完成", {"audit": audit}
    if name == "reflection_review":
        report = state.get("reflection_report", {})
        return f"{label}完成：{report.get('result', 'pass')}", {"reflection_report": report}
    if name == "repair_or_replan":
        return f"{label}完成", {"repair_attempts": state.get("repair_attempts", 0)}
    if name == "final_guardrails":
        report = state.get("final_guardrail_report", {})
        return f"{label}完成", {"final_guardrail_report": report}
    if name == "generate_next_options":
        options = state.get("options", [])
        return f"{label}完成：选项 {len(options)} 个", {"options": options}
    if name == "commit_state":
        return f"{label}完成", {"session_id": state.get("session_id")}
    return f"{label}完成", {}


def heuristic_intent(message: str) -> dict[str, Any]:
    # 意图解析兜底逻辑：LLM 不可用时仍能粗略识别行动类型、目标和技能。
    target = ""
    for marker in ["检查", "调查", "查看", "观察", "搜索", "询问", "前往", "进入"]:
        if marker in message:
            target = message.split(marker, 1)[-1].strip(" ，。！？")[:80]
            break
    vague = message.strip() in {"看看", "调查", "我看看", "我调查一下", "观察"}
    return {
        "action_type": infer_action_type(message),
        "target": target,
        "skill": infer_skill(message),
        "needs_clarification": vague,
        "clarification_question": "你想具体调查哪个目标？" if vague else "",
        "is_meta": "规则" in message or "怎么" in message,
        "reason": "启发式规则",
    }


def infer_action_type(message: str) -> str:
    if any(word in message for word in ["问", "询问", "交谈", "说服"]):
        return "社交"
    if any(word in message for word in ["去", "前往", "进入", "离开"]):
        return "移动"
    if any(word in message for word in ["攻击", "射击", "打", "逃"]):
        return "战斗"
    return "调查"


def infer_skill(message: str) -> str:
    # 用关键词映射常见 CoC 技能，作为裁定节点的默认技能输入。
    mapping = [
        (["听", "声音"], "聆听"),
        (["脚印", "追踪", "跟踪"], "追踪"),
        (["锁", "撬"], "锁匠"),
        (["修", "发电机", "无线电", "灯"], "电气维修"),
        (["尸体", "血", "伤口"], "医学"),
        (["金币", "价值", "估价"], "估价"),
        (["符号", "神秘", "咒印"], "神秘学"),
        (["书", "信", "日记", "资料"], "图书馆使用"),
        (["说服", "劝"], "说服"),
        (["潜行", "悄悄"], "潜行"),
        (["射击", "开枪"], "射击（手枪）"),
        (["打", "斗殴", "攻击"], "斗殴"),
    ]
    for keywords, skill in mapping:
        if any(keyword in message for keyword in keywords):
            return skill
    return "侦查"


def normalize_skill(skill: str) -> str:
    aliases = {"射击": "射击（手枪）", "驾驶": "驾驶（船）", "科学": "博物学"}
    return aliases.get(skill, skill or "侦查")


def format_context(rows: list[dict[str, Any]]) -> str:
    parts = []
    for row in rows:
        metadata = row.get("metadata") or {}
        title = metadata.get("title") or metadata.get("source_name") or row.get("id")
        parts.append(f"[{title}]\n{row.get('document', '')[:1200]}")
    return "\n\n".join(parts)


def format_inventory(items: list[models.InventoryItem]) -> str:
    if not items:
        return "暂无物品。"
    parts: list[str] = []
    for item in items:
        description = f"，{item.description}" if item.description else ""
        parts.append(f"{item.name} ×{item.quantity}{description}")
    return "；".join(parts[:20])


def filter_player_visible_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # 普通线索上下文不允许把“主持人秘密”直接交给玩家可见叙事。
    visible: list[dict[str, Any]] = []
    for row in rows:
        metadata = row.get("metadata") or {}
        if metadata.get("secret_level") == "主持人秘密":
            continue
        visible.append(row)
    return visible


def filter_player_visible_location_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    visible: list[dict[str, Any]] = []
    for row in rows:
        metadata = row.get("metadata") or {}
        if metadata.get("entity_type") != "地点":
            continue
        if metadata.get("secret_level") == "主持人秘密":
            continue
        visible.append(row)
    return visible


def format_location_names(rows: list[dict[str, Any]]) -> str:
    names: list[str] = []
    for row in rows:
        metadata = row.get("metadata") or {}
        name = str(metadata.get("title") or "").strip()
        if name and name not in names:
            names.append(name)
    return "、".join(names[:12]) if names else "暂无新的可见地点实体。"


def build_session_memory_chunk(session_id: str, turn_index: int, state: KeeperState) -> DocumentChunk | None:
    # 每回合生成一条可检索记忆，让长期会话能回忆玩家行动和守秘人回应。
    narration = state.get("narration", "").strip()
    player_input = state.get("player_input", "").strip()
    if not narration and not player_input:
        return None
    text = (
        f"会话：{session_id}\n"
        f"回合：{turn_index}\n"
        f"玩家行动：{player_input}\n"
        f"守秘人回应：{narration[:800]}\n"
        f"状态变化：{state.get('state_delta', {})}\n"
        f"裁定：{state.get('adjudication', {})}"
    )
    return DocumentChunk(
        id=f"memory:{session_id}:{turn_index}",
        text=text,
        metadata={
            "collection_type": "session_memory",
            "session_id": session_id,
            "turn_index": turn_index,
            "source_name": "会话记忆",
            "title": f"第 {turn_index} 回合记忆",
            "secret_level": "玩家可见",
            "rag_namespace": "session_memory",
            "source_type": "memory",
            "visibility": "player_visible",
            "memory_type": "session_memory",
            "is_rag_data": False,
            "data_source": "session_summary",
            "citation": f"会话记忆 · 第 {turn_index} 回合",
        },
    )


def ensure_options(value: Any) -> list[str]:
    # 规范化下一步选项：去重、截断，并固定追加“自定义行动”。
    if not isinstance(value, list):
        return default_options()
    options: list[str] = []
    seen: set[str] = set()
    for item in value:
        option = normalize_option(item)
        if not option or option in seen or option == "自定义行动":
            continue
        seen.add(option)
        options.append(option)
    if not options:
        return default_options()
    options = options[:5]
    options.append("自定义行动")
    return options


def normalize_option(value: Any) -> str:
    if isinstance(value, dict):
        for key in ["action", "label", "title", "name", "description"]:
            option = str(value.get(key) or "").strip()
            if option:
                return option[:120]
        return ""
    option = str(value).strip()
    if option.startswith("{") and option.endswith("}"):
        extracted = extract_option_from_mapping_text(option)
        if extracted:
            return extracted[:120]
    return option[:120]


def extract_option_from_mapping_text(value: str) -> str:
    for key in ["action", "label", "title", "name", "description"]:
        marker = f"'{key}':"
        if marker not in value:
            marker = f'"{key}":'
        if marker not in value:
            continue
        tail = value.split(marker, 1)[1].strip()
        if not tail:
            continue
        quote = tail[0]
        if quote not in {"'", '"'}:
            return tail.split(",", 1)[0].strip(" }")
        end = tail.find(quote, 1)
        if end > 1:
            return tail[1:end].strip()
    return ""


def default_options() -> list[str]:
    return ["继续搜索附近", "观察周围环境", "询问同伴看法", "检查角色状态", "自定义行动"]


def available_tool_names() -> list[str]:
    return [
        "ContextSearchTool",
        "RuleCheckTool",
        "InventoryLookupTool",
        "SceneAffordanceTool",
        "ClueEligibilityTool",
        "MemoryRecallTool",
    ]


def fallback_turn_plan(state: KeeperState) -> dict[str, Any]:
    intent = state.get("intent", heuristic_intent(state.get("player_input", "")))
    action_type = str(intent.get("action_type") or infer_action_type(state.get("player_input", "")))
    skill_name = choose_skill_name(action_type)
    allowed_tools = list(SKILL_SPECS[skill_name].allowed_tools)
    return {
        "intent": intent,
        "goal": f"处理玩家行动：{state.get('player_input', '')[:120]}",
        "assumptions": [],
        "needs_clarification": bool(intent.get("needs_clarification")),
        "clarification_question": intent.get("clarification_question") or "",
        "action_type": action_type,
        "required_context": ["visible_state", "scenario", "rules"],
        "allowed_tools": allowed_tools,
        "allowed_skills": [skill_name],
        "possible_checks": [intent.get("skill")] if intent.get("skill") else [],
        "risk_level": 1,
        "expected_state_delta": {},
        "success_criteria": "生成玩家可见裁定，并由确定性代码校验状态变化。",
        "fallback": "如果信息不足，则要求玩家澄清或给出非剧透提示。",
    }


def normalize_turn_plan(value: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        value = {}
    plan = {**fallback, **{key: item for key, item in value.items() if item is not None}}
    plan["allowed_tools"] = ensure_list(plan.get("allowed_tools")) or fallback["allowed_tools"]
    plan["allowed_skills"] = ensure_list(plan.get("allowed_skills")) or fallback["allowed_skills"]
    plan["required_context"] = ensure_list(plan.get("required_context"))
    plan["possible_checks"] = ensure_list(plan.get("possible_checks"))
    plan["assumptions"] = ensure_list(plan.get("assumptions"))
    plan["needs_clarification"] = bool(plan.get("needs_clarification"))
    plan["risk_level"] = clamp_int(to_int(plan.get("risk_level"), fallback.get("risk_level", 1)), 1, 5)
    return plan


def normalize_plan_intent(intent: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(intent or {})
    if plan.get("action_type"):
        normalized["action_type"] = str(plan.get("action_type"))
    if plan.get("intent") and isinstance(plan.get("intent"), dict):
        normalized = {**normalized, **{key: value for key, value in plan["intent"].items() if value is not None}}
    normalized["needs_clarification"] = bool(plan.get("needs_clarification"))
    normalized["clarification_question"] = str(plan.get("clarification_question") or normalized.get("clarification_question") or "")
    return normalized


def ensure_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def to_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def clamp_int(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, value))


def apply_rule_observation_to_state(state: KeeperState, observations: list[dict[str, Any]]) -> None:
    for observation in observations:
        if observation.get("tool") != "RuleCheckTool":
            continue
        output = observation.get("output") if isinstance(observation.get("output"), dict) else {}
        if output.get("adjudication"):
            state["adjudication"] = output["adjudication"]
        state["dice_results"] = output.get("dice_results", state.get("dice_results", []))
        state["skill_checks"] = output.get("skill_checks", state.get("skill_checks", []))
        state["sanity_checks"] = output.get("sanity_checks", state.get("sanity_checks", []))


def summarize_turn_plan(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "goal": plan.get("goal", ""),
        "action_type": plan.get("action_type", ""),
        "allowed_tools": plan.get("allowed_tools", []),
        "allowed_skills": plan.get("allowed_skills", []),
        "risk_level": plan.get("risk_level", 1),
    }


def should_offer_clue_hint(state: KeeperState) -> bool:
    # 多回合没有新线索时，主动提供回顾线索的软提示，降低卡关概率。
    generated_clues = state.get("state_delta", {}).get("generated_clues", [])
    if isinstance(generated_clues, list) and generated_clues:
        return False
    memory = state.get("story_state", {}).get("记忆", {})
    previous_count = int(memory.get("连续无新线索回合") or 0) if isinstance(memory, dict) else 0
    return previous_count >= 4


def update_no_clue_counter(session_state: dict[str, Any], has_new_clue: bool) -> None:
    memory = session_state.setdefault("记忆", {})
    if has_new_clue:
        memory["连续无新线索回合"] = 0
        return
    memory["连续无新线索回合"] = int(memory.get("连续无新线索回合") or 0) + 1


def summarize_skill_outcome(checks: list[dict[str, Any]]) -> str:
    if not checks:
        return "没有技能检定。"
    check = checks[-1]
    return f"{check.get('skill')} {check.get('roll')}/{check.get('skill_value')}，{check.get('success_level')}。"


def summarize_sanity_outcome(checks: list[dict[str, Any]]) -> str:
    if not checks:
        return "没有理智检定。"
    check = checks[-1]
    return f"理智损失 {check.get('san_loss')}，当前理智 {check.get('san_after')}。"


def fallback_response(state: KeeperState) -> dict[str, Any]:
    skill_text = ""
    if state.get("skill_checks"):
        check = state["skill_checks"][0]
        skill_text = f"\n\n检定：{check['skill']} {check['roll']}/{check['skill_value']}，结果为 {check['success_level']}。"
    san_text = ""
    if state.get("sanity_checks"):
        san = state["sanity_checks"][0]
        san_text = f"\n理智损失：{san['san_loss']}，当前理智 {san['san_after']}。"
    guidance = ""
    if state.get("divergence", {}).get("needs_guidance"):
        guidance = f"\n\n{state['divergence'].get('guidance')}"
    return {
        "narration": f"你在{state['session'].current_location}继续行动。风雨和黑暗让每个细节都显得不可靠，但你的行动已经推进了调查。{skill_text}{san_text}{guidance}",
        "options": ["继续搜索附近", "观察周围环境", "前往灯塔小屋", "检查角色状态", "自定义行动"],
        "state_delta": {},
        "discovered_clues": [],
    }
