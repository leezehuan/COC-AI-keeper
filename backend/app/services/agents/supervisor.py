# =============================================================================
# 【KeeperSupervisor：多 Agent 调度器】
# =============================================================================
# 这是整个回合执行流程的"大脑"——负责按顺序调度各个 Agent 并处理异常。
# 你可以把它理解为"项目经理"：自己不干活，但确保每个专业工人按时完成任务。
#
# 完整的回合流程（run_turn）：
#
# Phase 1: ContextAgent（情报收集）
#   → 加载会话状态、解析玩家意图、RAG 检索
#
# Phase 2: PlannerAgent（制定计划）
#   → 生成回合计划、校验白名单
#   → 如果需要追问 → 跳过后续 Phase，直接追问玩家
#
# Phase 3: ExecutorAgent（执行计划）
#   → 执行 Skill、规则裁定、掷骰检定、综合裁定
#
# Phase 4: NarratorAgent（生成叙事）
#   → 生成守秘人叙事文本、玩家选项、状态增量
#
# Phase 5: GuardAgent（质量检查）
#   → 确定性校验、Reflection 自检、防剧透清洗、修复判断
#
# Phase 5.5: Repair Loop（修复循环，最多 2 次）
#   → 如果 GuardAgent 发现问题，根据修复类型执行相应操作
#   → repair_text: 让 NarratorAgent 重新生成叙事
#   → repair_state_delta: 修复状态增量
#   → ask_clarification/fail_safe: 构造追问/兜底响应
#   → replan_once: 重新规划一次
#
# Phase 6: _commit_state（落库保存）
#   → 更新 PostgreSQL（状态、线索、物品、回合日志）
#   → 写入 ChromaDB（会话记忆向量）
#
# 对旧的 KeeperAgent.run_turn 调用保持兼容。
# =============================================================================
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app import models
from app.services.agents.base import AgentContext, AgentMessage, BaseAgent
from app.services.agents.context_agent import ContextAgent
from app.services.agents.executor_agent import ExecutorAgent
from app.services.agents.guard_agent import GuardAgent
from app.services.agents.narrator_agent import NarratorAgent
from app.services.agents.planner_agent import PlannerAgent
from app.services.agents.utils import (
    build_session_memory_chunk,
    ensure_options,
    update_no_clue_counter,
)
from app.services.agent_monitor import AgentTraceRecorder
from app.services.chunking import DocumentChunk
from app.services.debug_events import DebugEmitter, emit_debug
from app.services.guardrails import validate_state_delta
from app.services.inventory import apply_inventory_changes
from app.services.llm import LLMClient
from app.services.retrieval import RetrievalService
from app.services.story_state import apply_turn_delta
from app.services.summary import apply_summary_to_session, build_summary_memory_chunk, build_turn_summary
from app.utils import safe_key


