# =============================================================================
# 【GameAssistantAgent：游戏助手 Agent】
# =============================================================================
# 这是一个独立于主回合流程的辅助 Agent，相当于玩家的"随身规则书"。
# 玩家可以随时向它提问，它不会修改游戏状态，只负责查找资料并回答。
#
# 与主回合 KeeperSupervisor 的关键区别：
# - 不调用 run_turn → 不触发回合流程
# - 不修改游戏状态 → 不写入数据库
# - 不执行 Skill/Tool/规则检定 → 不掷骰子
# - 只做检索 + 生成回答 → 纯信息查询
#
# 两种检索策略：
#
# 1. MQE（Multi-Query Expansion，多查询扩展）
#    - 把玩家问题扩展为多个语义等价的查询
#    - 比如"侦查技能怎么用？"扩展为：
#      "侦查检定的规则"、"Spot Hidden 如何判定"、"观察类技能检定方法"
#    - 目的：同一个问题有多种问法，扩展查询能覆盖更多相关文档
#
# 2. HyDE（Hypothetical Document Embedding，假设文档嵌入）
#    - 让 LLM 先生成一个假设性回答（可能不完全正确）
#    - 用这个假设回答的向量去检索
#    - 原理：假设回答的语义更接近目标文档，比直接用问题检索更有效
#
# 三种模式：
# - rules：仅检索规则文档，回答规则相关问题
# - session_help：检索规则 + 会话记忆，回答当前会话相关问题
# - auto：根据问题内容自动推断模式
# =============================================================================
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Literal

from sqlalchemy.orm import Session, selectinload

from app import models
from app.services.agent_monitor import AgentTraceRecorder
from app.services.assistant_prompts import build_assistant_prompt, build_hyde_prompt, build_mqe_prompt, format_assistant_context
from app.services.debug_events import DebugEmitter, emit_debug
from app.services.llm import LLMClient
from app.services.retrieval import RetrievalService


# 助手模式：rules 仅规则，session_help 规则+会话记忆，auto 自动推断
AssistantMode = Literal["rules", "session_help", "auto"]
# 剧透关键词：检测到这些词时拦截回答
SPOILER_TERMS = ["真相", "幕后", "黑手", "怪物是什么", "达贡", "深潜者", "邪教", "结局", "秘密"]


