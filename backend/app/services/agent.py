from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, StateGraph
from sqlalchemy.orm import Session, selectinload

from app import models
from app.services.chunking import DocumentChunk
from app.services.guardrails import (
    build_audit_record,
    classify_divergence,
    sanitize_options,
    sanitize_player_output,
    validate_state_delta,
)
from app.services.inventory import apply_inventory_changes
from app.services.llm import LLMClient
from app.services.prompt_config import build_intent_prompt, build_keeper_response_prompt
from app.services.retrieval import RetrievalService
from app.services.rules import adjudicate_action, as_adjudication_dict, execute_rule_tools
from app.services.story_state import apply_turn_delta, build_turn_delta, ensure_story_state
from app.services.summary import apply_summary_to_session, build_summary_memory_chunk, build_turn_summary
from app.utils import safe_key


class KeeperState(TypedDict, total=False):
    # LangGraph 在各节点之间传递的共享状态，包含数据库对象、检索上下文和本回合产物。
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


class KeeperAgent:
    def __init__(self) -> None:
        self.llm = LLMClient()
        self.retrieval = RetrievalService()
        self.graph = self._build_graph()

    def run_turn(self, db: Session, session_id: str, player_input: str) -> KeeperState:
        # 每次玩家输入都会启动一次完整守秘人回合，并返回最终状态供 API 序列化。
        initial: KeeperState = {"db": db, "session_id": session_id, "player_input": player_input}
        return self.graph.invoke(initial)

    def _build_graph(self):
        # 回合流程：载入状态 -> 理解意图 -> 检索资料 -> 规则裁定 -> 生成叙事 -> 校验并落库。
        graph = StateGraph(KeeperState)
        graph.add_node("load_state", self.load_state)
        graph.add_node("parse_intent", self.parse_intent)
        graph.add_node("clarify_action", self.clarify_action)
        graph.add_node("retrieve_context", self.retrieve_context)
        graph.add_node("adjudicate", self.adjudicate)
        graph.add_node("roll_tools", self.roll_tools)
        graph.add_node("resolve_action", self.resolve_action)
        graph.add_node("generate_response", self.generate_response)
        graph.add_node("generate_state_delta", self.generate_state_delta)
        graph.add_node("validate_state_delta", self.validate_state_delta_node)
        graph.add_node("secret_leak_check", self.secret_leak_check)
        graph.add_node("generate_next_options", self.generate_next_options)
        graph.add_node("commit_state", self.commit_state)
        graph.set_entry_point("load_state")
        graph.add_edge("load_state", "parse_intent")
        graph.add_conditional_edges("parse_intent", self.route_after_intent, {"clarify": "clarify_action", "continue": "retrieve_context"})
        graph.add_edge("clarify_action", "commit_state")
        graph.add_edge("retrieve_context", "adjudicate")
        graph.add_edge("adjudicate", "roll_tools")
        graph.add_edge("roll_tools", "resolve_action")
        graph.add_edge("resolve_action", "generate_response")
        graph.add_edge("generate_response", "generate_state_delta")
        graph.add_edge("generate_state_delta", "validate_state_delta")
        graph.add_edge("validate_state_delta", "secret_leak_check")
        graph.add_edge("secret_leak_check", "generate_next_options")
        graph.add_edge("generate_next_options", "commit_state")
        graph.add_edge("commit_state", END)
        return graph.compile()

    def load_state(self, state: KeeperState) -> KeeperState:
        # 先加载会话及其关联数据，保证后续节点不触发额外懒加载或拿到不完整上下文。
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
        return state

    def parse_intent(self, state: KeeperState) -> KeeperState:
        # LLM 解析失败时使用启发式结果兜底，确保流程始终能继续或提出澄清。
        session = state["session"]
        message = state["player_input"]
        fallback = heuristic_intent(message)
        prompt = build_intent_prompt(session.current_location, session.current_scene, message)
        parsed = self.llm.chat_json(prompt, fallback=fallback)
        parsed = {**fallback, **{k: v for k, v in parsed.items() if v is not None}}
        state["intent"] = parsed
        state["needs_clarification"] = bool(parsed.get("needs_clarification"))
        return state

    def route_after_intent(self, state: KeeperState) -> str:
        return "clarify" if state.get("needs_clarification") else "continue"

    def clarify_action(self, state: KeeperState) -> KeeperState:
        question = state["intent"].get("clarification_question") or "你想具体调查哪里，或以什么方式行动？"
        state["narration"] = str(question)
        state["options"] = ["检查附近明显可疑之处", "询问同伴的看法", "观察环境", "自定义行动"]
        state["state_delta"] = {"clarification": True, "time_cost_minutes": 0, "danger_delta": 0}
        state["audit"] = build_audit_record(state)
        return state

    def retrieve_context(self, state: KeeperState) -> KeeperState:
        # 用玩家输入、当前位置和意图拼接检索查询，同时拉取剧本、实体、线索、记忆与规则。
        session = state["session"]
        intent = state["intent"]
        query = " ".join([session.current_location, session.current_scene, state["player_input"], str(intent.get("target", "")), str(intent.get("skill", ""))])
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
        return state

    def adjudicate(self, state: KeeperState) -> KeeperState:
        # 根据角色技能、场景上下文和推断技能决定本轮是否需要检定及其难度。
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
        results = execute_rule_tools(state["adjudication"], state["character"].san_current)
        state["dice_results"] = results["dice_results"]
        state["skill_checks"] = results["skill_checks"]
        state["sanity_checks"] = results["sanity_checks"]
        return state

    def resolve_action(self, state: KeeperState) -> KeeperState:
        # 将规则结果和偏离剧情判断整理成 LLM 可引用的“裁定摘要”。
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
        return state

    def generate_state_delta(self, state: KeeperState) -> KeeperState:
        # 把 LLM 给出的自由格式 state_delta 收敛为项目内部稳定的结构化增量。
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
        validated_delta, report = validate_state_delta(state.get("state_delta", {}), state.get("story_state", {}))
        state["state_delta"] = validated_delta
        state["validation_report"] = report
        return state

    def secret_leak_check(self, state: KeeperState) -> KeeperState:
        # 最后一道玩家可见文本防线：屏蔽尚未发现的线索和可能剧透的选项。
        known_clues = [clue.name for clue in state["session"].clues] + state.get("state_delta", {}).get("generated_clues", [])
        safe_text, text_report = sanitize_player_output(state.get("narration", ""), known_clues)
        safe_options, option_report = sanitize_options(state.get("options", []), known_clues)
        state["narration"] = safe_text
        state["options"] = safe_options
        state["leak_report"] = {"叙事": text_report, "选项": option_report}
        return state

    def generate_next_options(self, state: KeeperState) -> KeeperState:
        # 在 LLM 选项基础上追加引导项，并保证最终始终保留“自定义行动”。
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
            },
            dice_results=state.get("dice_results", []),
            keeper_response=state.get("narration", ""),
            state_delta=delta,
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
