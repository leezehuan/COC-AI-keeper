from __future__ import annotations

from typing import Any

from app.services.tools.base import ToolObservation, ToolSpec


TOOL_NAME = "ClueEligibilityTool"


def tool_spec() -> ToolSpec:
    return ToolSpec(
        name=TOOL_NAME,
        description="判断候选线索是否可能被当前行动发现，不直接创建线索。",
        input_schema={"target": "行动目标", "clue_context": "候选线索检索结果", "known_clue_keys": "已发现线索 key 列表"},
        constraints=["只能返回候选资格。", "不能把未发现线索直接写入玩家状态。", "keeper_only 线索不得进入玩家可见输出。"],
    )


def run_clue_eligibility(*, target: str, clue_context: list[dict[str, Any]], known_clue_keys: list[str]) -> ToolObservation:
    target_text = target.strip().lower()
    known = {item.strip().lower() for item in known_clue_keys}
    candidates: list[dict[str, Any]] = []
    for row in clue_context:
        metadata = row.get("metadata") or {}
        clue_key = str(metadata.get("clue_key") or row.get("id") or "").strip()
        visibility = str(metadata.get("visibility") or metadata.get("secret_level") or "")
        if clue_key.lower() in known:
            continue
        if visibility in {"keeper_only", "主持人秘密"}:
            continue
        document = str(row.get("document") or "")
        title = str(metadata.get("title") or "")
        matched = not target_text or target_text in document.lower() or target_text in title.lower()
        candidates.append(
            {
                "clue_key": clue_key,
                "name": title or clue_key,
                "eligible": matched,
                "reason": "目标与线索片段匹配。" if matched else "可作为弱候选，需叙事和状态校验确认。",
                "citation": metadata.get("citation") or title,
            }
        )
    return ToolObservation(tool=TOOL_NAME, input={"target": target}, output={"candidates": candidates})
