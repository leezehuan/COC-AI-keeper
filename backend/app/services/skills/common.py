# =============================================================================
# 【Skill 通用执行逻辑】
# =============================================================================
# 这个文件提供了所有 Skill 共用的执行框架。
# 大部分 Skill（investigate、move、social 等）都使用这里的通用流程，
# 只有少数特殊 Skill（如 danger_and_sanity）有自己的执行逻辑。
#
# 核心函数：
#
# 1. run_generic_skill（通用 Skill 执行）
#    - 所有标准 Skill 的入口，按顺序调用 6 个 Tool
#    - 每个 Tool 只在白名单中时才执行
#    - 收集所有 Tool 的观察结果，汇总为 SkillResult
#
# 2. run_tool_with_debug（带调试的 Tool 包装器）
#    - 在 Tool 执行前后发射调试事件
#    - 捕获异常并发射 error 事件
#    - 将 ToolObservation 转为字典追加到观察列表
#
# 3. build_skill_query（构建检索查询）
#    - 拼接地点、场景、玩家输入、目标、技能名
#    - 生成的查询文本用于向量检索
#
# 4. should_run_rule_check（判断是否需要规则检定）
#    - 危险/战斗 Skill 始终需要检定
#    - 战斗/说服/恐吓/潜行等行动类型需要检定
# =============================================================================
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.services.debug_events import DebugEmitter, emit_debug, detail_tool_observation
from app.services.skills.base import SkillResult, SkillSpec
from app.services.tools.clue_eligibility import run_clue_eligibility
from app.services.tools.context_search import run_context_search
from app.services.tools.inventory_lookup import run_inventory_lookup
from app.services.tools.memory_recall import run_memory_recall
from app.services.tools.rule_check import run_rule_check
from app.services.tools.scene_affordance import run_scene_affordance


# 默认检索的 ChromaDB collection 列表
DEFAULT_COLLECTIONS = ["scenario_chunks", "scenario_entities", "clue_index", "rule_chunks"]


def run_tool_with_debug(
    debug_emit: DebugEmitter | None,
    observations: list[dict[str, Any]],
    tool_name: str,
    start_message: str,
    handler: Callable[[], Any],
    start_metadata: dict[str, Any] | None = None,
) -> None:
    """带调试事件发射的 Tool 执行包装器（run_tool_with_debug = 带调试运行 Tool）。

    【中文名称】带调试运行 Tool

    【功能说明】
    在 Tool 执行前后自动发射调试事件，让前端调试面板能看到每个 Tool
    的执行状态。同时处理异常和结果收集。

    【执行流程】
    1. 发射 tool.start 事件
    2. 调用 handler() 执行 Tool
    3. 如果抛异常 → 发射 tool.error 事件 → 重新抛出
    4. 将结果转为字典 → 追加到 observations 列表
    5. 发射 tool.success 或 tool.warning 事件

    【参数说明】
    - debug_emit: 调试事件发射器
    - observations: 观察结果列表（会被原地修改）
    - tool_name: Tool 名称（如 "ContextSearchTool"）
    - start_message: 开始执行时的日志消息
    - handler: 实际执行 Tool 的无参函数
    - start_metadata: 开始事件附带的元数据

    【返回值】无（结果通过修改 observations 列表传出）
    """
    emit_debug(debug_emit, phase="tool", name=tool_name, status="start", message=start_message, metadata=start_metadata)
    try:
        observation = handler()
    except Exception as exc:
        emit_debug(debug_emit, phase="tool", name=tool_name, status="error", message=str(exc)[:500])
        raise
    payload = observation.as_dict()
    observations.append(payload)
    status = "success" if payload.get("success", True) else "warning"
    msg, detail = detail_tool_observation(payload)  # 生成人类可读摘要
    emit_debug(debug_emit, phase="tool", name=tool_name, status=status, message=msg, metadata=detail)


