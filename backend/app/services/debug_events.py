# 【调试事件发射器】
# 这个文件定义了调试事件的发射机制，用于在 Agent 运行过程中向前端推送实时状态信息。
# 对初学者来说，核心概念：
# - DebugEmitter：一个回调函数类型，接受 dict 参数，由 api.py 中的 Queue 实现。
# - emit_debug：统一的调试事件发射函数，所有 Agent 节点和 Tool 调用都通过它发送事件。
# - detail_tool_observation：将 Tool 执行结果转换为人类可读的摘要文本，用于调试面板显示。
#
# 调试事件流：Agent 节点/Tool -> emit_debug() -> debug_emit 回调 -> Queue -> NDJSON -> 前端调试面板
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

# DebugEmitter 是一个回调函数类型：接受一个 dict 参数，无返回值。
# 在 api.py 中，它被实现为向 Queue 放入事件的函数，由 StreamingResponse 消费并发送给前端。
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
    """发射调试事件。如果 debug_emit 为 None（非流式请求），则静默跳过。

    参数：
        debug_emit：回调函数，None 表示当前请求不需要调试事件
        phase：阶段名，如 "agent_node"、"tool"、"assistant"
        name：具体名称，如 "load_state"、"ContextSearchTool"
        status：状态，如 "start"、"success"、"warning"、"error"
        message：人类可读的状态描述
        metadata：附加数据，会被 sanitize_metadata 清理为可序列化格式
    """
    if debug_emit is None:
        return  # 非流式请求，不需要调试事件
    payload: dict[str, Any] = {
        "phase": phase,
        "name": name,
        "status": status,
        "message": message,
        "timestamp": datetime.now(timezone.utc).isoformat(),  # UTC 时间戳
    }
    if metadata:
        payload["metadata"] = sanitize_metadata(metadata)
    try:
        debug_emit(payload)  # 将事件放入 Queue，由 StreamingResponse 发送给前端
    except Exception:
        return  # 调试事件发送失败不应影响主流程


def sanitize_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """清理元数据，确保所有值都可以被 JSON 序列化。

    将非基本类型（如 ORM 对象、函数等）转为字符串，避免序列化报错。
    """
    safe: dict[str, Any] = {}
    for key, value in metadata.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            safe[key] = value  # 基本类型直接保留
        elif isinstance(value, (list, dict)):
            safe[key] = value  # 列表和字典递归序列化
        else:
            safe[key] = str(value)  # 其他类型转为字符串
    return safe


def detail_tool_observation(observation: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """将 Tool 执行结果转换为人类可读的摘要和详情，用于调试面板显示。

    根据不同 Tool 的输出字段生成对应的摘要文本：
    - ContextSearchTool：返回 N 条结果
    - InventoryLookupTool：匹配 N 个物品
    - SceneAffordanceTool：发现 N 个场景要素
    - ClueEligibilityTool：候选线索 N 条
    - MemoryRecallTool：召回 N 条记忆
    - RuleCheckTool：技能检定 N 次，理智检定 N 次

    返回：(摘要文本, 详情字典)
    """
    tool = str(observation.get("tool") or "Tool")
    success = observation.get("success", True)
    output = observation.get("output") if isinstance(observation.get("output"), dict) else {}
    if not success:
        return f"{tool} 执行失败", {"success": False, "error": output.get("error", "")}
    summary = f"{tool} 完成"
    detail: dict[str, Any] = {"success": True}
    # 根据各 Tool 特有的输出字段生成摘要
    if "count" in output:  # ContextSearchTool
        summary += f"，返回 {output.get('count')} 条结果"
        detail["count"] = output["count"]
    if "items" in output and isinstance(output["items"], list):  # InventoryLookupTool
        summary += f"，匹配 {len(output['items'])} 个物品"
        detail["items"] = output["items"]
    if "affordances" in output and isinstance(output["affordances"], list):  # SceneAffordanceTool
        summary += f"，发现 {len(output['affordances'])} 个场景要素"
        detail["affordances"] = output["affordances"]
    if "candidates" in output and isinstance(output["candidates"], list):  # ClueEligibilityTool
        summary += f"，候选线索 {len(output['candidates'])} 条"
        detail["candidates"] = output["candidates"]
    if "memories" in output and isinstance(output["memories"], list):  # MemoryRecallTool
        summary += f"，召回 {len(output['memories'])} 条记忆"
        detail["memories"] = output["memories"]
    if "skill_checks" in output or "sanity_checks" in output:  # RuleCheckTool
        skill_checks = output.get("skill_checks") if isinstance(output.get("skill_checks"), list) else []
        sanity_checks = output.get("sanity_checks") if isinstance(output.get("sanity_checks"), list) else []
        summary += f"，技能检定 {len(skill_checks)} 次，理智检定 {len(sanity_checks)} 次"
        detail["skill_checks"] = skill_checks
        detail["sanity_checks"] = sanity_checks
        detail["dice_results"] = output.get("dice_results", [])
    if "adjudication" in output:  # RuleCheckTool 的裁定结果
        detail["adjudication"] = output["adjudication"]
    return summary, detail