class GameAssistantAgent:
    """游戏助手 Agent（可以理解为"随身规则书/游戏百科"）。

    【中文名称】游戏助手 Agent / 助手 Agent

    【功能说明】
    独立于主回合流程的辅助 Agent。玩家可以随时向它提问规则或
    当前会话状态，它通过检索 ChromaDB 来回答。

    【与主回合的关键区别】
    - 不调用 run_turn → 不触发回合流程
    - 不修改游戏状态 → 不写入数据库
    - 不执行 Skill/Tool/规则检定 → 不掷骰子
    - 只做检索 + 生成回答 → 纯信息查询

    【为什么需要它】
    在玩 TRPG 时，玩家经常需要查规则书。游戏助手就像一个
    内置的"规则搜索引擎"，玩家不用翻 PDF 就能快速得到答案。
    """

    def __init__(self) -> None:
        """初始化游戏助手（__init__ = 构造函数/初始化方法）。

        【中文名称】初始化

        【功能说明】
        创建 GameAssistantAgent 实例时自动调用。
        创建 LLM 客户端和检索服务，供后续查询使用。

        【参数说明】无参数
        【返回值】无（返回 None）
        """
        self.llm = LLMClient()  # 创建 LLM 客户端
        self.retrieval = RetrievalService()  # 创建向量检索服务

    def chat(
        self,
        db: Session,
        *,
        message: str,
        session_id: str | None = None,
        mode: AssistantMode = "auto",
        enable_mqe: bool = True,
        mqe_expansions: int = 2,
        enable_hyde: bool | None = None,
        top_k: int = 5,
        candidate_pool_multiplier: int = 4,
        debug_emit: DebugEmitter | None = None,
        trace_recorder: AgentTraceRecorder | None = None,
    ) -> dict[str, Any]:
        """游戏助手主入口（chat = 聊天/对话）。

        【中文名称】聊天 / 对话

        【功能说明】
        接收玩家问题，经过剧透检测、MQE 扩展、HyDE 增强、
        多集合检索、LLM 生成回答、防剧透清洗后返回结果。

        【执行流程】
        玩家问题 → 推断模式（rules/session_help）
          → 剧透检测（如果是剧透问题直接拦截）
          → MQE 查询扩展（生成多个查询变体）
          → HyDE 假设文档（生成假设回答辅助检索）
          → 多查询多集合检索（ChromaDB）
          → 过滤不可见内容 + 去重排序
          → LLM 生成回答
          → 防剧透清洗
          → 构建引用
          → 返回结果

        【参数说明】
        - db: 数据库会话
        - message: 玩家问题文本
        - session_id: 当前会话 ID（可选，用于 session_help 模式）
        - mode: 助手模式（rules/session_help/auto）
        - enable_mqe: 是否启用 MQE 查询扩展
        - mqe_expansions: MQE 扩展查询数量
        - enable_hyde: 是否启用 HyDE（None=自动判断）
        - top_k: 最终返回的检索结果数量
        - candidate_pool_multiplier: 候选池倍数
        - debug_emit: 调试事件发射器

        【返回值】
        - dict: 包含 answer（回答）、citations（引用）、mode（模式）等
        """
        with (trace_recorder.step(agent_name="GameAssistantAgent", step_name="chat", phase="assistant", input_payload={
            "message": message,
            "session_id": session_id,
            "mode": mode,
            "enable_mqe": enable_mqe,
            "mqe_expansions": mqe_expansions,
            "enable_hyde": enable_hyde,
            "top_k": top_k,
            "candidate_pool_multiplier": candidate_pool_multiplier,
        }) if trace_recorder else null_trace_step()) as trace_step:
            result = self._chat_impl(
                db,
                message=message,
                session_id=session_id,
                mode=mode,
                enable_mqe=enable_mqe,
                mqe_expansions=mqe_expansions,
                enable_hyde=enable_hyde,
                top_k=top_k,
                candidate_pool_multiplier=candidate_pool_multiplier,
                debug_emit=debug_emit,
                trace_recorder=trace_recorder,
            )
            trace_step["output"] = result
            return result

    def _chat_impl(
        self,
        db: Session,
        *,
        message: str,
        session_id: str | None,
        mode: AssistantMode,
        enable_mqe: bool,
        mqe_expansions: int,
        enable_hyde: bool | None,
        top_k: int,
        candidate_pool_multiplier: int,
        debug_emit: DebugEmitter | None,
        trace_recorder: AgentTraceRecorder | None,
    ) -> dict[str, Any]:
        effective_mode = infer_mode(message, mode)  # 推断助手模式
        emit_debug(debug_emit, phase="assistant", name="infer_mode", status="success", message=f"推断模式：{effective_mode}", metadata={"mode": effective_mode, "raw_mode": mode})
        session = load_session(db, session_id) if session_id else None  # 加载会话（可选）
        if is_spoiler_question(message):  # 剧透检测
            emit_debug(debug_emit, phase="assistant", name="spoiler_check", status="warning", message="检测到剧透问题，已拦截。")
            return spoiler_response(effective_mode)
        # MQE 查询扩展：生成多个语义等价的查询，提高检索召回率
        emit_debug(debug_emit, phase="assistant", name="expand_queries", status="start", message="开始 MQE 查询扩展。", metadata={"enable_mqe": enable_mqe, "mqe_expansions": mqe_expansions})
        with (trace_recorder.step(agent_name="GameAssistantAgent", step_name="expand_queries", phase="assistant", input_payload={"message": message, "enable_mqe": enable_mqe, "mqe_expansions": mqe_expansions}) if trace_recorder else null_trace_step()) as trace_step:
            expanded_queries = self.expand_queries(message, enable_mqe, mqe_expansions)
            trace_step["output"] = {"queries": expanded_queries}
        emit_debug(debug_emit, phase="assistant", name="expand_queries", status="success", message=f"查询扩展完成：{len(expanded_queries)} 条。", metadata={"queries": expanded_queries})
        # HyDE 假设文档：生成假设性回答文档，用其嵌入进行检索
        with (trace_recorder.step(agent_name="GameAssistantAgent", step_name="hyde", phase="assistant", input_payload={"message": message, "enable_hyde": enable_hyde, "mode": effective_mode}) if trace_recorder else null_trace_step()) as trace_step:
            hyde_text = self.generate_hyde(message, enable_hyde, effective_mode)
            trace_step["output"] = {"hyde_text": hyde_text}
        if hyde_text:
            expanded_queries.append(hyde_text)  # 将 HyDE 文档作为额外查询
            emit_debug(debug_emit, phase="assistant", name="hyde", status="success", message="HyDE 假设文档已生成。", metadata={"hyde_preview": hyde_text[:200]})
        # 多查询多集合检索
        emit_debug(debug_emit, phase="assistant", name="retrieve", status="start", message="开始检索规则与会话信息。", metadata={"collections": collections_for_mode(effective_mode, session), "top_k": top_k})
        with (trace_recorder.step(agent_name="GameAssistantAgent", step_name="retrieve", phase="assistant", input_payload={"queries": expanded_queries, "mode": effective_mode, "session": session, "top_k": top_k, "candidate_pool_multiplier": candidate_pool_multiplier}) if trace_recorder else null_trace_step()) as trace_step:
            rows = self.retrieve(expanded_queries, effective_mode, session, top_k, candidate_pool_multiplier)
            trace_step["output"] = {"rows": rows, "result_count": len(rows)}
        emit_debug(debug_emit, phase="assistant", name="retrieve", status="success", message=f"检索完成：{len(rows)} 条结果。", metadata={"result_count": len(rows), "visible_rows": [{"id": r.get("id", ""), "distance": r.get("distance")} for r in rows[:10]]})
        # 构建会话上下文和 prompt
        session_context = build_session_context(session) if session else "未绑定当前会话。"
        prompt = build_assistant_prompt(message=message, mode=effective_mode, context=format_assistant_context(rows), session_context=session_context)
        fallback = build_fallback_answer(message, rows, effective_mode)  # 回退回答
        emit_debug(debug_emit, phase="assistant", name="generate_answer", status="start", message="开始生成助手回答。")
        with (trace_recorder.step(agent_name="GameAssistantAgent", step_name="generate_answer", phase="assistant", input_payload={"prompt": prompt, "fallback": fallback}) if trace_recorder else null_trace_step()) as trace_step:
            answer = self.llm.chat_text(prompt, temperature=0.2) or fallback  # 低温度生成
            trace_step["output"] = {"answer": answer}
        answer, blocked = sanitize_assistant_answer(answer, session)  # 防剧透清洗
        emit_debug(debug_emit, phase="assistant", name="generate_answer", status="success", message=f"回答生成完成，{len(answer)} 字。", metadata={"spoiler_blocked": blocked, "answer_preview": answer[:200]})
        citations = build_citations(rows)  # 构建引用
        return {
            "answer": answer,  # 助手回答
            "citations": citations,  # 引用列表
            "retrieval_debug": {  # 检索调试信息
                "queries": expanded_queries,
                "hyde_enabled": bool(hyde_text),
                "result_count": len(rows),
                "collections": collections_for_mode(effective_mode, session),
            },
            "spoiler_blocked": blocked,  # 是否拦截了剧透
            "mode": effective_mode,  # 实际使用的模式
        }

    def expand_queries(self, message: str, enable_mqe: bool, count: int) -> list[str]:
        """MQE 查询扩展（expand_queries = 扩展查询）。

        【中文名称】扩展查询

        【功能说明】
        把玩家问题扩展为多个语义等价的查询，提高检索召回率。
        原始查询始终保留在结果中。

        【参数说明】
        - message: 玩家原始问题
        - enable_mqe: 是否启用 MQE
        - count: 扩展查询数量

        【返回值】
        - list[str]: 查询列表（原始查询 + 扩展查询）
        """
        queries = [message]  # 原始查询始终保留
        if not enable_mqe:
            return queries
        fallback = {"queries": []}
        generated = self.llm.chat_json(build_mqe_prompt(message, max(1, min(count, 3))), fallback=fallback)
        for item in generated.get("queries", []):
            text = str(item).strip()
            if text and text not in queries:  # 去重
                queries.append(text[:200])
        return queries

    def generate_hyde(self, message: str, enable_hyde: bool | None, mode: str) -> str:
        """HyDE 假设文档生成（generate_hyde = 生成假设文档）。

        【中文名称】生成假设文档

        【功能说明】
        让 LLM 生成一个假设性回答，用这个回答的向量去检索。
        session_help 模式下默认不启用（会话记忆通常较短）。

        【参数说明】
        - message: 玩家问题
        - enable_hyde: 是否启用（None=自动判断）
        - mode: 当前助手模式

        【返回值】
        - str: 假设文档文本（空字符串表示不启用）
        """
        if enable_hyde is False:
            return ""
        if enable_hyde is None and mode == "session_help":  # session_help 模式默认不启用
            return ""
        text = self.llm.chat_text(build_hyde_prompt(message), temperature=0.2)
        return text.strip()[:800] if text else ""

    def retrieve(self, queries: list[str], mode: str, session: models.GameSession | None, top_k: int, candidate_pool_multiplier: int) -> list[dict[str, Any]]:
        """多查询多集合检索（retrieve = 检索/查找）。

        【中文名称】检索

        【功能说明】
        对每个查询在每个集合中检索，合并结果后过滤不可见内容、
        去重排序，返回 top_k 条最佳结果。

        【参数说明】
        - queries: 查询列表（原始 + MQE 扩展 + HyDE）
        - mode: 助手模式
        - session: 游戏会话（用于过滤）
        - top_k: 最终返回数量
        - candidate_pool_multiplier: 候选池倍数

        【返回值】
        - list[dict]: 排序后的检索结果
        """
        candidate_pool = max(top_k, top_k * max(candidate_pool_multiplier, 1))  # 候选池大小
        rows: list[dict[str, Any]] = []
        for query in queries:
            for collection in collections_for_mode(mode, session):
                try:
                    # session_memory_chunks 按当前会话过滤
                    where = {"session_id": session.id} if collection == "session_memory_chunks" and session else None
                    rows.extend(self.retrieval.query(collection, query, n_results=min(candidate_pool, 20), where=where))
                except Exception:
                    continue
        return rank_and_dedupe(filter_visible_rows(rows, session))[:top_k]  # 过滤 + 去重排序 + 截取 top_k


