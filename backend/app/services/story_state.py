from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any

from app.utils import safe_key


STATE_VERSION = 1


def ensure_story_state(raw_state: dict[str, Any] | None, current_location: str, current_scene: str, current_time: str) -> dict[str, Any]:
    state = deepcopy(raw_state or {})
    state.setdefault("版本", STATE_VERSION)
    state.setdefault("剧情", {})
    state.setdefault("场景", {})
    state.setdefault("记忆", {})
    state.setdefault("秘密", {})
    story = state["剧情"]
    story.setdefault("已访问地点", [])
    story.setdefault("已发现线索", [])
    story.setdefault("未解析线索", [])
    story.setdefault("已触发事件", [])
    story.setdefault("已关闭事件", [])
    story.setdefault("当前可前往地点", [current_location])
    story.setdefault("当前NPC状态", {})
    story.setdefault("NPC态度", {})
    story.setdefault("敌对势力警觉", 1)
    story.setdefault("时间压力", "普通")
    story.setdefault("仪式进度", 0)
    story.setdefault("剧情flag", {})
    story.setdefault("结局倾向", "未定")
    scene = state["场景"]
    scene.setdefault("当前地点", current_location)
    scene.setdefault("当前场景", current_scene)
    scene.setdefault("当前时间", current_time)
    scene.setdefault("房间内对象", [])
    scene.setdefault("已调查对象", [])
    scene.setdefault("未调查对象", [])
    scene.setdefault("可见异常", [])
    scene.setdefault("隐藏线索", [])
    scene.setdefault("当前NPC", [])
    scene.setdefault("当前危险", [])
    scene.setdefault("光照情况", "未知")
    scene.setdefault("门窗状态", "未知")
    scene.setdefault("声音和气味", [])
    scene.setdefault("玩家已做动作", [])
    memory = state["记忆"]
    memory.setdefault("最近行动", [])
    memory.setdefault("当前场景摘要", "")
    memory.setdefault("重要裁定记录", [])
    state["秘密"].setdefault("已屏蔽条目", [])
    story["已访问地点"] = unique_locations(story.get("已访问地点", []))
    story["当前可前往地点"] = unique_locations(story.get("当前可前往地点", []))
    add_unique(story["已访问地点"], normalize_location_name(current_location))
    sync_available_locations(story["当前可前往地点"], current_location)
    return state


