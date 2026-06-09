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

from app.services.agent_monitor import AgentTraceRecorder
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
    trace_recorder: AgentTraceRecorder | None = None,
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
        if trace_recorder:
            with trace_recorder.step(agent_name="SkillTool", step_name=tool_name, phase="tool", input_payload=start_metadata or {}) as trace_step:
                observation = handler()
                trace_step["output"] = observation.as_dict()
        else:
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
    # 1. 从 runtime 中读取当前计划允许使用的 Tool 名称列表，并转成 set 方便快速判断。
    allowed_tools = set(runtime.get("allowed_tools") or [])  # allowed_tools = Tool 白名单，PlannerAgent 校验后传入
    # 2. 创建观察结果列表；每个 Tool 执行完都会把 ToolObservation.as_dict() 追加到这里。
    observations: list[dict[str, Any]] = []  # observations = 本 Skill 调用过的所有 Tool 观察记录
    # 3. 从 state 中取出当前游戏会话；Tool 需要从里面读取地点、物品栏、线索等信息。
    session = state["session"]  # session = GameSession ORM 对象，包含当前地点、物品、线索、日志等关联数据
    # 4. 从 state 中取出当前角色；RuleCheckTool 需要读取技能值、属性、SAN、幸运等字段。
    character = state["character"]  # character = Character ORM 对象，提供角色卡数据
    # 5. 读取结构化意图；里面通常包含 action_type、target、skill、reason 等字段。
    intent = state.get("intent", {})  # intent = ContextAgent 解析出的玩家行动意图
    # 6. 构建检索查询文本；ContextSearchTool 和 MemoryRecallTool 都会用这个 query 做向量检索。
    query = build_skill_query(state)  # query = 拼接地点、场景、玩家输入、目标和技能名得到的检索文本
    # 7. 读取调试事件回调；run_tool_with_debug 会用它把 Tool 状态推送给前端调试面板。
    debug_emit = runtime.get("debug_emit")  # debug_emit = 调试事件发射器，可为 None
    # 8. 读取 Agent 监控记录器；如果存在，就把每个 Tool 调用写入 /monitor 可查看的 trace。
    trace_recorder: AgentTraceRecorder | None = runtime.get("trace_recorder")  # trace_recorder = Agent 调用链记录器，可为 None

    # ===== 1. 上下文检索 =====
    # 9. 只有当计划白名单允许 ContextSearchTool，且 runtime 提供了 retrieval 服务时，才执行上下文检索。
    if "ContextSearchTool" in allowed_tools and runtime.get("retrieval") is not None:
        run_tool_with_debug(  # 10. 用统一包装器执行 Tool，自动记录调试事件和观察结果
            debug_emit,  # debug_emit = 调试事件回调
            observations,  # observations = 收集 ToolObservation 的列表，会被原地追加
            "ContextSearchTool",  # tool_name = 当前执行的 Tool 名称
            "开始检索上下文。",  # start_message = 前端调试面板显示的开始消息
            lambda: run_context_search(  # handler = 真正执行 Chroma 检索的无参函数
                retrieval=runtime["retrieval"],  # retrieval = RetrievalService 实例，封装 Chroma 查询
                query=query,  # query = 上面构造的检索文本
                collections=runtime.get("collections") or DEFAULT_COLLECTIONS,  # collections = 要查的向量集合，缺省使用剧本/实体/线索/规则
                n_results=int(runtime.get("n_results") or 3),  # n_results = 每个集合最多返回多少条
            ),
            start_metadata={  # start_metadata = 写入调试事件和 trace 的输入摘要
                "query": query,  # query = 本次检索文本
                "collections": runtime.get("collections") or DEFAULT_COLLECTIONS,  # collections = 本次检索集合
            },
            trace_recorder=trace_recorder,  # trace_recorder = 可选监控记录器
        )

    # ===== 2. 物品栏查询 =====
    # 11. 如果计划允许 InventoryLookupTool，就查询当前会话物品栏。
    if "InventoryLookupTool" in allowed_tools:
        run_tool_with_debug(  # 12. 统一执行物品栏查询 Tool，并收集观察结果
            debug_emit,  # debug_emit = 调试事件回调
            observations,  # observations = Tool 观察结果列表
            "InventoryLookupTool",  # tool_name = 物品栏查询 Tool
            "开始查询物品栏。",  # start_message = 调试面板展示文本
            lambda: run_inventory_lookup(  # handler = 真正执行物品栏过滤/查询的函数
                items=getattr(session, "inventory_items", []),  # items = 当前会话物品列表，没有该属性时回退空列表
                query=str(intent.get("target") or ""),  # query = 玩家行动目标，用于在物品名/描述中匹配
            ),
            start_metadata={"target": str(intent.get("target") or "")},  # start_metadata = 记录本次查询目标
            trace_recorder=trace_recorder,  # trace_recorder = 可选监控记录器
        )

    # ===== 3. 场景可交互信息 =====
    # 13. 如果计划允许 SceneAffordanceTool，就读取当前地点有哪些可交互对象和可前往地点。
    if "SceneAffordanceTool" in allowed_tools:
        run_tool_with_debug(  # 14. 统一执行场景可交互查询 Tool
            debug_emit,  # debug_emit = 调试事件回调
            observations,  # observations = Tool 观察结果列表
            "SceneAffordanceTool",  # tool_name = 场景可交互信息 Tool
            "开始读取场景可交互信息。",  # start_message = 调试面板展示文本
            lambda: run_scene_affordance(  # handler = 从实体上下文和 story_state 中提取 affordance
                location_context=state.get("entity_context", []),  # location_context = ContextAgent 检索到的地点/实体片段
                story_state=state.get("story_state", {}),  # story_state = 当前长期剧情状态
            ),
            start_metadata={"location": getattr(session, "current_location", "")},  # start_metadata = 当前地点，便于调试定位
            trace_recorder=trace_recorder,  # trace_recorder = 可选监控记录器
        )

    # ===== 4. 线索候选资格 =====
    # 15. 如果计划允许 ClueEligibilityTool，就判断候选线索是否可能被当前行动发现。
    if "ClueEligibilityTool" in allowed_tools:
        # 16. 提取当前会话已发现线索的 key，用于避免重复发现同一条线索。
        known_keys = [str(getattr(clue, "clue_key", "")) for clue in getattr(session, "clues", [])]  # known_keys = 已发现线索 key 列表
        run_tool_with_debug(  # 17. 统一执行线索候选资格判断 Tool
            debug_emit,  # debug_emit = 调试事件回调
            observations,  # observations = Tool 观察结果列表
            "ClueEligibilityTool",  # tool_name = 线索资格判断 Tool
            "开始判断线索候选资格。",  # start_message = 调试面板展示文本
            lambda: run_clue_eligibility(  # handler = 真正执行线索过滤和目标匹配的函数
                target=str(intent.get("target") or ""),  # target = 玩家行动目标，如“脚印”“灯塔门口”
                clue_context=state.get("clue_context", []),  # clue_context = ContextAgent 检索到的候选线索片段
                known_clue_keys=known_keys,  # known_clue_keys = 已发现线索 key，用于去重
            ),
            start_metadata={  # start_metadata = 调试事件输入摘要
                "target": str(intent.get("target") or ""),  # target = 本次判断的目标
                "known_clue_count": len(known_keys),  # known_clue_count = 已发现线索数量
            },
            trace_recorder=trace_recorder,  # trace_recorder = 可选监控记录器
        )

    # ===== 5. 会话记忆召回 =====
    # 18. 如果计划允许 MemoryRecallTool，且 retrieval 服务可用，就从 Chroma 召回本会话长期记忆。
    if "MemoryRecallTool" in allowed_tools and runtime.get("retrieval") is not None:
        run_tool_with_debug(  # 19. 统一执行会话记忆召回 Tool
            debug_emit,  # debug_emit = 调试事件回调
            observations,  # observations = Tool 观察结果列表
            "MemoryRecallTool",  # tool_name = 会话记忆召回 Tool
            "开始召回会话记忆。",  # start_message = 调试面板展示文本
            lambda: run_memory_recall(  # handler = 真正执行 session_memory_chunks 检索的函数
                retrieval=runtime["retrieval"],  # retrieval = RetrievalService 实例
                query=query,  # query = 本 Skill 的检索文本
                session_id=session.id,  # session_id = 当前会话 ID，只召回本局游戏记忆
                n_results=3,  # n_results = 召回记忆条数
            ),
            start_metadata={"query": query, "session_id": session.id},  # start_metadata = 记录检索文本和会话 ID
            trace_recorder=trace_recorder,  # trace_recorder = 可选监控记录器
        )

    # ===== 6. 规则检定（仅当需要时） =====
    # 20. RuleCheckTool 需要同时满足两个条件：当前行动确实需要检定，并且计划白名单允许它。
    if should_run_rule_check(spec.name, state) and "RuleCheckTool" in allowed_tools:
        run_tool_with_debug(  # 21. 统一执行规则检定 Tool，并记录骰点结果
            debug_emit,  # debug_emit = 调试事件回调
            observations,  # observations = Tool 观察结果列表
            "RuleCheckTool",  # tool_name = 规则检定 Tool
            "开始执行规则检定。",  # start_message = 调试面板展示文本
            lambda: run_rule_check(  # handler = 先裁定行动，再执行技能/理智检定
                message=state.get("player_input", ""),  # message = 玩家原始行动文本
                intent=intent,  # intent = 结构化意图，包含 action_type/target/skill 等
                character_skills=character.skills,  # character_skills = 角色技能字典，如 {"侦查": 60}
                character_attributes=character.attributes,  # character_attributes = 角色属性字典
                scenario_context=state.get("scenario_context", []),  # scenario_context = 剧本上下文，辅助判断风险和难度
                default_skill=str(intent.get("skill") or runtime.get("default_skill") or "侦查"),  # default_skill = 未明确技能时的默认检定技能
                current_san=character.san_current,  # current_san = 当前理智值，用于理智检定后计算损失
                luck=character.luck,  # luck = 幸运值，规则裁定可能会使用
            ),
            start_metadata={  # start_metadata = 调试事件输入摘要
                "default_skill": str(intent.get("skill") or runtime.get("default_skill") or "侦查"),  # default_skill = 本次预期检定技能
                "current_san": character.san_current,  # current_san = 检定前理智值
            },
            trace_recorder=trace_recorder,  # trace_recorder = 可选监控记录器
        )

    # ===== 汇总结果 =====
    # 22. 所有允许的 Tool 尝试执行完后，统一打包成 SkillResult 返回给 ExecutorAgent。
    return SkillResult(  # SkillResult = Skill 的标准输出结构
        skill=spec.name,  # skill = 当前 Skill 名称，如 InvestigateSkill / MoveSkill
        input={"player_input": state.get("player_input", ""), "intent": intent},  # input = 本 Skill 的关键输入摘要，便于日志和调试
        observations=observations,  # observations = 所有 Tool 的观察结果列表
        result={  # result = 给后续 Agent 使用的汇总信息
            "decision_summary": build_decision_summary(spec, observations),  # decision_summary = 人类可读的一句话执行摘要
            "candidate_resolution": build_candidate_resolution(spec, observations),  # candidate_resolution = 候选裁定，声明需要后续综合处理
            "used_tools": [item.get("tool") for item in observations],  # used_tools = 本 Skill 实际调用过的 Tool 名称
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