def load_session(db: Session, session_id: str | None) -> models.GameSession | None:
    """加载游戏会话（load_session = 加载会话）。

    【中文名称】加载会话

    【功能说明】
    从 PostgreSQL 加载指定会话及其关联数据（线索、物品、回合日志）。
    如果 session_id 为空则返回 None。

    【参数说明】
    - db: 数据库会话
    - session_id: 会话 ID（可为 None）

    【返回值】
    - GameSession | None: 游戏会话对象，不存在时返回 None
    """
    if not session_id:
        return None
    return (
        db.query(models.GameSession)
        .options(selectinload(models.GameSession.clues), selectinload(models.GameSession.inventory_items), selectinload(models.GameSession.turn_logs))
        .filter(models.GameSession.id == session_id)
        .one_or_none()
    )


def infer_mode(message: str, mode: str) -> str:
    """推断助手模式（infer_mode = 推断模式）。

    【中文名称】推断模式

    【功能说明】
    如果 mode 已经是 rules 或 session_help，直接返回。
    如果是 auto，根据问题内容推断：
    - 包含"我现在"、"线索"、"去哪"等关键词 → session_help
    - 否则 → rules

    【参数说明】
    - message: 玩家问题
    - mode: 原始模式设置

    【返回值】
    - str: 推断后的模式（rules 或 session_help）
    """
    if mode in {"rules", "session_help"}:
        return mode
    if any(word in message for word in ["我现在", "线索", "去哪", "下一步", "目前", "已发现"]):
        return "session_help"
    return "rules"


