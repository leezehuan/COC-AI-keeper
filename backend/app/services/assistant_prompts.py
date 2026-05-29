from __future__ import annotations

from typing import Any


ASSISTANT_SYSTEM_PROMPT = """你是 coc-lite 的场外游戏助手。

职责：
1. 回答 COC 规则问题、术语解释和网页操作问题。
2. 只基于规则资料、调查员手册、玩家已发现线索和当前会话可见摘要回答。
3. 提供非剧透行动建议。
4. 不推进剧情、不替守秘人裁定、不掷骰、不修改状态。
5. 不透露未发现线索、剧本真相、隐藏 NPC 动机、怪物真名或 keeper_only 内容。
6. 回答末尾应尽量提到引用来源。

如果问题涉及剧本秘密，应拒绝剧透，并建议玩家回顾已发现信息。
"""

ASSISTANT_USER_PROMPT_TEMPLATE = """【问题】
{message}

【模式】
{mode}

【检索片段】
{context}

【当前会话可见信息】
{session_context}

请用简洁中文回答。不要编造规则；不确定时说明不确定。不要泄露未发现剧情。"""

MQE_SYSTEM_PROMPT = """你是检索查询扩展器。请把玩家问题改写为 2 到 3 条中文短查询，用于召回 COC 规则或已知信息。

输出合法 JSON，对象字段为 queries，值为字符串数组。不要输出 Markdown。"""

MQE_USER_PROMPT_TEMPLATE = """玩家问题：{message}

扩展数量：{count}

请生成语义等价或互补的短查询。"""

HYDE_SYSTEM_PROMPT = """你是 HyDE 假设文档生成器。请为玩家的 COC 规则问题生成一段可能出现在规则书中的中文答案性段落。

这段文字只用于检索，不会直接展示给玩家。不要编造具体剧本秘密。"""

HYDE_USER_PROMPT_TEMPLATE = """玩家问题：{message}

请生成一段 120 到 220 字的假设规则段落。"""


def build_assistant_prompt(*, message: str, mode: str, context: str, session_context: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": ASSISTANT_SYSTEM_PROMPT},
        {"role": "user", "content": ASSISTANT_USER_PROMPT_TEMPLATE.format(message=message, mode=mode, context=context, session_context=session_context)},
    ]


def build_mqe_prompt(message: str, count: int) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": MQE_SYSTEM_PROMPT},
        {"role": "user", "content": MQE_USER_PROMPT_TEMPLATE.format(message=message, count=count)},
    ]


def build_hyde_prompt(message: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": HYDE_SYSTEM_PROMPT},
        {"role": "user", "content": HYDE_USER_PROMPT_TEMPLATE.format(message=message)},
    ]


def format_assistant_context(rows: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for index, row in enumerate(rows, 1):
        metadata = row.get("metadata") or {}
        citation = metadata.get("citation") or metadata.get("title") or metadata.get("source_name") or row.get("id")
        parts.append(f"[{index}] {citation}\n{str(row.get('document') or '')[:900]}")
    return "\n\n".join(parts) if parts else "暂无可靠检索片段。"