class KeeperSupervisor:
    """多 Agent 调度器（可以理解为"项目经理/指挥中心"）。

    【中文名称】守秘人调度器 / 主管 Agent

    【功能说明】
    这是整个回合流程的中央控制器。它不执行具体的业务逻辑，
    而是负责：
    1. 按顺序调度 5 个专业 Agent
    2. 在 Agent 之间传递数据（组装 AgentMessage 信封）
    3. 处理修复循环（最多 2 次）
    4. 最终将结果写入数据库

    【为什么需要调度器】
    5 个 Agent 各司其职，但需要有人协调它们的工作顺序和数据传递。
    就像建筑工地需要工头来协调电工、水管工、木工的工作顺序。
    没有 Supervisor，Agent 们不知道谁先谁后、数据怎么传递。

    【对外接口】
    run_turn(db, session_id, player_input, debug_emit=None, trace_recorder=None) → dict
    调用签名与旧版 KeeperAgent.run_turn 保持兼容；当前 API 层直接调用 KeeperSupervisor。
    """

    def __init__(self) -> None:
        """初始化调度器（__init__ = 构造函数/初始化方法）。

        【中文名称】初始化

        【功能说明】
        创建 KeeperSupervisor 实例时自动调用。完成以下初始化工作：
        1. 创建 LLM 客户端（用于调用大语言模型）
        2. 创建检索服务（用于查询 ChromaDB）
        3. 创建共享上下文（AgentContext，注入 LLM 和检索服务）
        4. 创建 5 个专业 Agent 实例（注入共享上下文）

        【创建的 Agent 列表】
        - context_agent: ContextAgent → 上下文加载与意图解析
        - planner_agent: PlannerAgent → 回合计划生成
        - executor_agent: ExecutorAgent → 计划执行与规则检定
        - narrator_agent: NarratorAgent → 守秘人叙事生成
        - guard_agent: GuardAgent → 守卫校验与防剧透

        【参数说明】无参数
        【返回值】无（返回 None）
        """
        self.llm = LLMClient()  # 创建 LLM 客户端
        self.retrieval = RetrievalService()  # 创建向量检索服务
        self.context = AgentContext(llm=self.llm, retrieval=self.retrieval)  # 创建共享服务上下文
        # 创建五个专业 Agent，注入共享上下文
        self.context_agent = ContextAgent(self.context)  # 上下文加载与意图解析
        self.planner_agent = PlannerAgent(self.context)  # 回合计划生成
        self.executor_agent = ExecutorAgent(self.context)  # 计划执行与规则检定
        self.narrator_agent = NarratorAgent(self.context)  # 守秘人叙事生成
        self.guard_agent = GuardAgent(self.context)  # 守卫校验与防剧透

    def run_turn(
        self,
        db: Session,
        session_id: str,
        player_input: str,
        debug_emit: DebugEmitter | None = None,
        trace_recorder: AgentTraceRecorder | None = None,
    ) -> dict[str, Any]:
        """执行一个完整的游戏回合（run_turn = 运行回合）。

        【中文名称】运行回合

        【功能说明】
        这是 Supervisor 最重要的方法，也是外部调用的唯一入口。
        接收玩家输入，调度 5 个 Agent 依次处理，最终返回包含叙事、
        选项、状态变化等信息的字典。

        【完整的 6 个阶段】
        Phase 1: ContextAgent → 加载状态、解析意图、RAG 检索
        Phase 2: PlannerAgent → 生成计划、校验白名单
          （如果需要追问，跳到 _clarify_and_commit）
        Phase 3: ExecutorAgent → 执行 Skill、规则检定
        Phase 4: NarratorAgent → 生成叙事和选项
        Phase 5: GuardAgent → 校验、自检、防剧透
        Phase 5.5: Repair Loop → 最多 2 次修复循环
        Phase 6: _commit_state → 写入数据库

        【参数说明】
        - db: Session → SQLAlchemy 数据库会话，用于查询和写入 PostgreSQL
        - session_id: str → 游戏会话的唯一标识符
        - player_input: str → 玩家输入的自然语言文本
        - debug_emit: DebugEmitter | None → 调试事件发射器（可选）
        - trace_recorder: AgentTraceRecorder | None → Agent 监控记录器（可选）

        【返回值】
        - dict: 面向 API 层的回合结果字典，字段兼容旧版 KeeperState，包含：
          - narration: 守秘人叙事文本
          - options: 玩家可选行动列表
          - state_delta: 状态变化
          - discovered_clues: 新发现的线索
          - skill_checks / sanity_checks: 检定结果
          - 等等...
        """
        # 0. 发出调试事件：告诉前端调试面板“Supervisor 已经开始处理本回合”。
        emit_debug(debug_emit, phase="stream", name="supervisor", status="start", message="Supervisor 开始调度回合。")

        # === Phase 1: 加载上下文 ===
        # 1. 构造发给 ContextAgent 的消息信封：Agent 之间不直接散传参数，而是统一放进 AgentMessage。
        ctx_envelope = AgentMessage(  # ctx_envelope = 上下文阶段输入信封，负责携带数据库会话、会话ID和玩家输入
            payload={  # payload = 信封负载，真正要给 ContextAgent 使用的数据都在这里
                "db": db,  # db = SQLAlchemy 数据库会话，ContextAgent 用它读取 GameSession、Character、Clue 等对象
                "session_id": session_id,  # session_id = 当前游戏会话 ID，用来定位这一次跑团存档
                "player_input": player_input,  # player_input = 玩家原始输入文本，是后续意图解析和检索查询的基础
                "debug_emit": debug_emit,  # debug_emit = 调试事件回调，用于把 Agent 节点状态推给前端调试面板
                "trace_recorder": trace_recorder,  # trace_recorder = 监控记录器，用于 /monitor 页面回放 Agent 执行步骤
            }
        )
        ctx_result = self.context_agent.run(ctx_envelope)  # 2. 运行 ContextAgent：加载会话状态、解析意图、执行 RAG 检索
        ctx = ctx_result["payload"]  # 3. 取出 ContextAgent 输出负载，后续阶段都会复用这里的 session、intent、context

        # === Phase 2: 生成计划 ===
        # 4. 构造发给 PlannerAgent 的消息信封：计划阶段只需要玩家可见信息和结构化意图。
        plan_envelope = AgentMessage(  # plan_envelope = 计划阶段输入信封
            payload={  # payload = PlannerAgent 需要读取的计划输入
                "visible_context": ctx["visible_context"],  # visible_context = 玩家可见上下文，避免计划阶段依赖不该暴露的秘密
                "intent": ctx["intent"],  # intent = ContextAgent 解析出的行动类型、目标、技能等结构化意图
                "player_input": player_input,  # player_input = 原始玩家输入，给 LLM 生成计划时保留完整语义
                "debug_emit": debug_emit,  # debug_emit = 调试事件回调，记录 PlannerAgent 的开始/成功/失败
                "trace_recorder": trace_recorder,  # trace_recorder = 监控记录器，保存 PlannerAgent 的输入输出
            }
        )
        plan_result = self.planner_agent.run(plan_envelope)  # 5. 运行 PlannerAgent：生成 turn_plan、allowed_tools、allowed_skills
        plan = plan_result["payload"]  # 6. 取出计划结果，后续 ExecutorAgent 会严格按这里的白名单执行

        # 若需要追问，跳过后续 Phase，直接构造澄清结果并落库
        if plan["needs_clarification"]:  # 7. 如果 PlannerAgent 判断玩家输入太模糊，本回合不执行技能，只追问玩家
            emit_debug(debug_emit, phase="agent_node", name="Supervisor", status="success", message="玩家输入模糊，进入澄清分支。")
            return self._clarify_and_commit(  # 8. 构造追问回复并落库，保证“追问回合”也有回合日志
                db,  # db = 当前数据库会话
                ctx["session"],  # session = ContextAgent 已加载的 GameSession ORM 对象
                ctx["character"],  # character = 当前玩家角色对象
                player_input,  # player_input = 玩家刚才的模糊输入
                ctx["intent"],  # intent = 已解析出的意图，可能带 needs_clarification 标记
                plan["turn_plan"],  # turn_plan = PlannerAgent 生成的计划，通常包含 clarification_question
                ctx["story_state"],  # story_state = 当前长期剧情状态
                debug_emit,  # debug_emit = 调试事件回调
                trace_recorder,  # trace_recorder = Agent 监控记录器
            )

        # === Phase 3: 执行计划（Skill + 规则检定） ===
        # 9. 构造发给 ExecutorAgent 的消息信封：执行阶段需要计划、状态、角色和所有检索上下文。
        exec_envelope = AgentMessage(  # exec_envelope = 执行阶段输入信封
            payload={  # payload = ExecutorAgent 运行 Skill 和 Tool 所需的完整输入
                "turn_plan": plan["turn_plan"],  # turn_plan = 计划详情，包含目标、风险、允许的 Tool/Skill
                "visible_context": ctx["visible_context"],  # visible_context = 玩家可见信息，供 Skill 判断当前公开状态
                "keeper_only_context": ctx["keeper_only_context"],  # keeper_only_context = 守秘人专用状态，执行时可用于裁定但不能直接泄露
                "player_input": player_input,  # player_input = 原始行动文本，RuleCheckTool 和检索查询都会用到
                "intent": ctx["intent"],  # intent = 结构化意图，帮助选择技能、目标和行动类型
                "session": ctx["session"],  # session = 当前 GameSession ORM 对象，包含地点、场景、线索、物品等关系
                "character": ctx["character"],  # character = 当前角色对象，规则检定需要技能值、属性、SAN 等
                "scenario_context": ctx["scenario_context"],  # scenario_context = 剧本检索片段，提供场景依据
                "entity_context": ctx["entity_context"],  # entity_context = 地点/NPC/物品等结构化实体检索结果
                "clue_context": ctx["clue_context"],  # clue_context = 线索索引候选，ClueEligibilityTool 会读取
                "memory_context": ctx["memory_context"],  # memory_context = 会话长期记忆，用于保持前后文一致
                "rule_context": ctx["rule_context"],  # rule_context = 相关规则书片段，辅助叙事和裁定解释
                "debug_emit": debug_emit,  # debug_emit = 调试事件回调，记录 Skill/Tool 执行状态
                "trace_recorder": trace_recorder,  # trace_recorder = 监控记录器，记录 ExecutorAgent 和 Tool 调用
            }
        )
        exec_result = self.executor_agent.run(exec_envelope)  # 10. 运行 ExecutorAgent：按计划执行 Skill、Tool 和规则检定
        exec_data = exec_result["payload"]  # 11. 取出执行结果：包含 react_trace、tool_observations、dice_results、resolution

        # === Phase 4: 生成叙事（守秘人回应 + 状态增量） ===
        # 12. 构造发给 NarratorAgent 的消息信封：叙事阶段把执行结果翻译成玩家可见文本和状态增量。
        narr_envelope = AgentMessage(  # narr_envelope = 叙事阶段输入信封
            payload={  # payload = NarratorAgent 生成 narration/options/state_delta 所需的数据
                "visible_context": ctx["visible_context"],  # visible_context = 仅玩家可见信息，控制叙事不要主动剧透
                "resolution": exec_data["resolution"],  # resolution = ExecutorAgent 汇总后的综合裁定
                "skill_checks": exec_data["skill_checks"],  # skill_checks = 技能检定结果列表，叙事中可展示骰点结果
                "sanity_checks": exec_data["sanity_checks"],  # sanity_checks = 理智检定结果列表，用于叙述 SAN 损失
                "player_input": player_input,  # player_input = 玩家原始行动，帮助生成回应时贴合玩家表达
                "intent": ctx["intent"],  # intent = 结构化意图，辅助生成准确的目标和行动类型
                "adjudication": exec_data["adjudication"],  # adjudication = 规则裁定依据，如是否需要检定、耗时、风险
                "scenario_context": ctx["scenario_context"],  # scenario_context = 剧本上下文，提供叙事素材
                "entity_context": ctx["entity_context"],  # entity_context = 实体上下文，提供地点和对象信息
                "clue_context": ctx["clue_context"],  # clue_context = 线索候选上下文，辅助生成 discovered_clues
                "memory_context": ctx["memory_context"],  # memory_context = 历史记忆，保持叙事连续
                "rule_context": ctx["rule_context"],  # rule_context = 规则片段，帮助叙事解释检定
                "session": ctx["session"],  # session = 当前会话对象，提供地点、时间、物品等状态
                "character": ctx["character"],  # character = 当前角色对象，提供角色职业、HP/SAN 等信息
                "story_state": ctx["story_state"],  # story_state = 长期剧情状态，build_turn_delta 会基于它构造状态增量
                "debug_emit": debug_emit,  # debug_emit = 调试事件回调
                "trace_recorder": trace_recorder,  # trace_recorder = 监控记录器
            }
        )
        narr_result = self.narrator_agent.run(narr_envelope)  # 13. 运行 NarratorAgent：生成叙事、选项、候选 state_delta
        narr_data = narr_result["payload"]  # 14. 取出叙事结果，下一步交给 GuardAgent 校验和清洗

        # === Phase 5: 校验与 Reflection（确定性校验 + 自检 + 防剧透） ===
        # 15. 构造发给 GuardAgent 的消息信封：校验阶段会检查叙事、选项、状态增量是否安全合法。
        guard_envelope = AgentMessage(  # guard_envelope = 守卫阶段输入信封
            payload={  # payload = GuardAgent 做确定性校验、Reflection 和防剧透清洗所需的数据
                "narration": narr_data["narration"],  # narration = NarratorAgent 生成的原始叙事，可能需要清洗
                "options": narr_data["options"],  # options = NarratorAgent 生成的行动建议，可能需要去重或防剧透
                "state_delta": narr_data["state_delta"],  # state_delta = 候选状态变化，需要 validate_state_delta 校验
                "visible_context": ctx["visible_context"],  # visible_context = 玩家可见上下文，用于判断输出是否超出玩家认知
                "keeper_only_context": ctx["keeper_only_context"],  # keeper_only_context = 隐藏信息，用于检测叙事是否泄露秘密
                "turn_plan": plan["turn_plan"],  # turn_plan = 原始计划，用于 Reflection 判断执行是否偏离计划
                "story_state": ctx["story_state"],  # story_state = 当前长期状态，用于校验 state_delta 是否能被合法合并
                "react_trace": exec_data["react_trace"],  # react_trace = ReAct 执行轨迹，用于审计 Tool/Skill 调用链
                "tool_observations": exec_data["tool_observations"],  # tool_observations = Tool 输出列表，GuardAgent 可据此检查依据是否充分
                "skill_results": exec_data["skill_results"],  # skill_results = Skill 执行结果，用于 Reflection 回看候选裁定
                "resolution": exec_data["resolution"],  # resolution = 综合裁定结果，用于判断叙事是否与裁定一致
                "player_input": player_input,  # player_input = 玩家原始输入，判断回应是否答题
                "session": ctx["session"],  # session = 当前会话对象，提供已知线索、物品、位置等状态
                "character": ctx["character"],  # character = 当前角色对象，辅助检查 HP/SAN 等状态叙述
                "intent": ctx["intent"],  # intent = 结构化意图，辅助判断行动目标是否一致
                "adjudication": exec_data["adjudication"],  # adjudication = 规则裁定依据，用于校验叙事是否尊重规则
                "skill_checks": exec_data["skill_checks"],  # skill_checks = 技能检定结果，防止叙事篡改骰点结果
                "sanity_checks": exec_data["sanity_checks"],  # sanity_checks = 理智检定结果，防止 SAN 变化不一致
                "scenario_context": ctx["scenario_context"],  # scenario_context = 剧本上下文，用于检查剧情一致性
                "entity_context": ctx["entity_context"],  # entity_context = 实体上下文，用于检查地点/NPC/物品引用
                "clue_context": ctx["clue_context"],  # clue_context = 线索上下文，用于检查未发现线索是否被泄露
                "memory_context": ctx["memory_context"],  # memory_context = 会话记忆，用于检查前后矛盾
                "rule_context": ctx["rule_context"],  # rule_context = 规则上下文，用于检查规则解释是否合理
                "debug_emit": debug_emit,  # debug_emit = 调试事件回调
                "trace_recorder": trace_recorder,  # trace_recorder = 监控记录器
            }
        )
        guard_result = self.guard_agent.run(guard_envelope)  # 16. 运行 GuardAgent：生成 safe_narration、safe_options、validated_delta 和修复建议
        guard_data = guard_result["payload"]  # 17. 取出 GuardAgent 输出，后续决定是否进入修复循环

        # === Phase 5.5: Repair Loop（最多 2 次）===
        # 当 GuardAgent 判定需要修复时，根据修复类型执行相应操作
        # repair_text: 让 NarratorAgent 重新生成叙事
        # repair_state_delta: 修复状态增量
        # ask_clarification/fail_safe: 构造追问/兜底响应，不再继续修复
        # replan_once: 重新规划一次
        repair_attempts = 0  # 18. 初始化修复次数计数器，避免 GuardAgent 反复要求修复导致无限循环
        while guard_data["needs_repair"] and repair_attempts < 2:  # 19. 只要仍需修复且未超过上限，就继续修复
            repair_attempts += 1  # 20. 记录本次修复尝试次数
            emit_debug(
                debug_emit,  # debug_emit = 调试事件回调
                phase="agent_node",  # phase = agent_node，表示这是 Agent 节点级事件
                name="Supervisor",  # name = Supervisor，表示修复循环由调度器触发
                status="start",  # status = start，表示修复开始
                message=f"触发修复循环 #{repair_attempts}，类型 {guard_data['repair_type']}。",  # message = 展示当前修复次数和类型
            )

            repair_type = guard_data["repair_type"]  # 21. 读取 GuardAgent 给出的修复类型
            if repair_type == "repair_text":  # 22a. 文本修复：叙事不合格，需要 NarratorAgent 重新生成
                # 让 NarratorAgent 重新生成叙事，注入修复指令
                repair_envelope = AgentMessage(  # repair_envelope = 修复阶段输入信封，复用原 Narrator 输入
                    payload={**narr_envelope["payload"], "repair_instruction": guard_data["repair_instruction"], "trace_recorder": trace_recorder}  # repair_instruction = GuardAgent 给出的具体修复要求
                )
                narr_result = self.narrator_agent.repair(repair_envelope)  # 23a. 调用 NarratorAgent.repair 重新生成叙事和状态候选
                narr_data = narr_result["payload"]  # 24a. 用修复后的叙事结果替换原 narr_data
            elif repair_type == "repair_state_delta":  # 22b. 状态修复：叙事可能可用，但 state_delta 需要被修正
                # 合并修复的状态增量并重新校验
                merged = {**narr_data["state_delta"], **guard_data.get("repair_state_delta", {})}  # 23b. 将原状态增量和 GuardAgent 建议的修复增量合并
                validated_delta, _ = validate_state_delta(merged, ctx["story_state"])  # 24b. 再跑一次确定性状态校验，确保修复后的 delta 合法
                narr_data = {**narr_data, "state_delta": validated_delta}  # 25b. 更新 narr_data 中的 state_delta
            elif repair_type in ("ask_clarification", "fail_safe"):  # 22c. 追问或兜底：不再继续复杂修复，直接给安全回应
                # 追问或兜底：构造固定响应，不再继续修复循环
                narr_data["narration"] = guard_data["repair_instruction"] or "这个行动还需要更多明确目标。你可以说明想调查的对象、使用的物品或采取的方式。"  # 23c. 使用 GuardAgent 的修复文本，没有则用默认追问
                narr_data["options"] = ["说明具体目标", "换一种调查方式", "回顾已知线索", "自定义行动"]  # 24c. 给玩家提供安全的下一步选项
                narr_data["state_delta"] = {"clarification": True, "time_cost_minutes": 0, "danger_delta": 0}  # 25c. 标记为澄清回合，不推进时间和危险
                break  # 26c. 一旦进入 clarify/fail_safe，不再继续修复
            elif repair_type == "replan_once":  # 22d. 重新规划：当前计划可能有问题，最多重新调用 PlannerAgent 一次
                # 重新规划一次：重新调用 PlannerAgent
                plan_envelope = AgentMessage(  # 23d. 重新构造计划信封，输入仍来自 ContextAgent
                    payload={  # payload = PlannerAgent 所需输入
                        "visible_context": ctx["visible_context"],  # visible_context = 玩家可见上下文
                        "intent": ctx["intent"],  # intent = 原始结构化意图
                        "player_input": player_input,  # player_input = 玩家原始输入
                        "debug_emit": debug_emit,  # debug_emit = 调试事件回调
                        "trace_recorder": trace_recorder,  # trace_recorder = 监控记录器
                    }
                )
                plan_result = self.planner_agent.run(plan_envelope)  # 24d. 再次运行 PlannerAgent 生成新计划
                plan = plan_result["payload"]  # 25d. 用新计划覆盖旧计划
                if plan["needs_clarification"]:  # 26d. 如果新计划认为仍需澄清，则进入追问落库分支
                    return self._clarify_and_commit(  # 27d. 追问并落库，结束本回合
                        db, ctx["session"], ctx["character"], player_input, ctx["intent"],
                        plan["turn_plan"], ctx["story_state"], debug_emit, trace_recorder
                    )
                # 简化处理：直接 break 避免无限递归
                break  # 28d. 当前实现不递归重跑完整执行链，避免复杂循环

            # 修复后重新 Guard 校验
            guard_envelope = AgentMessage(  # 29. 修复后重新构造 Guard 信封
                payload={**guard_envelope["payload"], **narr_data}  # 30. 用修复后的 narration/options/state_delta 覆盖原始输入
            )
            guard_result = self.guard_agent.run(guard_envelope)  # 31. 重新运行 GuardAgent，确认修复是否通过
            guard_data = guard_result["payload"]  # 32. 更新 guard_data，while 条件会据此决定是否继续修复

        # === Phase 6: 最终落库 ===
        # 使用 GuardAgent 清洗后的安全叙事和校验后的状态增量
        emit_debug(debug_emit, phase="stream", name="supervisor", status="success", message="Supervisor 完成调度，准备落库。")  # 33. 通知调试面板：Agent 链路完成，即将写数据库
        return self._commit_state(  # 34. 进入集中落库方法，真正更新会话、线索、物品、回合日志和记忆
            db=db,  # db = 当前数据库会话
            session=ctx["session"],  # session = 当前游戏会话 ORM 对象
            character=ctx["character"],  # character = 当前角色 ORM 对象
            player_input=player_input,  # player_input = 玩家原始输入
            intent=ctx["intent"],  # intent = 结构化意图
            turn_plan=plan["turn_plan"],  # turn_plan = 最终使用的回合计划
            plan_validation=plan["plan_validation"],  # plan_validation = PlannerAgent 白名单校验结果
            react_trace=exec_data["react_trace"],  # react_trace = ExecutorAgent 的 ReAct 执行轨迹
            tool_observations=exec_data["tool_observations"],  # tool_observations = Tool 调用观察列表
            skill_results=exec_data["skill_results"],  # skill_results = Skill 执行结果列表
            reflection_report=guard_data["reflection_report"],  # reflection_report = GuardAgent 的 LLM 自检报告
            final_guardrail_report=guard_data["final_guardrail_report"],  # final_guardrail_report = 最终状态校验和防剧透报告
            adjudication=exec_data["adjudication"],  # adjudication = 规则裁定结果
            dice_results=exec_data["dice_results"],  # dice_results = 所有骰点结果
            skill_checks=exec_data["skill_checks"],  # skill_checks = 技能检定结果
            sanity_checks=exec_data["sanity_checks"],  # sanity_checks = 理智检定结果
            resolution=exec_data["resolution"],  # resolution = 综合裁定摘要
            narration=guard_data["safe_narration"],  # narration = GuardAgent 清洗后的玩家可见叙事
            options=guard_data["safe_options"],  # options = GuardAgent 清洗后的玩家可选行动
            state_delta=guard_data["validated_delta"],  # state_delta = 经过确定性校验后的状态增量
            story_state=ctx["story_state"],  # story_state = 本回合开始时的长期剧情状态
            needs_image=narr_data.get("needs_image", False),  # needs_image = NarratorAgent 判断是否需要生成场景图
            image_scene_type=narr_data.get("image_scene_type", ""),  # image_scene_type = 场景图类型，影响图片宽高比
            debug_emit=debug_emit,  # debug_emit = 调试事件回调
            trace_recorder=trace_recorder,  # trace_recorder = Agent 监控记录器
        )

    def _clarify_and_commit(
        self,
        db: Session,
        session: models.GameSession,
        character: models.Character,
        player_input: str,
        intent: dict[str, Any],
        turn_plan: dict[str, Any],
        story_state: dict[str, Any],
        debug_emit: DebugEmitter | None,
        trace_recorder: AgentTraceRecorder | None,
    ) -> dict[str, Any]:
        """处理追问回合（_clarify_and_commit = 追问并落库）。

        【中文名称】追问并落库

        【功能说明】
        当 PlannerAgent 判定玩家输入太模糊、需要追问时调用。
        不执行 Skill 和规则检定，直接构造一个追问问题作为叙事，
        然后调用 _commit_state 落库。

        【追问机制】
        1. PlannerAgent 发现玩家输入模糊 → needs_clarification=True
        2. Supervisor 调用 _clarify_and_commit
        3. 生成追问问题（如"你想具体调查哪里？"）
        4. 落库（不消耗游戏时间）
        5. 下一轮 ContextAgent 会识别追问回合，正确理解玩家回答

        【参数说明】
        - db: 数据库会话
        - session: 游戏会话对象
        - character: 角色对象
        - player_input: 玩家输入
        - intent: 解析后的意图
        - turn_plan: 回合计划（包含 clarification_question）
        - story_state: 剧情状态
        - debug_emit: 调试事件发射器
        - trace_recorder: Agent 监控记录器

        【返回值】
        - dict: 面向 API 层的回合结果字典，字段兼容旧版 KeeperState
        """
        question = turn_plan.get("clarification_question") or intent.get("clarification_question") or "你想具体调查哪里，或以什么方式行动？"
        narration = str(question)  # 叙事就是追问问题
        options = ["检查附近明显可疑之处", "询问同伴的看法", "观察环境", "自定义行动"]
        state_delta = {"clarification": True, "time_cost_minutes": 0, "danger_delta": 0}  # 追问回合不消耗时间

        audit = {
            "意图": intent,
            "裁定": {},
            "偏离剧情": {},
            "检索": {},
            "状态校验": {},
            "防剧透": {},
        }

        return self._commit_state(
            db=db,
            session=session,
            character=character,
            player_input=player_input,
            intent=intent,
            turn_plan=turn_plan,
            plan_validation={},
            react_trace=[],
            tool_observations=[],
            skill_results=[],
            reflection_report={"result": "pass", "reason": "澄清回合，跳过 Reflection。"},
            final_guardrail_report={},
            adjudication={},
            dice_results=[],
            skill_checks=[],
            sanity_checks=[],
            resolution={},
            narration=narration,
            options=options,
            state_delta=state_delta,
            story_state=story_state,
            needs_image=False,
            image_scene_type="",
            debug_emit=debug_emit,
            trace_recorder=trace_recorder,
        )

    def _commit_state(
        self,
        db: Session,
        session: models.GameSession,
        character: models.Character,
        player_input: str,
        intent: dict[str, Any],
        turn_plan: dict[str, Any],
        plan_validation: dict[str, Any],
        react_trace: list[dict[str, Any]],
        tool_observations: list[dict[str, Any]],
        skill_results: list[dict[str, Any]],
        reflection_report: dict[str, Any],
        final_guardrail_report: dict[str, Any],
        adjudication: dict[str, Any],
        dice_results: list[dict[str, Any]],
        skill_checks: list[dict[str, Any]],
        sanity_checks: list[dict[str, Any]],
        resolution: dict[str, Any],
        narration: str,
        options: list[str],
        state_delta: dict[str, Any],
        story_state: dict[str, Any],
        needs_image: bool,
        image_scene_type: str,
        debug_emit: DebugEmitter | None,
        trace_recorder: AgentTraceRecorder | None = None,
    ) -> dict[str, Any]:
        """唯一集中落库方法（_commit_state = 提交状态/保存存档）。

        【中文名称】提交状态 / 保存存档

        【功能说明】
        这是整个回合流程的最后一步。将本回合的所有结果写入数据库。
        不管是正常回合还是追问回合，最终都通过这个方法落库。

        【落库的 10 个步骤（按顺序）】
        1. 应用理智变化 → 更新 character.san_current
        2. 应用状态增量 → 更新地点、场景、时间、危险等级
        3. 保存元数据 → 意图、裁定、审计记录写入 session.state
        4. 处理线索发现 → 新线索写入 clues 表（自动去重）
        5. 处理物品变化 → 物品增减写入 inventory_items 表
        6. 更新线索计数器 → 用于判断是否给玩家线索提示
        7. 生成会话摘要 → 用 LLM 生成摘要，维护长期记忆
        8. 写 TurnLog → 记录本回合完整信息到 turn_logs 表
        9. 写入向量记忆 → 将回合记忆写入 ChromaDB
        10. 提交数据库事务 → db.commit()

        【参数说明】
        - db: 数据库会话
        - session: 游戏会话对象
        - character: 角色对象
        - player_input: 玩家输入
        - intent: 解析后的意图
        - turn_plan: 回合计划
        - plan_validation: 计划校验结果
        - react_trace: ReAct 执行轨迹
        - tool_observations: Tool 观察结果
        - skill_results: Skill 执行结果
        - reflection_report: Reflection 自检报告
        - final_guardrail_report: 综合守卫报告
        - adjudication: 规则裁定
        - dice_results: 骰点结果
        - skill_checks: 技能检定结果
        - sanity_checks: 理智检定结果
        - resolution: 综合裁定结果
        - narration: 清洗后的安全叙事
        - options: 清洗后的安全选项
        - state_delta: 校验后的状态增量
        - story_state: 剧情状态
        - needs_image: 是否需要配图
        - image_scene_type: 配图场景类型
        - debug_emit: 调试事件发射器
        - trace_recorder: Agent 监控记录器

        【返回值】
        - dict: 面向 API 层的回合结果字典，字段兼容旧版 KeeperState，供 API 层使用
        """
        # 这是整个回合里最值得“逐行精读”的方法之一。
        # 前面的 Agent 更偏“思考与生成”，这里只有一个核心问题：
        # “这回合结束后，哪些变化会真正永久保存？”
        #
        # 如果你是学生，建议读这段时持续带着两个问题：
        # 1. 哪些数据只是本回合临时使用，哪些数据会写入长期状态？
        # 2. 某个字段最终是写进 PostgreSQL，还是写进 Chroma 记忆库？
        # 1. 计算本回合序号：已有日志数量 + 1，因此第一回合为 1。
        turn_index = len(session.turn_logs) + 1  # turn_index = 本回合编号，会写入 TurnLog.discovered_turn 等字段
        # 2. 准备一个列表收集本回合新增或复用的线索对象，最后会返回给 API 层。
        discovered: list[models.Clue] = []  # discovered = 本回合发现的 Clue ORM 对象列表

        # ===== 1. 应用理智变化 =====
        # 3. 遍历所有理智检定结果；通常一回合最多一个，但用循环兼容多个结果。
        for san in sanity_checks:
            # 4. 将角色当前 SAN 更新为检定后的 san_after；真正提交数据库发生在 db.commit()。
            character.san_current = int(san["san_after"])  # character.san_current = 理智检定后的当前理智值

        # ===== 2. 应用状态增量 =====
        # 将 state_delta 合并到 session.state，更新地点、场景、时间、危险等级
        # 这里是“结构化状态推进”的关键点：
        # Narrator/Guard 之前产出的 state_delta 还是“本回合变化说明”，
        # apply_turn_delta 之后，它才真正变成 session.state 里的长期世界状态。
        # 5. 调用 story_state.apply_turn_delta，将本回合 delta 合并进长期剧情状态。
        session.state = apply_turn_delta(  # session.state = 合并后的长期 story_state JSON
            story_state,  # story_state = 回合开始前的长期剧情状态
            state_delta,  # state_delta = GuardAgent 校验后的本回合状态增量
            session.current_location,  # current_location = 回合开始时数据库记录的当前地点
            session.current_scene,  # current_scene = 回合开始时数据库记录的当前场景
            session.current_time,  # current_time = 回合开始时数据库记录的游戏内时间
        )
        # 6. 从 session.state 中安全取出“场景”分区；如果结构异常则回退空字典。
        scene_state = session.state.get("场景", {}) if isinstance(session.state.get("场景"), dict) else {}  # scene_state = 场景状态分区
        # 7. 如果场景状态里有合法的当前地点，就同步到 GameSession.current_location。
        if isinstance(scene_state.get("当前地点"), str) and scene_state["当前地点"]:
            session.current_location = scene_state["当前地点"][:200]  # current_location = 当前地点，限制长度避免数据库字段过长
        # 8. 如果场景状态里有合法的当前场景，就同步到 GameSession.current_scene。
        if isinstance(scene_state.get("当前场景"), str) and scene_state["当前场景"]:
            session.current_scene = scene_state["当前场景"][:200]  # current_scene = 当前场景，限制长度避免数据库字段过长
        # 9. 从场景状态同步当前时间；缺失时保留原 session.current_time。
        session.current_time = session.state.get("场景", {}).get("当前时间", session.current_time)  # current_time = 推进后的游戏内时间
        # 10. 从剧情状态同步危险等级；缺失时保留原 session.danger_level。
        session.danger_level = int(session.state.get("剧情", {}).get("敌对势力警觉", session.danger_level))  # danger_level = 敌对势力警觉等级

        # ===== 3. 保存元数据 =====
        # 将本回合的关键数据保存到 session.state，供调试和回溯使用
        # 注意这里保存了很多 last_* 字段。
        # 它们不一定都是“剧情世界的一部分”，更多是为了前端展示、调试面板和学习排查方便。
        # 11. 用字典展开复制当前状态，再附加 last_* 调试字段。
        session.state = {  # session.state = 原 story_state + 本回合调试元数据
            **session.state,  # 保留已有长期剧情状态
            "last_intent": intent,  # last_intent = 最近一次玩家意图
            "last_delta": state_delta,  # last_delta = 最近一次状态增量
            "last_audit": {  # last_audit = 回合审计摘要，前端调试面板会读取
                "意图": intent,  # 意图 = ContextAgent 输出的结构化意图
                "裁定": adjudication,  # 裁定 = ExecutorAgent/RuleCheckTool 的规则裁定
                "偏离剧情": resolution.get("偏离剧情", {}),  # 偏离剧情 = guardrails 对剧情偏离的判断
                "检索": {  # 检索 = 本回合上下文召回数量摘要
                    "剧本片段数": len(session.state.get("last_scenario_context", [])),  # 剧本片段数 = 最近剧本上下文数量
                    "结构化实体数": len(session.state.get("last_entity_context", [])),  # 结构化实体数 = 最近实体上下文数量
                    "线索索引数": len(session.state.get("last_clue_context", [])),  # 线索索引数 = 最近线索候选数量
                    "会话记忆数": len(session.state.get("last_memory_context", [])),  # 会话记忆数 = 最近记忆召回数量
                    "规则片段数": len(session.state.get("last_rule_context", [])),  # 规则片段数 = 最近规则片段数量
                },
                "状态校验": final_guardrail_report.get("validation", {}),  # 状态校验 = validate_state_delta 的报告
                "防剧透": final_guardrail_report.get("leak", {}),  # 防剧透 = 输出清洗报告
            },
            "last_options": options,  # last_options = 最近一次给玩家的行动选项
            "last_turn_plan": turn_plan,  # last_turn_plan = 最近一次 PlannerAgent 生成的计划
            "last_react_trace": react_trace,  # last_react_trace = 最近一次 ExecutorAgent 执行轨迹
            "last_tool_observations": tool_observations,  # last_tool_observations = 最近一次 Tool 观察结果
            "last_reflection_report": reflection_report,  # last_reflection_report = 最近一次 Reflection 自检报告
            "last_final_guardrail_report": final_guardrail_report,  # last_final_guardrail_report = 最近一次最终守卫报告
        }

        # ===== 4. 处理线索发现 =====
        # 将 LLM 生成的线索写入数据库（去重：已存在的线索不重复创建）
        # 12. 遍历状态增量中的 generated_clues，准备写入 Clue 表。
        for clue_payload in state_delta.get("generated_clues", []):
            # 13. 如果某个线索不是 dict，说明格式不合规，直接跳过避免写坏数据。
            if not isinstance(clue_payload, dict):
                continue  # 跳过非法线索载荷
            # 14. 生成线索唯一键；优先 clue_key，其次 name，最后使用默认 clue。
            clue_key = safe_key(str(clue_payload.get("clue_key") or clue_payload.get("name") or "clue"))  # clue_key = 幂等去重键
            # 15. 查询当前会话是否已经有同 key 线索，避免重复创建。
            existing = db.query(models.Clue).filter(  # existing = 已存在的同 key 线索或 None
                models.Clue.session_id == session.id,  # 限定当前会话
                models.Clue.clue_key == clue_key,  # 限定线索 key
            ).one_or_none()  # one_or_none = 找到一条或没有，重复会抛异常帮助暴露数据问题
            # 16. 如果线索已存在，就复用旧对象，并加入本回合 discovered 列表。
            if existing:
                discovered.append(existing)  # discovered += 已存在的线索
                continue  # 不重复创建数据库记录
            # 17. 创建新的 Clue ORM 对象，等待后续 db.add/db.commit。
            clue = models.Clue(  # clue = 新发现线索 ORM 对象
                session_id=session.id,  # session_id = 当前会话 ID
                clue_key=clue_key,  # clue_key = 幂等唯一键
                name=str(clue_payload.get("name") or clue_key),  # name = 线索展示名称
                content=str(clue_payload.get("content") or "玩家发现了一条新的线索。"),  # content = 线索正文，缺失时给默认描述
                source_location=clue_payload.get("source_location") or session.current_location,  # source_location = 发现地点
                discovered_turn=turn_index,  # discovered_turn = 发现回合编号
                metadata_={"来源": "守秘人代理"},  # metadata_ = 线索元数据，记录来源
            )
            # 18. 把新线索加入数据库会话，等待统一提交。
            db.add(clue)  # db.add = 将 clue 标记为待插入
            # 19. 把新线索加入本回合发现列表，API 返回时会序列化给前端。
            discovered.append(clue)  # discovered += 新线索

        # ===== 5. 处理物品变化 =====
        # 20. 将 state_delta 中的 inventory_changes 应用到会话物品栏。
        inventory_results = apply_inventory_changes(  # inventory_results = 物品变更处理结果
            db,  # db = 数据库会话
            session,  # session = 当前游戏会话
            state_delta.get("inventory_changes", []),  # inventory_changes = 本回合候选物品增减列表
            turn_index,  # turn_index = 当前回合编号，用于记录来源
        )
        # 21. 如果有物品变更被应用或忽略，就把结果写回 state_delta 和 session.state，便于调试。
        if inventory_results.get("applied") or inventory_results.get("ignored"):
            state_delta["inventory_results"] = inventory_results  # state_delta.inventory_results = 物品处理明细
            session.state["last_inventory_changes"] = inventory_results  # session.state.last_inventory_changes = 最近物品变更明细

        # ===== 6. 更新线索计数器 =====
        # 用于判断是否应该给玩家提供线索提示
        # 22. 根据本回合是否发现新线索，更新“连续无新线索回合”计数器。
        update_no_clue_counter(session.state, bool(discovered))  # 有新线索则归零，否则 +1

        # ===== 7. 生成并应用会话摘要 =====
        # 摘要用于维护会话的长期记忆，避免上下文过长
        # 23. 构造摘要输入：摘要模块不需要完整运行态，只需要玩家输入、叙事、状态变化和剧情状态。
        summary_state = {  # summary_state = 生成会话摘要所需的最小上下文
            "player_input": player_input,  # player_input = 本回合玩家原始输入
            "narration": narration,  # narration = 本回合最终玩家可见叙事
            "state_delta": state_delta,  # state_delta = 本回合最终状态增量
            "story_state": story_state,  # story_state = 回合开始时的长期剧情状态
        }
        # 24. 生成本回合摘要；内部可能调用 LLM，也可能使用兜底摘要。
        summary = build_turn_summary(session, summary_state, self.llm)  # summary = 本回合摘要文本/结构
        # 25. 将摘要应用回 session，例如更新 session.summary 或状态中的摘要字段。
        apply_summary_to_session(session, summary_state, summary)  # session.summary = 更新后的会话摘要

        # ===== 8. 写 TurnLog =====
        # 记录本回合的完整信息，供调试和回溯使用
        # 可以把 TurnLog 理解成“回合级审计日志”：
        # 它不会替代 session.state，但能帮你回看“第 N 回合当时为什么这样判断”。
        # 26. 创建 TurnLog ORM 对象，记录本回合的完整输入、裁定、输出和审计数据。
        log = models.TurnLog(  # log = 本回合日志对象，提交后写入 turn_logs 表
            session_id=session.id,  # session_id = 当前会话 ID
            turn_index=turn_index,  # turn_index = 本回合编号
            player_input=player_input,  # player_input = 玩家原始输入
            intent=intent,  # intent = 结构化意图
            retrieval={  # retrieval = 回合调试与审计包，字段名沿用旧版结构
                "剧本": [],  # 剧本 = 预留字段，当前详细上下文主要在 Tool 观察中
                "结构化实体": [],  # 结构化实体 = 预留字段
                "线索索引": [],  # 线索索引 = 预留字段
                "会话记忆": [],  # 会话记忆 = 预留字段
                "规则": [],  # 规则 = 预留字段
                "裁定": adjudication,  # 裁定 = 规则裁定结果
                "审计": session.state.get("last_audit", {}),  # 审计 = 本回合审计摘要
                "回合计划": turn_plan,  # 回合计划 = PlannerAgent 生成的计划
                "计划校验": plan_validation,  # 计划校验 = Tool/Skill 白名单校验结果
                "ReAct轨迹": react_trace,  # ReAct轨迹 = ExecutorAgent 执行过程
                "Tool观察": tool_observations,  # Tool观察 = ToolObservation 列表
                "Skill结果": skill_results,  # Skill结果 = SkillResult 列表
                "Reflection": reflection_report,  # Reflection = GuardAgent 自检报告
                "最终校验": final_guardrail_report,  # 最终校验 = 状态校验和防剧透报告
            },
            dice_results=dice_results,  # dice_results = 骰点结果列表
            keeper_response=narration,  # keeper_response = 最终守秘人叙事
            state_delta=state_delta,  # state_delta = 本回合状态增量
            image_url=None,  # image_url = 图片 URL，图片生成在 API 层后置处理
            image_metadata={  # image_metadata = 图片生成相关元信息
                "needs_image": needs_image,  # needs_image = 是否需要生成图片
                "scene_type": image_scene_type,  # scene_type = 图片场景类型
                "prompt_raw": "",  # prompt_raw = 原始图片提示词，后续图片生成时填充
                "prompt_optimized": "",  # prompt_optimized = 优化后图片提示词，后续图片生成时填充
            },
        )
        # 27. 把 TurnLog 加入数据库会话，等待统一提交。
        db.add(log)  # db.add = 将 log 标记为待插入
        # 28. 提前 flush，让数据库分配 log.id，后面的 trace_recorder 可以记录 turn_log_id。
        db.flush()  # db.flush = 发送 SQL 但不提交事务
        # 29. 如果启用了 Agent 监控，就记录 commit_state 这一步的输入输出摘要。
        if trace_recorder:
            trace_recorder.record(  # trace_recorder.record = 写入一条监控步骤
                agent_name="KeeperSupervisor",  # agent_name = 记录来源组件
                step_name="commit_state",  # step_name = 当前步骤名称
                phase="commit",  # phase = 提交阶段
                status="success",  # status = 本步骤成功
                input_payload={  # input_payload = 本步骤输入摘要
                    "session_id": session.id,  # session_id = 当前会话 ID
                    "turn_index": turn_index,  # turn_index = 当前回合编号
                    "player_input": player_input,  # player_input = 玩家原始输入
                    "intent": intent,  # intent = 结构化意图
                    "turn_plan": turn_plan,  # turn_plan = 最终回合计划
                    "state_delta": state_delta,  # state_delta = 最终状态增量
                },
                output_payload={  # output_payload = 本步骤输出摘要
                    "turn_log_id": log.id,  # turn_log_id = 刚 flush 出来的回合日志 ID
                    "narration": narration,  # narration = 最终叙事
                    "options": options,  # options = 最终行动选项
                    "discovered_clues": [clue.name for clue in discovered],  # discovered_clues = 本回合线索名称列表
                    "needs_image": needs_image,  # needs_image = 是否需要图片
                    "image_scene_type": image_scene_type,  # image_scene_type = 图片场景类型
                },
            )

        # ===== 9. 写入向量记忆 =====
        # 将本回合的关键信息写入 ChromaDB，供后续回合的 RAG 检索使用
        # 这里体现了 PostgreSQL 和 Chroma 的分工：
        # - PostgreSQL 保存结构化真相：会话、线索、地点、回合日志
        # - Chroma 保存“便于语义回忆”的文本记忆，方便后续相似检索
        # 30. 准备要写入 Chroma 的记忆块列表。
        memory_chunks: list[DocumentChunk] = []  # memory_chunks = 待写入 session_memory_chunks 集合的文档块
        # 31. 构建详细回合记忆块，包含玩家输入、叙事、状态变化和裁定。
        mem_chunk = build_session_memory_chunk(session.id, turn_index, {  # mem_chunk = 本回合详细记忆块
            "player_input": player_input,  # player_input = 玩家输入
            "narration": narration,  # narration = 守秘人叙事
            "state_delta": state_delta,  # state_delta = 状态变化
            "adjudication": adjudication,  # adjudication = 规则裁定
        })
        # 32. 如果详细记忆块非空，就加入待写入列表。
        if mem_chunk:
            memory_chunks.append(mem_chunk)  # memory_chunks += 本回合详细记忆
        # 33. 构建摘要记忆块，便于后续通过 RAG 召回高层剧情进展。
        summary_chunk = build_summary_memory_chunk(session.id, turn_index, summary)  # summary_chunk = 本回合摘要记忆块
        # 34. 如果摘要记忆块非空，也加入待写入列表。
        if summary_chunk:
            memory_chunks.append(summary_chunk)  # memory_chunks += 本回合摘要记忆
        # 35. 如果有任何记忆块，就写入 Chroma 的 session_memory_chunks 集合。
        if memory_chunks:
            self.retrieval.upsert_chunks("session_memory_chunks", memory_chunks)  # upsert = 新增或覆盖会话记忆向量

        # ===== 10. 提交数据库事务 =====
        # 36. 提交数据库事务：前面所有 session、character、clue、inventory、log 变化在这里真正落库。
        db.commit()  # db.commit = 提交 PostgreSQL 事务
        # 37. 刷新 session，让 ORM 对象拿到数据库提交后的最新字段。
        db.refresh(session)  # db.refresh(session) = 从数据库重新加载 session
        # 38. 刷新本回合发现的线索，确保 id、created_at 等数据库生成字段可用。
        for clue in discovered:
            db.refresh(clue)  # db.refresh(clue) = 从数据库重新加载 clue

        # 39. 发出最终调试事件，告诉前端调试面板“状态已经成功落库”。
        emit_debug(debug_emit, phase="agent_node", name="commit_state", status="success", message="状态已落库。", metadata={"session_id": session.id, "turn_index": turn_index})

        # 返回面向 API 层的回合结果字典，保留旧 KeeperState 字段名以兼容旧调用。
        # 40. 返回 API 层需要的结果字典；build_action_response 会再把它转成 Pydantic 响应模型。
        return {  # 返回值 = 面向 API 层的回合结果
            "db": db,  # db = 数据库会话，兼容旧 KeeperState 字段
            "session_id": session.id,  # session_id = 当前会话 ID
            "player_input": player_input,  # player_input = 玩家原始输入
            "session": session,  # session = 已刷新后的 GameSession 对象
            "character": character,  # character = 当前角色对象
            "intent": intent,  # intent = 结构化意图
            "turn_plan": turn_plan,  # turn_plan = 最终回合计划
            "plan_validation": plan_validation,  # plan_validation = 计划校验结果
            "react_trace": react_trace,  # react_trace = ReAct 执行轨迹
            "tool_observations": tool_observations,  # tool_observations = Tool 观察列表
            "skill_results": skill_results,  # skill_results = Skill 结果列表
            "reflection_report": reflection_report,  # reflection_report = Reflection 自检报告
            "final_guardrail_report": final_guardrail_report,  # final_guardrail_report = 最终守卫报告
            "adjudication": adjudication,  # adjudication = 规则裁定结果
            "dice_results": dice_results,  # dice_results = 骰点结果
            "skill_checks": skill_checks,  # skill_checks = 技能检定结果
            "sanity_checks": sanity_checks,  # sanity_checks = 理智检定结果
            "resolution": resolution,  # resolution = 综合裁定摘要
            "narration": narration,  # narration = 最终玩家可见叙事
            "options": options,  # options = 最终玩家选项
            "state_delta": state_delta,  # state_delta = 最终状态增量
            "story_state": story_state,  # story_state = 回合开始时的长期状态，兼容旧字段
            "discovered_clues": discovered,  # discovered_clues = 本回合发现线索对象列表
            "needs_clarification": state_delta.get("clarification", False),  # needs_clarification = 本回合是否是追问/澄清
            "visible_context": {},  # visible_context = 兼容旧 KeeperState 字段，当前返回空字典
            "keeper_only_context": {},  # keeper_only_context = 兼容旧 KeeperState 字段，当前返回空字典
            "needs_image": needs_image,  # needs_image = 是否需要后续生成图片
            "image_scene_type": image_scene_type,  # image_scene_type = 图片场景类型
            "image_url": None,  # image_url = 图片 URL，API 层图片生成后可能填充
            "image_prompt_raw": "",  # image_prompt_raw = 原始图片提示词，兼容旧字段
            "image_prompt_optimized": "",  # image_prompt_optimized = 优化图片提示词，兼容旧字段
            "image_metadata": {},  # image_metadata = 图片元数据，API 层图片生成后可能填充
        }