def is_spoiler_question(message: str) -> bool:
    """检测剧透问题（is_spoiler_question = 是否是剧透问题）。

    【中文名称】检测剧透问题

    【功能说明】
    同时满足两个条件时判定为剧透问题：
    1. 包含剧透关键词（如"真相"、"幕后"、"达贡"）
    2. 包含追问词（如"是什么"、"是谁"、"告诉我"）

    【参数说明】
    - message: 玩家问题

    【返回值】
    - bool: True 表示是剧透问题，应拦截
    """
    return any(term in message for term in SPOILER_TERMS) and any(term in message for term in ["什么", "是谁", "为何", "真相", "告诉我"])


def spoiler_response(mode: str) -> dict[str, Any]:
    """构造剧透拦截响应（spoiler_response = 剧透响应）。

    【中文名称】剧透响应

    【功能说明】
    当检测到剧透问题时，返回一个礼貌的拒绝回答，
    引导玩家通过正常调查来发现真相。

    【参数说明】
    - mode: 当前助手模式

    【返回值】
    - dict: 包含拦截回答的字典
    """
    answer = "这涉及可能的剧本秘密。我不能直接透露真相。你可以从已发现线索、当前位置和异常现象出发继续调查；如果需要，我可以帮你整理已经发现的信息。"
    return {"answer": answer, "citations": [], "retrieval_debug": {"queries": [], "result_count": 0}, "spoiler_blocked": True, "mode": mode}


