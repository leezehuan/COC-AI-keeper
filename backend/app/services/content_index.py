from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.services.chunking import DocumentChunk
from app.utils import safe_key


LOCATION_TERMS = ["灯塔", "小屋", "码头", "海面", "厨房", "书房", "宿舍", "走廊", "房间", "岛", "服务室", "贮藏室"]
NPC_TERMS = ["卡西迪", "巴恩斯", "邪教徒", "调查员", "艺术家", "古董商", "生物学家", "探员"]
CLUE_TERMS = ["线索", "信", "日记", "日志", "地图", "材料", "暗格", "脚印", "血迹", "金币", "尸体", "钥匙", "残页"]
EVENT_TERMS = ["攻击", "结局", "触发", "燃运", "理智", "疯狂", "沉没", "熄灭", "仪式"]
SECRET_TERMS = ["秘密", "真相", "达贡", "邪教", "深潜者", "幼徒", "混种", "结局", "幕后", "主持人", "怪物"]


@dataclass
class MarkdownSection:
    index: int
    title: str
    level: int
    text: str


def build_structured_indexes(path: Path) -> dict[str, list[DocumentChunk]]:
    sections = parse_markdown_sections(path)
    entity_chunks: list[DocumentChunk] = []
    clue_chunks: list[DocumentChunk] = []
    for section in sections:
        entity_type = classify_entity_type(section.title, section.text)
        secret_level = classify_secret_level(section.title, section.text)
        if entity_type:
            entity_chunks.append(build_entity_chunk(path, section, entity_type, secret_level))
        if is_clue_section(section.title, section.text):
            clue_chunks.append(build_clue_chunk(path, section, secret_level))
    return {"scenario_entities": entity_chunks, "clue_index": clue_chunks}


def parse_markdown_sections(path: Path) -> list[MarkdownSection]:
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    sections: list[MarkdownSection] = []
    current_title = path.stem
    current_level = 1
    buffer: list[str] = []
    section_index = 0

    def flush() -> None:
        nonlocal buffer, section_index
        text = "\n".join(buffer).strip()
        if text:
            sections.append(MarkdownSection(index=section_index, title=current_title, level=current_level, text=text))
            section_index += 1
        buffer = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            flush()
            current_level = len(stripped) - len(stripped.lstrip("#"))
            current_title = stripped.lstrip("#").strip() or current_title
            buffer.append(line)
            continue
        buffer.append(line)
    flush()
    return sections


def build_entity_chunk(path: Path, section: MarkdownSection, entity_type: str, secret_level: str) -> DocumentChunk:
    content = compact_text(section.text)
    return DocumentChunk(
        id=f"entity:{safe_key(path.stem)}:{section.index}",
        text=f"类型：{entity_type}\n名称：{section.title}\n秘密等级：{secret_level}\n内容：{content}",
        metadata={
            "source_path": str(path),
            "source_name": path.stem,
            "title": section.title,
            "collection_type": "scenario_entity",
            "entity_type": entity_type,
            "secret_level": secret_level,
        },
    )


def build_clue_chunk(path: Path, section: MarkdownSection, secret_level: str) -> DocumentChunk:
    clue_key = safe_key(section.title)
    content = compact_text(section.text)
    key_clue = "关键" in section.text or "必须" in section.text or "重要" in section.text
    return DocumentChunk(
        id=f"clue:{safe_key(path.stem)}:{section.index}:{clue_key}",
        text=f"线索名称：{section.title}\n来源位置：{section.title}\n是否关键线索：{'是' if key_clue else '否'}\n秘密等级：{secret_level}\n内容：{content}",
        metadata={
            "source_path": str(path),
            "source_name": path.stem,
            "title": section.title,
            "collection_type": "clue_index",
            "clue_key": clue_key,
            "source_location": section.title,
            "secret_level": secret_level,
            "is_key_clue": key_clue,
        },
    )


def classify_entity_type(title: str, text: str) -> str:
    probe = f"{title}\n{text[:500]}"
    if any(term in probe for term in LOCATION_TERMS):
        return "地点"
    if any(term in probe for term in NPC_TERMS):
        return "NPC"
    if any(term in probe for term in CLUE_TERMS):
        return "线索"
    if any(term in probe for term in EVENT_TERMS):
        return "事件"
    return ""


def classify_secret_level(title: str, text: str) -> str:
    probe = f"{title}\n{text[:1000]}"
    if any(term in probe for term in SECRET_TERMS):
        return "主持人秘密"
    return "玩家可见"


def is_clue_section(title: str, text: str) -> bool:
    probe = f"{title}\n{text[:1000]}"
    return any(term in probe for term in CLUE_TERMS)


def compact_text(text: str, limit: int = 1200) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)[:limit]
