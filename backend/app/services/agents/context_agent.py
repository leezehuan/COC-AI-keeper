# =============================================================================
# 【ContextAgent：上下文加载与意图解析 Agent】
# =============================================================================
# 这是回合执行流程的第一个 Agent，相当于"战前情报官"。
# 它的工作是在守秘人做出任何决定之前，先把所有需要的信息准备好。
#
# 具体做四件事（按顺序）：
#
# 1. 加载游戏状态（从 PostgreSQL 数据库）
#    - 读取当前会话、角色属性、已发现的线索、物品栏、剧情标记
#    - 读取最近的回合日志，了解之前发生了什么
#    - 类比：就像打开游戏存档，看看现在玩到哪了
#
# 2. 解析玩家意图（用 LLM + 关键词回退）
#    - 把"我划向北岸码头检查脚印"这种自然语言
#    - 变成结构化数据：{action_type: "调查", target: "脚印", skill: "侦查"}
#    - 先让 LLM 解析（精准但可能失败），失败了就用关键词匹配（粗糙但可靠）
#
# 3. 构建上下文（分两套）
#    - visible_context：玩家能看到的信息（地点、场景、物品、已知线索）
#    - keeper_only_context：只有守秘人能看到的信息（完整剧情状态）
#    - 为什么要分两套？防止 LLM 在叙事中不小心泄露秘密
#
# 4. RAG 检索（从 ChromaDB 向量库）
#    - 检索剧本片段（当前场景相关的剧情描述）
#    - 检索实体信息（场景中的 NPC、物品、地点）
#    - 检索线索索引（哪些线索可以被发现）
#    - 检索会话记忆（之前回合发生了什么）
#    - 检索规则片段（相关的游戏规则）
#
# 输出数据供后续所有 Agent 使用，是整个回合的信息基础。
# =============================================================================
from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from sqlalchemy.orm import Session, selectinload

from app import models
from app.services.agents.base import AgentContext, AgentMessage, BaseAgent
from app.services.agents.utils import (
    format_inventory,
    heuristic_intent,
)
from app.services.agent_monitor import AgentTraceRecorder
from app.services.debug_events import DebugEmitter, emit_debug
from app.services.prompt_config import build_intent_prompt
from app.services.story_state import ensure_story_state


