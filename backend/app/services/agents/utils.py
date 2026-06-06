from __future__ import annotations

from typing import Any

from app import models
from app.services.chunking import DocumentChunk
from app.services.skills import SKILL_SPECS, choose_skill_name


def format_context(rows: list[dict[str, Any]]) -> str:
    """格式化上下文（format_context = 格式化上下文）。将检索结果列表转为LLM可读的文本块。"""
    parts = []
    for row in rows:
        metadata = row.get("metadata") or {}
        title = metadata.get("title") or metadata.get("source_name") or row.get("id")
        parts.append(f"[{title}]\n{row.get('document', '')[:1200]}")
    return "\n\n".join(parts)


def format_inventory(items: list[models.InventoryItem]) -> str:
    """格式化物品栏（format_inventory = 格式化物品栏）。将物品列表转为可读文本。"""
    if not items:
        return "暂无物品。"
    parts: list[str] = []
    for item in items:
        description = f"，{item.description}" if item.description else ""
        parts.append(f"{item.name} ×{item.quantity}{description}")
    return "；".join(parts[:20])


def filter_player_visible_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """过滤玩家可见行（filter_player_visible_rows = 过滤可见行）。移除标记为"主持人秘密"的内容。"""
    visible: list[dict[str, Any]] = []
    for row in rows:
        metadata = row.get("metadata") or {}
        if metadata.get("secret_level") == "主持人秘密":
            continue
        visible.append(row)
    return visible


def filter_player_visible_location_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """过滤玩家可见地点（filter_player_visible_location_rows = 过滤可见地点）。只保留entity_type为"地点"且非秘密的实体。"""
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
    """格式化地点名列表（format_location_names = 格式化地点名）。将地点实体列表转为"、".join的字符串。"""
    names: list[str] = []
    for row in rows:
        metadata = row.get("metadata") or {}
        name = str(metadata.get("title") or "").strip()
        if name and name not in names:
            names.append(name)
    return "、".join(names[:12]) if names else "暂无新的可见地点实体。"


def build_session_memory_chunk(session_id: str, turn_index: int, state: dict[str, Any]) -> DocumentChunk | None:
    """构建会话记忆块（build_session_memory_chunk = 构建记忆块）。将回合数据打包为可存入ChromaDB的DocumentChunk。"""
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
    """确保选项列表有效（ensure_options = 确保选项）。去重、限制5个、追加"自定义行动"。"""
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
    """规范化选项文本（normalize_option = 规范化选项）。从字典或字符串中提取选项文本。"""
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
    """从映射文本提取选项（extract_option_from_mapping_text = 提取选项）。从类似字典的字符串中提取选项值。"""
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
    """默认选项列表（default_options = 默认选项）。当LLM未生成选项时使用的兜底选项。"""
    return ["继续搜索附近", "观察周围环境", "询问同伴看法", "检查角色状态", "自定义行动"]


def available_tool_names() -> list[str]:
    """可用Tool名称列表（available_tool_names = 可用Tool列表）。系统支持的所有Tool白名单。"""
    return [
        "ContextSearchTool",
        "RuleCheckTool",
        "InventoryLookupTool",
        "SceneAffordanceTool",
        "ClueEligibilityTool",
        "MemoryRecallTool",
    ]


def fallback_turn_plan(state: dict[str, Any]) -> dict[str, Any]:
    """回退回合计划（fallback_turn_plan = 回退计划）。LLM生成计划失败时使用的默认计划。"""
    intent = state.get("intent") or heuristic_intent(state.get("player_input", ""))
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
    """规范化回合计划（normalize_turn_plan = 规范化计划）。确保计划所有字段完整，缺失字段用回退值填充。"""
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
    """规范化计划意图（normalize_plan_intent = 规范化意图）。合并意图和计划中的action_type等信息。"""
    normalized = dict(intent or {})
    if plan.get("action_type"):
        normalized["action_type"] = str(plan.get("action_type"))
    if plan.get("intent") and isinstance(plan.get("intent"), dict):
        normalized = {**normalized, **{key: value for key, value in plan["intent"].items() if value is not None}}
    normalized["needs_clarification"] = bool(plan.get("needs_clarification"))
    normalized["clarification_question"] = str(plan.get("clarification_question") or normalized.get("clarification_question") or "")
    return normalized