def build_turn_delta(
    story_state: dict[str, Any],
    player_input: str,
    intent: dict[str, Any],
    adjudication: dict[str, Any],
    skill_checks: list[dict[str, Any]],
    sanity_checks: list[dict[str, Any]],
    generated_delta: dict[str, Any],
    generated_clues: list[dict[str, Any]],
    current_location: str,
    current_scene: str,
    location_context: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    target = str(intent.get("target") or "").strip()
    action_type = str(intent.get("action_type") or "调查")
    time_cost = int(adjudication.get("time_cost_minutes") or 0)
    danger_delta = infer_danger_delta(action_type, skill_checks, sanity_checks, adjudication)
    target_location = extract_location_delta(generated_delta)
    target_scene = extract_scene_delta(generated_delta)
    if target_location and not target_scene and location_dedupe_key(target_location) != location_dedupe_key(current_location):
        target_scene = target_location
    delta: dict[str, Any] = {
        "location": target_location,
        "scene": target_scene,
        "time_cost_minutes": time_cost,
        "danger_delta": danger_delta,
        "investigated_target": target,
        "action_type": action_type,
        "story_updates": {
            "visited_location": target_location or current_location,
            "available_locations": infer_available_locations(player_input, generated_delta, location_context),
            "triggered_events": infer_triggered_events(player_input, generated_clues),
            "flags": build_flags(player_input, intent, skill_checks, sanity_checks),
        },
        "scene_updates": {
            "investigated_object": target,
            "action_record": summarize_action(player_input, target, skill_checks, sanity_checks),
        },
        "memory_updates": {
            "recent_action": summarize_action(player_input, target, skill_checks, sanity_checks),
            "adjudication_record": {
                "技能": adjudication.get("skill"),
                "难度": adjudication.get("difficulty"),
                "耗时分钟": time_cost,
                "风险等级": adjudication.get("risk_level"),
            },
        },
    }
    if not delta["location"]:
        delta.pop("location")
    if not delta["scene"]:
        delta.pop("scene")
    return delta


def extract_location_delta(generated_delta: dict[str, Any]) -> str:
    candidates = [
        generated_delta.get("location"),
        generated_delta.get("current_location"),
        generated_delta.get("当前位置"),
        generated_delta.get("当前地点"),
        generated_delta.get("地点"),
    ]
    for key in ["scene_updates", "story_updates", "location_updates"]:
        nested = generated_delta.get(key)
        if isinstance(nested, dict):
            candidates.extend([
                nested.get("location"),
                nested.get("current_location"),
                nested.get("当前位置"),
                nested.get("当前地点"),
                nested.get("地点"),
                nested.get("visited_location"),
            ])
    for candidate in candidates:
        normalized = normalize_location_name(candidate)
        if normalized:
            return normalized
    return ""


def extract_scene_delta(generated_delta: dict[str, Any]) -> str:
    candidates = [
        generated_delta.get("scene"),
        generated_delta.get("current_scene"),
        generated_delta.get("当前场景"),
        generated_delta.get("场景"),
    ]
    for key in ["scene_updates", "story_updates"]:
        nested = generated_delta.get(key)
        if isinstance(nested, dict):
            candidates.extend([
                nested.get("scene"),
                nested.get("current_scene"),
                nested.get("当前场景"),
                nested.get("场景"),
            ])
    for candidate in candidates:
        normalized = normalize_scene_name(candidate)
        if normalized:
            return normalized
    return ""


def apply_turn_delta(story_state: dict[str, Any], delta: dict[str, Any], current_location: str, current_scene: str, current_time: str) -> dict[str, Any]:
    state = ensure_story_state(story_state, current_location, current_scene, current_time)
    story = state["剧情"]
    scene = state["场景"]
    memory = state["记忆"]
    target_location = normalize_location_name(delta.get("location") or current_location) or current_location
    previous_location = normalize_location_name(current_location) or current_location
    location_changed = location_dedupe_key(target_location) != location_dedupe_key(previous_location)
    target_scene = normalize_scene_name(delta.get("scene")) or (target_location if location_changed else normalize_scene_name(current_scene) or current_scene)
    add_unique(story["已访问地点"], target_location)
    sync_available_locations(story["当前可前往地点"], target_location)
    for location in delta.get("story_updates", {}).get("available_locations", []):
        sync_available_locations(story["当前可前往地点"], location)
    for event in delta.get("story_updates", {}).get("triggered_events", []):
        add_unique(story["已触发事件"], str(event))
    story["剧情flag"].update(delta.get("story_updates", {}).get("flags", {}))
    story["敌对势力警觉"] = clamp_int(int(story.get("敌对势力警觉", 1)) + max(0, int(delta.get("danger_delta") or 0)), 1, 5)
    scene["当前地点"] = target_location
    scene["当前场景"] = target_scene
    scene["当前时间"] = advance_time(str(scene.get("当前时间") or current_time), int(delta.get("time_cost_minutes") or 0))
    investigated = str(delta.get("scene_updates", {}).get("investigated_object") or "").strip()
    if investigated:
        add_unique(scene["已调查对象"], investigated)
        remove_value(scene["未调查对象"], investigated)
    action_record = str(delta.get("scene_updates", {}).get("action_record") or "").strip()
    if action_record:
        append_limited(scene["玩家已做动作"], action_record, 30)
        append_limited(memory["最近行动"], action_record, 10)
    adjudication_record = delta.get("memory_updates", {}).get("adjudication_record")
    if isinstance(adjudication_record, dict):
        append_limited(memory["重要裁定记录"], adjudication_record, 20)
    return state


def infer_available_locations(player_input: str, generated_delta: dict[str, Any], location_context: list[dict[str, Any]] | None = None) -> list[str]:
    locations: list[str] = []
    append_locations(locations, generated_delta.get("available_locations"))
    story_updates = generated_delta.get("story_updates")
    if isinstance(story_updates, dict):
        append_locations(locations, story_updates.get("available_locations"))
        append_locations(locations, story_updates.get("当前可前往地点"))
        append_locations(locations, story_updates.get("可前往地点"))
    for row in location_context or []:
        location = location_from_context_row(row)
        if location:
            locations.append(location)
    for candidate in ["灯塔小屋", "北岸码头", "灯塔", "宿舍", "书房", "厨房", "灯塔服务室"]:
        if candidate in player_input:
            locations.append(candidate)
    return unique_locations(locations)


def append_locations(locations: list[str], value: Any) -> None:
    if isinstance(value, list):
        locations.extend(normalized for item in value if (normalized := normalize_location_name(item)))
        return
    normalized = normalize_location_name(value)
    if normalized:
        locations.append(normalized)


def location_from_context_row(row: dict[str, Any]) -> str:
    metadata = row.get("metadata") or {}
    if metadata.get("entity_type") != "地点":
        return ""
    if metadata.get("secret_level") == "主持人秘密":
        return ""
    return str(metadata.get("title") or "").strip()


def unique_locations(locations: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for location in locations:
        value = normalize_location_name(location)
        key = location_dedupe_key(value)
        if value and key not in seen:
            seen.add(key)
            unique.append(value[:120])
    return unique[:12]


def normalize_location_name(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple, set)):
        return ""
    text = str(value).strip(" \t\r\n，。；;:：")
    if not text or text.lower() in {"none", "null", "undefined", "nan"}:
        return ""
    for prefix in ["起点", "当前位置", "当前地点", "地点", "可前往地点"]:
        marker = f"{prefix}："
        if text.startswith(marker):
            text = text.removeprefix(marker).strip(" \t\r\n，。；;:：")
        marker = f"{prefix}:"
        if text.startswith(marker):
            text = text.removeprefix(marker).strip(" \t\r\n，。；;:：")
    return text


def normalize_scene_name(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple, set)):
        return ""
    text = str(value).strip(" \t\r\n，。；;:：")
    if not text or text.lower() in {"none", "null", "undefined", "nan"}:
        return ""
    for prefix in ["当前场景", "场景"]:
        marker = f"{prefix}："
        if text.startswith(marker):
            text = text.removeprefix(marker).strip(" \t\r\n，。；;:：")
        marker = f"{prefix}:"
        if text.startswith(marker):
            text = text.removeprefix(marker).strip(" \t\r\n，。；;:：")
    return text


