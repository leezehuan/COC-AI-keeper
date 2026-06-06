# 【ClueEligibilityTool：线索候选资格判断工具】
# 判断候选线索是否可能被当前行动发现，但不直接创建线索。
# 核心逻辑：
# - 过滤掉已发现的线索（避免重复）和主持人秘密线索（防剧透）。
# - 根据玩家行动目标与线索内容的匹配度，判断线索是否"有资格被发现"。
# - 最终是否真正创建线索，由 NarratorAgent 在 state_delta 中决定，GuardAgent 校验。
from __future__ import annotations

from typing import Any

from app.services.tools.base import ToolObservation, ToolSpec


TOOL_NAME = "ClueEligibilityTool"


def tool_spec() -> ToolSpec:
    """返回 Tool 的规格说明（tool_spec = Tool 规格说明）。

    【中文名称】Tool 规格说明
    """
    return ToolSpec(
        name=TOOL_NAME,
        description="判断候选线索是否可能被当前行动发现，不直接创建线索。",
        input_schema={"target": "行动目标", "clue_context": "候选线索检索结果", "known_clue_keys": "已发现线索 key 列表"},
        constraints=["只能返回候选资格。", "不能把未发现线索直接写入玩家状态。", "keeper_only 线索不得进入玩家可见输出。"],
    )


def run_clue_eligibility(*, target: str, clue_context: list[dict[str, Any]], known_clue_keys: list[str]) -> ToolObservation:
    """判断线索候选资格（run_clue_eligibility = 运行线索候选判断）。

    【中文名称】运行线索候选判断

    【功能说明】
    过滤已发现和秘密线索，匹配行动目标，返回候选线索列表。
    最终是否创建线索由 NarratorAgent 和 GuardAgent 决定。

    【参数说明】
    - target: 玩家行动目标（如"灯塔地下室"）
    - clue_context: 候选线索检索结果
    - known_clue_keys: 已发现线索的 key 列表

    【返回值】
    - ToolObservation: 包含候选线索列表
    """
    target_text = target.strip().lower()
    known = {item.strip().lower() for item in known_clue_keys}  # 已发现线索集合
    candidates: list[dict[str, Any]] = []
    for row in clue_context:
        metadata = row.get("metadata") or {}
        clue_key = str(metadata.get("clue_key") or row.get("id") or "").strip()
        visibility = str(metadata.get("visibility") or metadata.get("secret_level") or "")
        if clue_key.lower() in known:
            continue  # 跳过已发现的线索
        if visibility in {"keeper_only", "主持人秘密"}:
            continue  # 跳过主持人秘密线索，防止剧透
        document = str(row.get("document") or "")
        title = str(metadata.get("title") or "")
        # 判断目标是否与线索内容匹配
        matched = not target_text or target_text in document.lower() or target_text in title.lower()
        candidates.append(
            {
                "clue_key": clue_key,
                "name": title or clue_key,
                "eligible": matched,  # True 表示强匹配，False 表示弱候选
                "reason": "目标与线索片段匹配。" if matched else "可作为弱候选，需叙事和状态校验确认。",
                "citation": metadata.get("citation") or title,  # 引用标注
            }
        )
    return ToolObservation(tool=TOOL_NAME, input={"target": target}, output={"candidates": candidates})