def collections_for_mode(mode: str, session: models.GameSession | None) -> list[str]:
    """根据模式返回检索集合列表（collections_for_mode = 模式对应的集合）。

    【中文名称】模式对应的检索集合

    【功能说明】
    - rules 模式：只检索 rule_chunks（规则书）
    - session_help 模式：检索 session_memory_chunks（会话记忆）+ rule_chunks（规则书）

    【参数说明】
    - mode: 助手模式
    - session: 游戏会话

    【返回值】
    - list[str]: ChromaDB collection 名称列表
    """
    if mode == "session_help" and session is not None:
        return ["session_memory_chunks", "rule_chunks"]
    return ["rule_chunks"]


def filter_visible_rows(rows: list[dict[str, Any]], session: models.GameSession | None) -> list[dict[str, Any]]:
    """过滤不可见的检索结果（filter_visible_rows = 过滤可见行）。

    【中文名称】过滤可见行

    【功能说明】
    移除两类不可见内容：
    1. visibility 为 keeper_only 的内容（守秘人专用）
    2. 玩家尚未发现的线索（不在 known_clues 中）

    【参数说明】
    - rows: 检索结果列表
    - session: 游戏会话

    【返回值】
    - list[dict]: 过滤后的可见结果
    """
    visible: list[dict[str, Any]] = []
    known_clues = {clue.clue_key for clue in session.clues} | {clue.name for clue in session.clues} if session else set()  # 已知线索 key
    for row in rows:
        metadata = row.get("metadata") or {}
        visibility = metadata.get("visibility") or metadata.get("secret_level")
        if visibility in {"keeper_only", "主持人秘密"}:  # 跳过守秘人专用内容
            continue
        clue_key = metadata.get("clue_key")
        if clue_key and metadata.get("source_type") == "clue" and session and clue_key not in known_clues:  # 跳过未发现线索
            continue
        visible.append(row)
    return visible


def rank_and_dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """去重并排序检索结果（rank_and_dedupe = 排序并去重）。

    【中文名称】排序并去重

    【功能说明】
    按唯一 key 去重（相同文档只保留得分最高的），
    然后按得分降序排列。

    【参数说明】
    - rows: 检索结果列表

    【返回值】
    - list[dict]: 去重排序后的结果
    """
    by_key: dict[str, dict[str, Any]] = {}
    for row in rows:
        metadata = row.get("metadata") or {}
        key = str(metadata.get("memory_id") or metadata.get("chunk_id") or row.get("id") or f"{metadata.get('source_path')}:{metadata.get('chunk_index')}")
        existing = by_key.get(key)
        if existing is None or score_row(row) > score_row(existing):
            by_key[key] = row
    return sorted(by_key.values(), key=score_row, reverse=True)


def score_row(row: dict[str, Any]) -> float:
    """计算检索结果得分（score_row = 计算行得分）。

    【中文名称】计算行得分

    【功能说明】
    综合三个因素计算得分：
    - 基础分 = 1 - distance（向量距离，越近越高）
    - 规则书加成 +0.2（source_type 为 rulebook/investigator_handbook）
    - 公开内容加成 +0.1（visibility 为 public/player_visible）

    【参数说明】
    - row: 单条检索结果

    【返回值】
    - float: 综合得分
    """
    metadata = row.get("metadata") or {}
    distance = row.get("distance")
    base = 1.0 - float(distance or 0.0)
    if metadata.get("source_type") in {"rulebook", "investigator_handbook"}:
        base += 0.2
    if metadata.get("visibility") in {"public", "player_visible"}:
        base += 0.1
    return base


