from pathlib import Path
import os
from typing import Any

os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
os.environ.setdefault("CHROMA_TELEMETRY_DISABLED", "1")

import chromadb
from chromadb.api.models.Collection import Collection
from chromadb.config import Settings as ChromaSettings

from app.config import get_settings
from app.services.chunking import DocumentChunk
from app.services.llm import EmbeddingClient
from app.utils import resolve_project_path


class RetrievalService:
    def __init__(self) -> None:
        settings = get_settings()
        chroma_path = resolve_project_path(settings.chroma_path)
        chroma_path.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(
            path=str(chroma_path),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        self.embedding = EmbeddingClient()

    def reset(self) -> None:
        for name in self.client.list_collections():
            collection_name = name.name if hasattr(name, "name") else str(name)
            self.client.delete_collection(collection_name)

    def collection(self, name: str) -> Collection:
        return self.client.get_or_create_collection(name=name, metadata={"hnsw:space": "cosine"})

    def upsert_chunks(self, collection_name: str, chunks: list[DocumentChunk], batch_size: int = 10) -> int:
        collection = self.collection(collection_name)
        count = 0
        batch_size = min(batch_size, 10)
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start : start + batch_size]
            embeddings = self.embedding.embed_texts([chunk.text for chunk in batch])
            collection.upsert(
                ids=[chunk.id for chunk in batch],
                documents=[chunk.text for chunk in batch],
                embeddings=embeddings,
                metadatas=[chunk.metadata for chunk in batch],
            )
            count += len(batch)
        return count

    def delete_where(self, collection_name: str, where: dict[str, Any]) -> int:
        collection = self.collection(collection_name)
        if collection.count() <= 0:
            return 0
        result = collection.get(where=where)
        ids = result.get("ids", [])
        if not ids:
            return 0
        collection.delete(ids=ids)
        return len(ids)

    def query(self, collection_name: str, query: str, n_results: int = 5, where: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        collection = self.collection(collection_name)
        collection_count = collection.count()
        if collection_count <= 0:
            return []
        n_results = min(n_results, collection_count)
        embedding = self.embedding.embed_texts([query])[0]
        result = collection.query(query_embeddings=[embedding], n_results=n_results, where=where)
        rows: list[dict[str, Any]] = []
        ids = result.get("ids", [[]])[0]
        documents = result.get("documents", [[]])[0]
        metadatas = result.get("metadatas", [[]])[0]
        distances = result.get("distances", [[]])[0]
        for index, doc_id in enumerate(ids):
            rows.append(
                {
                    "id": doc_id,
                    "document": documents[index],
                    "metadata": metadatas[index] or {},
                    "distance": distances[index] if index < len(distances) else None,
                }
            )
        return rows


def existing_source_paths(paths: list[Path]) -> list[Path]:
    return [path for path in paths if path.exists() and path.is_file()]