def ensure_list(value: Any) -> list[Any]:
    """确保为列表（ensure_list = 确保列表）。将非列表值包装为列表，None返回空列表。"""
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def to_int(value: Any, default: int) -> int:
    """安全转整数（to_int = 转为整数）。转换失败时返回默认值。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def clamp_int(value: int, minimum: int, maximum: int) -> int:
    """限制整数范围（clamp_int = 限制范围）。将value限制在[minimum, maximum]区间内。"""
    return max(minimum, min(maximum, value))


def apply_rule_observation_to_state(state: dict[str, Any], observations: list[dict[str, Any]]) -> None:
    """应用规则观察结果到状态（apply_rule_observation_to_state = 应用规则结果）。从Tool观察中提取RuleCheckTool的裁定和检定数据。"""
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
    """摘要回合计划（summarize_turn_plan = 摘要计划）。提取计划的关键字段用于调试。"""
    return {
        "goal": plan.get("goal", ""),
        "action_type": plan.get("action_type", ""),
        "allowed_tools": plan.get("allowed_tools", []),
        "allowed_skills": plan.get("allowed_skills", []),
        "risk_level": plan.get("risk_level", 1),
    }


def should_offer_clue_hint(state: dict[str, Any]) -> bool:
    """判断是否应提示线索（should_offer_clue_hint = 是否提示线索）。连续4回合无新线索时提示。"""
    generated_clues = state.get("state_delta", {}).get("generated_clues", [])
    if isinstance(generated_clues, list) and generated_clues:
        return False
    memory = state.get("story_state", {}).get("记忆", {})
    previous_count = int(memory.get("连续无新线索回合") or 0) if isinstance(memory, dict) else 0
    return previous_count >= 4


def update_no_clue_counter(session_state: dict[str, Any], has_new_clue: bool) -> None:
    """更新无新线索计数器（update_no_clue_counter = 更新线索计数）。有新线索时归零，否则+1。"""
    memory = session_state.setdefault("记忆", {})
    if has_new_clue:
        memory["连续无新线索回合"] = 0
        return
    memory["连续无新线索回合"] = int(memory.get("连续无新线索回合") or 0) + 1


def summarize_skill_outcome(checks: list[dict[str, Any]]) -> str:
    """摘要技能检定结果（summarize_skill_outcome = 摘要技能结果）。生成技能检定的可读摘要。"""
    if not checks:
        return "没有技能检定。"
    check = checks[-1]
    return f"{check.get('skill')} {check.get('roll')}/{check.get('skill_value')}，{check.get('success_level')}。"


def summarize_sanity_outcome(checks: list[dict[str, Any]]) -> str:
    """摘要理智检定结果（summarize_sanity_outcome = 摘要理智结果）。生成理智检定的可读摘要。"""
    if not checks:
        return "没有理智检定。"
    check = checks[-1]
    return f"理智损失 {check.get('san_loss')}，当前理智 {check.get('san_after')}。"


def fallback_response(state: dict[str, Any]) -> dict[str, Any]:
    """回退响应（fallback_response = 回退响应）。LLM生成叙事失败时使用的兜底叙事和选项。"""
    session = state.get("session")
    current_location = getattr(session, "current_location", "未知地点") if session else "未知地点"
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
        "narration": f"你在{current_location}继续行动。风雨和黑暗让每个细节都显得不可靠，但你的行动已经推进了调查。{skill_text}{san_text}{guidance}",
        "options": ["继续搜索附近", "观察周围环境", "前往灯塔小屋", "检查角色状态", "自定义行动"],
        "state_delta": {},
        "discovered_clues": [],
    }


def heuristic_intent(message: str) -> dict[str, Any]:
    """启发式意图解析（heuristic_intent = 启发式意图）。用关键词匹配解析玩家意图，LLM失败时使用。"""
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
    """推断行动类型（infer_action_type = 推断行动类型）。根据关键词判断社交/移动/战斗/调查。"""
    if any(word in message for word in ["问", "询问", "交谈", "说服"]):
        return "社交"
    if any(word in message for word in ["去", "前往", "进入", "离开"]):
        return "移动"
    if any(word in message for word in ["攻击", "射击", "打", "逃"]):
        return "战斗"
    return "调查"


def infer_skill(message: str) -> str:
    """推断使用技能（infer_skill = 推断技能）。根据关键词匹配推断玩家想使用的技能，默认返回"侦查"。"""
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
    """规范化技能名（normalize_skill = 规范化技能）。将简称映射为完整技能名，如"射击"→"射击（手枪）"。"""
    aliases = {"射击": "射击（手枪）", "驾驶": "驾驶（船）", "科学": "博物学"}
    return aliases.get(skill, skill or "侦查")
