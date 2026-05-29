from dataclasses import dataclass
from pathlib import Path


@dataclass
class DocumentChunk:
    id: str
    text: str
    metadata: dict


def chunk_markdown(path: Path, collection_type: str, max_chars: int = 1400) -> list[DocumentChunk]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()
    chunks: list[DocumentChunk] = []
    current_title = path.stem
    buffer: list[str] = []
    chunk_index = 0

    def flush() -> None:
        nonlocal buffer, chunk_index
        content = "\n".join(buffer).strip()
        if not content:
            buffer = []
            return
        chunks.append(
            DocumentChunk(
                id=f"{collection_type}:{path.stem}:{chunk_index}",
                text=content,
                metadata={
                    "source_path": str(path),
                    "source_name": path.stem,
                    "title": current_title,
                    "collection_type": collection_type,
                    "rag_namespace": "rules" if collection_type == "rule" else "scenario",
                    "source_type": infer_source_type(path, collection_type),
                    "visibility": infer_visibility(collection_type, current_title, content),
                    "chapter": current_title,
                    "chunk_index": chunk_index,
                    "memory_type": "rag_chunk",
                    "is_rag_data": True,
                    "data_source": "rag_pipeline",
                    "citation": f"{path.stem} · {current_title}",
                },
            )
        )
        chunk_index += 1
        buffer = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            flush()
            current_title = stripped.lstrip("#").strip() or current_title
            buffer.append(line)
            continue
        if sum(len(item) + 1 for item in buffer) + len(line) > max_chars:
            flush()
        buffer.append(line)
    flush()
    return chunks


def infer_source_type(path: Path, collection_type: str) -> str:
    if collection_type == "rule":
        if "investigator" in str(path).lower() or "调查员" in path.stem:
            return "investigator_handbook"
        return "rulebook"
    return "scenario_public"


def infer_visibility(collection_type: str, title: str, content: str) -> str:
    if collection_type == "rule":
        return "public"
    probe = f"{title}\n{content[:800]}"
    if any(term in probe for term in ["秘密", "真相", "幕后", "主持人", "结局", "深潜者", "达贡", "邪教"]):
        return "keeper_only"
    return "player_visible"