def run_generic_skill(*, spec: SkillSpec, state: dict[str, Any], runtime: dict[str, Any]) -> SkillResult:
    """通用 Skill 执行流程（run_generic_skill = 运行通用 Skill）。

    【中文名称】运行通用 Skill

    【功能说明】
    所有标准 Skill 的统一执行入口。按固定顺序尝试调用 6 个 Tool，
    每个 Tool 只在 allowed_tools 白名单中时才执行。

    【6 个 Tool 的执行顺序】
    1. ContextSearchTool → 从 ChromaDB 检索上下文
    2. InventoryLookupTool → 查询角色物品栏
    3. SceneAffordanceTool → 查询场景可交互信息
    4. ClueEligibilityTool → 判断线索候选资格
    5. MemoryRecallTool → 召回会话记忆
    6. RuleCheckTool → 执行规则检定（仅当需要时）

    【参数说明】
    - spec: Skill 规格说明（名称、描述、允许的 Tool 列表）
    - state: 当前回合状态（session、character、intent 等）
    - runtime: 运行时参数（retrieval、debug_emit、allowed_tools 等）

    【返回值】
    - SkillResult: 包含所有 Tool 观察结果和决策摘要
    """
    allowed_tools = set(runtime.get("allowed_tools") or [])  # 当前 Skill 允许使用的 Tool 白名单
    observations: list[dict[str, Any]] = []  # 收集所有 Tool 的观察结果
    session = state["session"]  # GameSession ORM 对象
    character = state["character"]  # Character ORM 对象
    intent = state.get("intent", {})  # 结构化意图
    query = build_skill_query(state)  # 构建检索查询文本
    debug_emit = runtime.get("debug_emit")  # 调试事件发射器

    # ===== 1. 上下文检索 =====
    if "ContextSearchTool" in allowed_tools and runtime.get("retrieval") is not None:
        run_tool_with_debug(
            debug_emit, observations, "ContextSearchTool", "开始检索上下文。",
            lambda: run_context_search(
                retrieval=runtime["retrieval"], query=query,
                collections=runtime.get("collections") or DEFAULT_COLLECTIONS,
                n_results=int(runtime.get("n_results") or 3),
            ),
            start_metadata={"query": query, "collections": runtime.get("collections") or DEFAULT_COLLECTIONS},
        )

    # ===== 2. 物品栏查询 =====
    if "InventoryLookupTool" in allowed_tools:
        run_tool_with_debug(
            debug_emit, observations, "InventoryLookupTool", "开始查询物品栏。",
            lambda: run_inventory_lookup(items=getattr(session, "inventory_items", []), query=str(intent.get("target") or "")),
            start_metadata={"target": str(intent.get("target") or "")},
        )

    # ===== 3. 场景可交互信息 =====
    if "SceneAffordanceTool" in allowed_tools:
        run_tool_with_debug(
            debug_emit, observations, "SceneAffordanceTool", "开始读取场景可交互信息。",
            lambda: run_scene_affordance(location_context=state.get("entity_context", []), story_state=state.get("story_state", {})),
            start_metadata={"location": getattr(session, "current_location", "")},
        )

    # ===== 4. 线索候选资格 =====
    if "ClueEligibilityTool" in allowed_tools:
        known_keys = [str(getattr(clue, "clue_key", "")) for clue in getattr(session, "clues", [])]  # 已发现线索的 key
        run_tool_with_debug(
            debug_emit, observations, "ClueEligibilityTool", "开始判断线索候选资格。",
            lambda: run_clue_eligibility(target=str(intent.get("target") or ""), clue_context=state.get("clue_context", []), known_clue_keys=known_keys),
            start_metadata={"target": str(intent.get("target") or ""), "known_clue_count": len(known_keys)},
        )

    # ===== 5. 会话记忆召回 =====
    if "MemoryRecallTool" in allowed_tools and runtime.get("retrieval") is not None:
        run_tool_with_debug(
            debug_emit, observations, "MemoryRecallTool", "开始召回会话记忆。",
            lambda: run_memory_recall(retrieval=runtime["retrieval"], query=query, session_id=session.id, n_results=3),
            start_metadata={"query": query, "session_id": session.id},
        )

    # ===== 6. 规则检定（仅当需要时） =====
    if should_run_rule_check(spec.name, state) and "RuleCheckTool" in allowed_tools:
        run_tool_with_debug(
            debug_emit, observations, "RuleCheckTool", "开始执行规则检定。",
            lambda: run_rule_check(
                message=state.get("player_input", ""), intent=intent,
                character_skills=character.skills, character_attributes=character.attributes,
                scenario_context=state.get("scenario_context", []),
                default_skill=str(intent.get("skill") or runtime.get("default_skill") or "侦查"),
                current_san=character.san_current, luck=character.luck,
            ),
            start_metadata={"default_skill": str(intent.get("skill") or runtime.get("default_skill") or "侦查"), "current_san": character.san_current},
        )

    # ===== 汇总结果 =====
    return SkillResult(
        skill=spec.name,
        input={"player_input": state.get("player_input", ""), "intent": intent},
        observations=observations,  # 所有 Tool 的观察结果
        result={
            "decision_summary": build_decision_summary(spec, observations),  # 决策摘要
            "candidate_resolution": build_candidate_resolution(spec, observations),  # 候选裁定
            "used_tools": [item.get("tool") for item in observations],  # 实际使用的 Tool 列表
        },
    )