class ContextAgent(BaseAgent):
    """上下文加载与意图解析 Agent（可以理解为"战前情报官"）。

    【中文名称】上下文 Agent / 情报收集 Agent

    【功能说明】
    这是回合流程的第一个 Agent。Supervisor 会先调用它，获取当前游戏的
    所有状态信息和相关资料，然后才让其他 Agent 做决策。

    【为什么需要它】
    如果把一次回合比作"医生看病"：
    - ContextAgent = 护士量体温、测血压、问症状（收集基础信息）
    - PlannerAgent = 医生开处方（决定治疗方案）
    - ExecutorAgent = 药剂师配药（执行治疗）
    没有 ContextAgent 收集的信息，后面的 Agent 就像盲人摸象。

    【输入（envelope.payload）】
    - db: Session           → 数据库连接，用来查 PostgreSQL
    - session_id: str       → 游戏会话 ID，确定是哪一局游戏
    - player_input: str     → 玩家输入的自然语言文本
    - debug_emit: DebugEmitter | None → 调试事件发射器（可选）

    【输出（envelope.payload）】
    - session: GameSession          → 游戏会话对象（含角色、线索等关联数据）
    - character: Character          → 当前角色对象
    - story_state: dict             → 剧情状态字典
    - visible_context: dict         → 玩家可见的上下文
    - keeper_only_context: dict     → 守秘人专用的上下文（玩家不可见）
    - intent: dict                  → 解析后的结构化意图
    - scenario_context: list[dict]  → 剧本检索结果
    - entity_context: list[dict]    → 实体检索结果
    - clue_context: list[dict]      → 线索检索结果
    - memory_context: list[dict]    → 记忆检索结果
    - rule_context: list[dict]      → 规则检索结果
    """

    name = "ContextAgent"

    def run(self, envelope: AgentMessage) -> AgentMessage:
        """执行上下文加载与意图解析（run = 运行/执行）。

        【中文名称】运行

        【功能说明】
        ContextAgent 的主入口方法。按顺序执行四步：
        1. 从 PostgreSQL 加载会话数据
        2. 解析玩家意图（LLM + 关键词回退）
        3. 构建可见上下文和守秘人专用上下文
        4. 从 ChromaDB 检索相关资料

        【执行流程】
        db → 查 GameSession（含角色/线索/物品/标记/日志）
          → 解析意图（_parse_intent）
          → 构建 visible_context + keeper_only_context
          → RAG 检索（_retrieve_context）
          → 打包返回 AgentMessage

        【参数说明】
        - envelope: 输入信封，payload 需包含 db、session_id、player_input

        【返回值】
        - AgentMessage: 输出信封，payload 包含所有上下文和检索结果
        """
        payload = envelope.get("payload", {})  # payload = 负载数据：信封中携带的实际内容
        db: Session = payload["db"]  # db = 数据库会话：用于查询 PostgreSQL
        session_id: str = payload["session_id"]  # session_id = 会话ID：标识当前游戏
        player_input: str = payload["player_input"]  # player_input = 玩家输入：玩家发送的自然语言文本
        debug_emit: DebugEmitter | None = payload.get("debug_emit")  # debug_emit = 调试发射器：向前端发送实时调试事件
        trace_recorder: AgentTraceRecorder | None = payload.get("trace_recorder")

        with (trace_recorder.step(agent_name=self.name, step_name="run", phase="context", input_payload=payload) if trace_recorder else null_trace_step()) as trace_step:
            result = self._run_impl(db, session_id, player_input, debug_emit, trace_recorder)
            trace_step["output"] = result
            return result

    def _run_impl(
        self,
        db: Session,
        session_id: str,
        player_input: str,
        debug_emit: DebugEmitter | None,
        trace_recorder: AgentTraceRecorder | None,
    ) -> AgentMessage:
        emit_debug(debug_emit, phase="agent_node", name="ContextAgent", status="start", message="ContextAgent 开始加载状态与检索。")

        # ===== 1. 加载会话与关联数据 =====
        # 使用 selectinload 预加载所有关联数据，避免 N+1 查询问题
        session = (  # session = 游戏会话：从数据库加载的 GameSession ORM 对象
            db.query(models.GameSession)
            .options(
                selectinload(models.GameSession.character),  # 预加载角色
                selectinload(models.GameSession.clues),  # 预加载线索
                selectinload(models.GameSession.inventory_items),  # 预加载物品
                selectinload(models.GameSession.flags),  # 预加载剧情标记
                selectinload(models.GameSession.turn_logs),  # 预加载回合日志
            )
            .filter(models.GameSession.id == session_id)
            .one()
        )
        character = session.character  # character = 角色：当前玩家的 Character ORM 对象
        # 确保剧情状态字典完整，缺失字段用默认值填充
        story_state = ensure_story_state(  # story_state = 剧情状态：结构完整的游戏世界状态字典
            session.state, session.current_location, session.current_scene, session.current_time
        )

        # ===== 2. 解析意图 =====
        # 将玩家自然语言输入解析为结构化意图（action_type、target、skill 等）
        intent = self._parse_intent(session, player_input, debug_emit, trace_recorder)  # intent = 意图：解析后的结构化意图字典

        # ===== 3. 构建可见上下文 =====
        # visible_context：玩家可以看到的信息（地点、场景、物品、已知线索等）
        # keeper_only_context：只有守秘人能看到的信息（完整剧情状态）
        visible_context = {  # visible_context = 可见上下文：玩家能看到的信息（地点/场景/物品/已知线索）
            "current_location": session.current_location,
            "current_scene": session.current_scene,
            "current_time": session.current_time,
            "character_archetype": character.archetype,
            "inventory_text": format_inventory(session.inventory_items),  # 格式化物品列表
            "known_clues": [clue.name for clue in session.clues],  # 已知线索名称列表
            "summary": session.summary,  # 会话摘要
        }
        keeper_only_context = {"story_state": story_state}  # keeper_only_context = 守秘人上下文：含隐藏信息，玩家不可见

        # ===== 4. RAG 检索 =====
        # 从 ChromaDB 向量库中检索相关上下文片段
        scenario_context, entity_context, clue_context, memory_context, rule_context = self._retrieve_context(  # 五个检索结果
            session, player_input, intent, debug_emit, trace_recorder
        )

        emit_debug(
            debug_emit,
            phase="agent_node",
            name="ContextAgent",
            status="success",
            message=(
                f"ContextAgent 完成：地点 {session.current_location}，"
                f"检索 剧本 {len(scenario_context)} 实体 {len(entity_context)} 线索 {len(clue_context)} 记忆 {len(memory_context)} 规则 {len(rule_context)}"
            ),
            metadata={"intent": intent},
        )

        return AgentMessage(
            from_agent=self.name,
            phase="context",
            payload={
                "session": session,
                "character": character,
                "story_state": story_state,
                "visible_context": visible_context,
                "keeper_only_context": keeper_only_context,
                "intent": intent,
                "scenario_context": scenario_context,
                "entity_context": entity_context,
                "clue_context": clue_context,
                "memory_context": memory_context,
                "rule_context": rule_context,
            },
            context_summary=f"会话 {session_id}，地点 {session.current_location}，意图 {intent.get('action_type', '未知')}",
        )

    def _parse_intent(
        self,
        session: models.GameSession,
        player_input: str,
        debug_emit: DebugEmitter | None,
        trace_recorder: AgentTraceRecorder | None,
    ) -> dict[str, Any]:
        """解析玩家意图（_parse_intent = 解析意图）。

        【中文名称】解析意图

        【功能说明】
        把玩家输入的自然语言（如"我检查码头上的脚印"）变成结构化数据。
        采用"双保险"策略：LLM 解析 + 关键词回退。

        【为什么需要两种方式】
        - LLM 解析：精准，能理解复杂表达，但可能因为网络问题失败
        - 关键词回退：简单粗暴，基于关键词匹配，永远不会失败
        - 合并策略：LLM 的非空字段覆盖关键词结果，确保总有可用数据

        【执行流程】
        1. heuristic_intent(player_input) → 得到关键词回退意图
        2. 构建追问上下文（如果上一轮是追问回合）
        3. 构建意图解析提示词
        4. LLM.chat_json(prompt, fallback=回退意图) → 得到 LLM 解析结果
        5. 合并：{**回退, **LLM的非空字段}

        【参数说明】
        - session: 游戏会话对象
        - player_input: 玩家输入的自然语言文本
        - debug_emit: 调试事件发射器

        【返回值】
        - dict: 结构化意图，包含 action_type、target、skill、reason 等字段
        """
        fallback = heuristic_intent(player_input)  # fallback = 回退意图：基于关键词匹配的意图（LLM失败时使用）
        clarification_context = self._build_clarification_context(session)  # clarification_context = 追问上下文：上一轮追问信息
        prompt = build_intent_prompt(session.current_location, session.current_scene, player_input, clarification_context)  # prompt = 提示词：发给LLM的意图解析指令
        trace_input = {
            "session_id": session.id,
            "current_location": session.current_location,
            "current_scene": session.current_scene,
            "player_input": player_input,
            "clarification_context": clarification_context,
            "prompt": prompt,
            "fallback": fallback,
        }
        try:
            with (trace_recorder.step(agent_name=self.name, step_name="parse_intent", phase="agent_step", input_payload=trace_input) if trace_recorder else null_trace_step()) as trace_step:
                parsed = self.context.llm.chat_json(prompt, fallback=fallback)  # parsed = LLM解析结果：LLM返回的结构化意图
                trace_step["output"] = parsed
        except Exception as exc:
            emit_debug(debug_emit, phase="agent_step", name="parse_intent", status="error", message=str(exc)[:500])
            parsed = fallback  # LLM 失败时使用回退
        if not isinstance(parsed, dict):
            parsed = fallback  # 非字典结果使用回退
        # 合并：LLM 的非空字段覆盖启发式结果
        parsed = {**fallback, **{k: v for k, v in parsed.items() if v is not None}}
        return parsed

    def _build_clarification_context(self, session: models.GameSession) -> str:
        """构建追问上下文（_build_clarification_context = 构建追问上下文）。

        【中文名称】构建追问上下文

        【功能说明】
        检查上一轮是否是追问回合。如果是，生成一段提示文本，
        告诉 LLM"玩家现在的输入是对上一轮追问的回答"，帮助 LLM 正确理解。

        【追问机制解释】
        当玩家输入模糊时（如只说"我调查一下"），PlannerAgent 会标记
        needs_clarification=True，守秘人会追问"你想调查什么？"。
        下一轮玩家回答时，ContextAgent 需要知道这是对追问的回答，
        而不是一个全新的行动。

        【参数说明】
        - session: 游戏会话对象

        【返回值】
        - str: 追问上下文文本。如果不是追问回合，返回空字符串 ""
        """
        if not session.turn_logs:
            return ""
        latest_log = max(session.turn_logs, key=lambda log: log.turn_index)  # latest_log = 最新日志：最近一条回合日志
        intent = latest_log.intent if isinstance(latest_log.intent, dict) else {}
        if not intent.get("needs_clarification"):
            return ""  # 上一轮不是追问回合
        return (
            f"【上一轮是追问回合】\n"
            f"玩家原动作：{latest_log.player_input}\n"
            f"系统追问：{intent.get('clarification_question', '')}\n"
            f"请结合以上内容，将本轮玩家输入视为对追问的回答，推断完整意图。"
        )

    def _retrieve_context(
        self,
        session: models.GameSession,
        player_input: str,
        intent: dict[str, Any],
        debug_emit: DebugEmitter | None,
        trace_recorder: AgentTraceRecorder | None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        """RAG 检索上下文（_retrieve_context = 检索上下文）。

        【中文名称】检索上下文

        【功能说明】
        从 ChromaDB 向量数据库中检索与当前回合相关的资料。
        用当前地点、场景、玩家输入、意图目标拼成一个查询字符串，
        分别查询 5 个不同的 collection（集合）。

        【什么是 RAG】
        RAG = Retrieval-Augmented Generation（检索增强生成）。
        简单说：在让 LLM 回答问题之前，先从资料库中找出相关内容，
        和问题一起喂给 LLM。这样 LLM 的回答更准确、更有依据。

        【检索的 5 个集合】
        1. scenario_chunks（剧本片段）→ 当前场景的剧情描述，取 6 条
        2. scenario_entities（实体信息）→ NPC、物品、地点等，取 4 条
        3. clue_index（线索索引）→ 可发现的线索，取 4 条
        4. session_memory_chunks（会话记忆）→ 之前回合的记录，取 3 条
        5. rule_chunks（规则片段）→ 相关游戏规则，取 3 条

        【容错设计】
        每个集合的查询都包在 try/except 中。
        如果某个集合查询失败，不会影响其他集合的查询。
        这保证了即使 ChromaDB 部分出问题，系统仍能继续运行。

        【参数说明】
        - session: 游戏会话对象
        - player_input: 玩家输入文本
        - intent: 解析后的意图
        - debug_emit: 调试事件发射器

        【返回值】
        - tuple[5个list]: 按顺序返回 (剧本, 实体, 线索, 记忆, 规则) 的检索结果
        """
        query = " ".join([  # query = 检索查询：拼接地点/场景/玩家输入/目标/技能
            session.current_location,  # 当前地点
            session.current_scene,  # 当前场景
            player_input,  # 玩家输入
            str(intent.get("target", "")),  # 行动目标
            str(intent.get("skill", "")),  # 使用技能
        ])
        emit_debug(debug_emit, phase="agent_step", name="retrieve_context", status="start", message="开始检索剧本、规则与会话记忆。", metadata={"query": query})

        with (trace_recorder.step(agent_name=self.name, step_name="retrieve_context", phase="agent_step", input_payload={"session_id": session.id, "query": query, "intent": intent}) if trace_recorder else null_trace_step()) as trace_step:
            result = self._retrieve_context_impl(session, query)
            trace_step["output"] = {
                "scenario_context": result[0],
                "entity_context": result[1],
                "clue_context": result[2],
                "memory_context": result[3],
                "rule_context": result[4],
            }

        scenario_context, entity_context, clue_context, memory_context, rule_context = result
        emit_debug(
            debug_emit,
            phase="agent_step",
            name="retrieve_context",
            status="success",
            message=(
                f"检索完成：剧本 {len(scenario_context)}、实体 {len(entity_context)}、"
                f"线索 {len(clue_context)}、记忆 {len(memory_context)}、规则 {len(rule_context)}。"
            ),
            metadata={
                "scenario_context": scenario_context,
                "entity_context": entity_context,
                "clue_context": clue_context,
                "memory_context": memory_context,
                "rule_context": rule_context,
            },
        )
        return scenario_context, entity_context, clue_context, memory_context, rule_context

    def _retrieve_context_impl(
        self,
        session: models.GameSession,
        query: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:

        retrieval = self.context.retrieval  # retrieval = 检索服务：用于查询 ChromaDB 向量库
        scenario_context: list[dict[str, Any]] = []  # scenario_context = 剧本片段：从 ChromaDB 检索的剧情描述
        entity_context: list[dict[str, Any]] = []  # entity_context = 实体信息：场景中的 NPC/物品/地点
        clue_context: list[dict[str, Any]] = []  # clue_context = 线索索引：可被发现的线索列表
        memory_context: list[dict[str, Any]] = []  # memory_context = 会话记忆：之前回合的记录
        rule_context: list[dict[str, Any]] = []  # rule_context = 规则片段：相关的游戏规则

        try:
            scenario_context = retrieval.query("scenario_chunks", query, n_results=6)  # 剧本片段，返回较多
        except Exception as exc:
            scenario_context = [{"id": "retrieval-error", "document": f"剧本检索暂不可用：{exc}", "metadata": {}, "distance": None}]
        try:
            entity_context = retrieval.query("scenario_entities", query, n_results=4)  # 场景实体
        except Exception:
            entity_context = []
        try:
            clue_context = retrieval.query("clue_index", query, n_results=4)  # 线索索引
        except Exception:
            clue_context = []
        try:
            # 会话记忆：按 session_id 过滤，只检索当前会话的记忆
            memory_context = retrieval.query("session_memory_chunks", query, n_results=3, where={"session_id": session.id})
        except Exception:
            memory_context = []
        try:
            rule_context = retrieval.query("rule_chunks", query, n_results=3)  # 规则片段
        except Exception:
            rule_context = []

        return scenario_context, entity_context, clue_context, memory_context, rule_context


@contextmanager
def null_trace_step():
    state: dict[str, Any] = {}
    yield state
