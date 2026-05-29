from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

DebugEmitter = Callable[[dict[str, Any]], None]


def emit_debug(
    debug_emit: DebugEmitter | None,
    *,
    phase: str,
    name: str,
    status: str,
    message: str = "",
    metadata: dict[str, Any] | None = None,
) -> None:
    if debug_emit is None:
        return
    payload: dict[str, Any] = {
        "phase": phase,
        "name": name,
        "status": status,
        "message": message,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if metadata:
        payload["metadata"] = sanitize_metadata(metadata)
    try:
        debug_emit(payload)
    except Exception:
        return


def sanitize_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in metadata.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            safe[key] = value
        elif isinstance(value, (list, dict)):
            safe[key] = value
        else:
            safe[key] = str(value)
    return safe


def detail_tool_observation(observation: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    tool = str(observation.get("tool") or "Tool")
    success = observation.get("success", True)
    output = observation.get("output") if isinstance(observation.get("output"), dict) else {}
    if not success:
        return f"{tool} 执行失败", {"success": False, "error": output.get("error", "")}
    summary = f"{tool} 完成"
    detail: dict[str, Any] = {"success": True}
    if "count" in output:
        summary += f"，返回 {output.get('count')} 条结果"
        detail["count"] = output["count"]
    if "items" in output and isinstance(output["items"], list):
        summary += f"，匹配 {len(output['items'])} 个物品"
        detail["items"] = output["items"]
    if "affordances" in output and isinstance(output["affordances"], list):
        summary += f"，发现 {len(output['affordances'])} 个场景要素"
        detail["affordances"] = output["affordances"]
    if "candidates" in output and isinstance(output["candidates"], list):
        summary += f"，候选线索 {len(output['candidates'])} 条"
        detail["candidates"] = output["candidates"]
    if "memories" in output and isinstance(output["memories"], list):
        summary += f"，召回 {len(output['memories'])} 条记忆"
        detail["memories"] = output["memories"]
    if "skill_checks" in output or "sanity_checks" in output:
        skill_checks = output.get("skill_checks") if isinstance(output.get("skill_checks"), list) else []
        sanity_checks = output.get("sanity_checks") if isinstance(output.get("sanity_checks"), list) else []
        summary += f"，技能检定 {len(skill_checks)} 次，理智检定 {len(sanity_checks)} 次"
        detail["skill_checks"] = skill_checks
        detail["sanity_checks"] = sanity_checks
        detail["dice_results"] = output.get("dice_results", [])
    if "adjudication" in output:
        detail["adjudication"] = output["adjudication"]
    return summary, detail