def build_skill_query(state: dict[str, Any]) -> str:
    """构建 Skill 的检索查询文本（build_skill_query = 构建 Skill 查询）。

    【中文名称】构建 Skill 查询

    【功能说明】
    拼接当前地点、场景、玩家输入、行动目标和技能名，
    生成一个用于向量检索的查询字符串。

    【参数说明】
    - state: 当前回合状态

    【返回值】
    - str: 检索查询文本
    """
    session = state.get("session")
    intent = state.get("intent", {})
    return " ".join(
        [
            str(getattr(session, "current_location", "")),  # 当前地点
            str(getattr(session, "current_scene", "")),  # 当前场景
            str(state.get("player_input", "")),  # 玩家输入
            str(intent.get("target", "")),  # 行动目标
            str(intent.get("skill", "")),  # 使用技能
        ]
    ).strip()


def should_run_rule_check(skill_name: str, state: dict[str, Any]) -> bool:
    """判断是否需要执行规则检定（should_run_rule_check = 是否应执行规则检定）。

    【中文名称】是否应执行规则检定

    【功能说明】
    根据 Skill 类型和行动类型判断是否需要掷骰子。

    【判断规则】
    - DangerAndSanitySkill / CombatLiteSkill → 始终需要
    - 行动类型为战斗/说服/恐吓/潜行 → 需要
    - 意图中明确指定了技能 → 需要

    【参数说明】
    - skill_name: Skill 名称
    - state: 当前回合状态

    【返回值】
    - bool: True 表示需要执行规则检定
    """
    action_type = str((state.get("intent") or {}).get("action_type") or "")
    if skill_name in {"DangerAndSanitySkill", "CombatLiteSkill"}:
        return True  # 危险和战斗技能始终需要检定
    return action_type in {"战斗", "说服", "恐吓", "潜行"} or bool((state.get("intent") or {}).get("skill"))


def build_decision_summary(spec: SkillSpec, observations: list[dict[str, Any]]) -> str:
    """构建决策摘要（build_decision_summary = 构建决策摘要）。

    【中文名称】构建决策摘要

    【功能说明】
    生成一句话摘要，描述 Skill 调用了哪些 Tool，
    供 NarratorAgent 和 GuardAgent 理解 Skill 做了什么。

    【参数说明】
    - spec: Skill 规格说明
    - observations: Tool 观察结果列表

    【返回值】
    - str: 决策摘要文本
    """
    tool_names = [str(item.get("tool")) for item in observations if item.get("tool")]
    if not tool_names:
        return f"{spec.name} 未调用工具，仅保留行动意图供后续节点处理。"
    return f"{spec.name} 调用了 {'、'.join(tool_names)}，形成候选裁定。"


def build_candidate_resolution(spec: SkillSpec, observations: list[dict[str, Any]]) -> dict[str, Any]:
    """构建候选裁定（build_candidate_resolution = 构建候选裁定）。

    【中文名称】构建候选裁定

    【功能说明】
    标记 Skill 结果需要后续综合处理：
    - requires_synthesis=True: 需要 NarratorAgent 综合裁定
    - no_direct_state_write=True: 不直接写入状态，由 commit_state 统一处理

    【参数说明】
    - spec: Skill 规格说明
    - observations: Tool 观察结果列表

    【返回值】
    - dict: 候选裁定字典
    """
    return {
        "skill": spec.name,
        "observation_count": len(observations),
        "requires_synthesis": True,  # 需要后续综合处理
        "no_direct_state_write": True,  # 不直接写入状态
    }
