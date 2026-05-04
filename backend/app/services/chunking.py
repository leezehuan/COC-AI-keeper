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
