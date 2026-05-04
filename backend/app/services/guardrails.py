from __future__ import annotations

from typing import Any

from app.services.inventory import normalize_inventory_changes

SECRET_TERMS = ["达贡", "邪教", "深潜者", "幼徒", "混种", "幕后", "主持人秘密", "最终真相", "结局条件", "仪式真相"]
SEVERE_DIVERGENCE_TERMS = ["离开航标岛", "回家", "报警", "炸掉", "烧掉灯塔", "跳海", "自杀", "杀死所有人"]
MEDIUM_DIVERGENCE_TERMS = ["造船", "无线电求救", "独自逃走", "不调查", "等待救援"]
ALLOWED_DELTA_KEYS = {"location", "scene", "time_cost_minutes", "danger_delta", "investigated_target", "action_type", "story_updates", "scene_updates", "memory_updates", "generated_clues", "inventory_changes", "inventory_results"}


def classify_divergence(player_input: str, story_state: dict[str, Any]) -> dict[str, Any]:
    if any(term in player_input for term in SEVERE_DIVERGENCE_TERMS):
        return {"level": "严重", "needs_guidance": True, "guidance": "玩家行动会明显脱离当前剧本约束，应以环境阻碍、资源不足或危险迫近的方式引导回调查。"}
    if any(term in player_input for term in MEDIUM_DIVERGENCE_TERMS):
        return {"level": "中度", "needs_guidance": True, "guidance": "玩家行动偏离主要调查线，应允许尝试，但给出代价、时间压力或新的调查入口。"}
    return {"level": "轻微", "needs_guidance": False, "guidance": "行动仍在当前调查范围内。"}


def validate_state_delta(delta: dict[str, Any], story_state: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    issues: list[str] = []
    validated = {key: value for key, value in delta.items() if key in ALLOWED_DELTA_KEYS}
    removed = sorted(set(delta.keys()) - set(validated.keys()))
    if removed:
        issues.append(f"移除了未允许的状态字段：{', '.join(removed)}")
    validated["time_cost_minutes"] = clamp_int(to_int(validated.get("time_cost_minutes"), 0), 0, 120)
    validated["danger_delta"] = clamp_int(to_int(validated.get("danger_delta"), 0), 0, 2)
    validated["story_updates"] = normalize_mapping(validated.get("story_updates"))
    validated["scene_updates"] = normalize_mapping(validated.get("scene_updates"))
    validated["memory_updates"] = normalize_mapping(validated.get("memory_updates"))
    validated["generated_clues"] = normalize_clues(validated.get("generated_clues"), issues)
    validated["inventory_changes"] = normalize_inventory_changes(validated.get("inventory_changes"), issues)
    if "inventory_results" in validated:
        validated.pop("inventory_results")
        issues.append("移除了不允许由模型直接提交的物品结果。")
    report = {"有效": not issues, "问题": issues, "字段": sorted(validated.keys())}
    return validated, report


def sanitize_player_output(text: str, discovered_clues: list[Any]) -> tuple[str, dict[str, Any]]:
    discovered_text = "\n".join(str(item) for item in discovered_clues)
    blocked: list[str] = []
    sanitized = text
    for term in SECRET_TERMS:
        if term in sanitized and term not in discovered_text:
            sanitized = sanitized.replace(term, "尚未明朗的阴影")
            blocked.append(term)
    if blocked:
        sanitized = f"{sanitized}\n\n你意识到有些判断还为时过早，只能先依据眼前证据继续调查。"
    return sanitized, {"通过": not blocked, "屏蔽词": blocked}


def sanitize_options(options: list[str], discovered_clues: list[Any]) -> tuple[list[str], dict[str, Any]]:
    safe_options: list[str] = []
    blocked: list[str] = []
    discovered_text = "\n".join(str(item) for item in discovered_clues)
    for option in options:
        if any(term in option and term not in discovered_text for term in SECRET_TERMS):
            blocked.append(option)
            continue
        safe_options.append(option)
    if "自定义行动" not in safe_options:
        safe_options.append("自定义行动")
    return safe_options[:6], {"通过": not blocked, "已移除选项": blocked}


def build_audit_record(state: dict[str, Any], validation_report: dict[str, Any] | None = None, leak_report: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "意图": state.get("intent", {}),
        "裁定": state.get("adjudication", {}),
        "偏离剧情": state.get("divergence", {}),
        "检索": {
            "剧本片段数": len(state.get("scenario_context", [])),
            "结构化实体数": len(state.get("entity_context", [])),
            "线索索引数": len(state.get("clue_context", [])),
            "会话记忆数": len(state.get("memory_context", [])),
            "规则片段数": len(state.get("rule_context", [])),
        },
        "状态校验": validation_report or state.get("validation_report", {}),
        "防剧透": leak_report or state.get("leak_report", {}),
    }


def normalize_clues(value: Any, issues: list[str]) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    clues: list[dict[str, Any]] = []
    for item in value[:10]:
        if not isinstance(item, dict):
            issues.append("忽略了格式不正确的线索。")
            continue
        name = str(item.get("name") or item.get("clue_key") or "线索").strip()[:120]
        content = str(item.get("content") or "玩家发现了一条新的线索。").strip()[:1200]
        clues.append({
            "clue_key": str(item.get("clue_key") or name).strip()[:160],
            "name": name,
            "content": content,
            "source_location": str(item.get("source_location") or "").strip()[:160],
        })
    return clues


def normalize_mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def to_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def clamp_int(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, value))