def location_dedupe_key(value: str) -> str:
    key = normalize_location_name(value)
    for prefix in ["航标岛", "岛上", "岛"]:
        if key.startswith(prefix) and len(key) > len(prefix) + 1:
            key = key.removeprefix(prefix).strip(" 的之-—")
    return key


def infer_triggered_events(player_input: str, generated_clues: list[dict[str, Any]]) -> list[str]:
    events: list[str] = []
    if generated_clues:
        events.append("发现线索")
    if any(word in player_input for word in ["攻击", "开枪", "逃跑", "怪物"]):
        events.append("危险行动")
    return events


def build_flags(player_input: str, intent: dict[str, Any], skill_checks: list[dict[str, Any]], sanity_checks: list[dict[str, Any]]) -> dict[str, Any]:
    flags: dict[str, Any] = {}
    target = str(intent.get("target") or "").strip()
    if target:
        flags[f"已尝试_{safe_key(target)}"] = True
    if skill_checks:
        flags["最近技能检定"] = skill_checks[-1]
    if sanity_checks:
        flags["最近理智检定"] = sanity_checks[-1]
    if any(word in player_input for word in ["灯塔", "灯"]):
        flags["关注灯塔"] = True
    return flags


def infer_danger_delta(action_type: str, skill_checks: list[dict[str, Any]], sanity_checks: list[dict[str, Any]], adjudication: dict[str, Any]) -> int:
    delta = 0
    if action_type == "战斗":
        delta += 1
    if skill_checks and not skill_checks[-1].get("success"):
        delta += 1
    if sanity_checks and int(sanity_checks[-1].get("san_loss") or 0) > 0:
        delta += 1
    if int(adjudication.get("risk_level") or 1) >= 4:
        delta += 1
    return min(delta, 2)


def summarize_action(player_input: str, target: str, skill_checks: list[dict[str, Any]], sanity_checks: list[dict[str, Any]]) -> str:
    parts = [player_input[:120]]
    if target:
        parts.append(f"目标：{target}")
    if skill_checks:
        check = skill_checks[-1]
        parts.append(f"{check.get('skill')} {check.get('roll')}/{check.get('skill_value')} {check.get('success_level')}")
    if sanity_checks:
        parts.append(f"理智损失 {sanity_checks[-1].get('san_loss')}")
    return "；".join(parts)


def advance_time(value: str, minutes: int) -> str:
    if minutes <= 0:
        return value
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M")
        return (parsed + timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return value


def add_unique(items: list[Any], value: Any) -> None:
    if value and value not in items:
        items.append(value)


def remove_value(items: list[Any], value: Any) -> None:
    while value in items:
        items.remove(value)


def append_limited(items: list[Any], value: Any, limit: int) -> None:
    items.append(value)
    del items[:-limit]


def sync_available_locations(items: list[Any], location: str) -> None:
    normalized = normalize_location_name(location)
    if not normalized:
        return
    key = location_dedupe_key(normalized)
    for item in items:
        if location_dedupe_key(str(item)) == key:
            return
    items.append(normalized)


def clamp_int(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, value))