def build_citations(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """构建引用列表（build_citations = 构建引用）。

    【中文名称】构建引用

    【功能说明】
    从检索结果中提取来源信息，构建引用列表供前端展示。
    每条引用包含：id、标题、来源类型、引用文本、内容片段。

    【参数说明】
    - rows: 检索结果列表

    【返回值】
    - list[dict]: 引用列表
    """
    citations: list[dict[str, Any]] = []
    for row in rows:
        metadata = row.get("metadata") or {}
        citations.append(
            {
                "id": row.get("id", ""),
                "title": metadata.get("title") or metadata.get("source_name") or row.get("id", ""),
                "source_type": metadata.get("source_type") or metadata.get("collection_type") or "unknown",
                "citation": metadata.get("citation") or metadata.get("title") or "未命名来源",
                "snippet": str(row.get("document") or "")[:240],
            }
        )
    return citations


def build_session_context(session: models.GameSession | None) -> str:
    """构建会话上下文文本（build_session_context = 构建会话上下文）。

    【中文名称】构建会话上下文

    【功能说明】
    将当前会话的关键信息格式化为文本，供 LLM 理解当前游戏状态。
    包括：位置、场景、摘要、已发现线索、物品栏。

    【参数说明】
    - session: 游戏会话（可为 None）

    【返回值】
    - str: 格式化的会话上下文文本
    """
    if session is None:
        return "未绑定当前会话。"
    clue_text = "；".join(f"{clue.name}: {clue.content[:120]}" for clue in session.clues[:12]) or "暂无已发现线索。"
    inventory_text = "；".join(f"{item.name}×{item.quantity}" for item in session.inventory_items[:12]) or "暂无物品。"
    return f"当前位置：{session.current_location}\n当前场景：{session.current_scene}\n摘要：{session.summary}\n已发现线索：{clue_text}\n物品：{inventory_text}"


def build_fallback_answer(message: str, rows: list[dict[str, Any]], mode: str) -> str:
    """构建回退回答（build_fallback_answer = 构建回退回答）。

    【中文名称】构建回退回答

    【功能说明】
    LLM 生成失败时使用的兜底回答。
    如果没有检索结果，提示换一种问法。
    如果有结果，引用第一条作为参考。

    【参数说明】
    - message: 玩家问题
    - rows: 检索结果
    - mode: 助手模式

    【返回值】
    - str: 回退回答文本
    """
    if not rows:
        return "我暂时没有检索到可靠资料。你可以换一种问法，或询问更具体的规则术语。"
    first = rows[0]
    citation = (first.get("metadata") or {}).get("citation") or (first.get("metadata") or {}).get("title") or "资料片段"
    return f"根据可见资料，相关内容可参考：{citation}。简要来说，这个问题需要结合检索片段判断；如果你需要，我可以继续解释具体规则。"


def sanitize_assistant_answer(answer: str, session: models.GameSession | None) -> tuple[str, bool]:
    """防剧透清洗（sanitize_assistant_answer = 清洗助手回答）。

    【中文名称】清洗助手回答

    【功能说明】
    检查回答中是否包含剧透关键词（如“达贡”、“深潜者”），
    如果包含且玩家尚未通过正常游戏发现，则替换为“尚未确认的秘密”。

    【参数说明】
    - answer: LLM 生成的原始回答
    - session: 游戏会话（用于判断哪些是已知信息）

    【返回值】
    - tuple[str, bool]: (清洗后的回答, 是否拦截了剧透)
    """
    known = "\n".join(clue.name for clue in session.clues) if session else ""
    blocked = False
    sanitized = answer
    for term in ["达贡", "深潜者", "邪教", "幕后", "最终真相", "结局条件"]:
        if term in sanitized and term not in known:
            sanitized = sanitized.replace(term, "尚未确认的秘密")
            blocked = True
    if blocked:
        sanitized += "\n\n这部分可能涉及未发现内容，我已改为非剧透表述。"
    return sanitized, blocked


@contextmanager
def null_trace_step():
    state: dict[str, Any] = {}
    yield state
