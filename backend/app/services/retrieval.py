# 【RAG 检索服务】
# 这个文件封装了 ChromaDB 向量数据库的读写操作，是 RAG（检索增强生成）的核心组件。
# 对初学者来说，核心概念：
# - RetrievalService：管理 ChromaDB 客户端，提供 upsert（写入）、query（检索）、delete（删除）操作。
# - collection：ChromaDB 中的"表"，每个 collection 存储一类数据（剧本、规则、线索、记忆等）。
# - cosine 相似度：衡量两个向量有多"接近"，0 表示完全相同，2 表示完全相反。
# - upsert_chunks：把文本块和向量写入 ChromaDB，用于后续检索。
# - query：根据查询文本检索最相似的文本块，返回给 Agent 使用。
from pathlib import Path
import os
from typing import Any

# 禁用 ChromaDB 的匿名遥测，避免启动时打印警告
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
os.environ.setdefault("CHROMA_TELEMETRY_DISABLED", "1")

import chromadb
from chromadb.api.models.Collection import Collection
from chromadb.config import Settings as ChromaSettings

from app.config import get_settings
from app.services.chunking import DocumentChunk  # 文本块数据类
from app.services.llm import EmbeddingClient  # 向量化客户端
from app.utils import resolve_project_path


class RetrievalService:
    """RAG 检索服务（可以理解为"向量数据库管家"）。

    【中文名称】检索服务

    【功能说明】管理 ChromaDB 向量数据库的读写操作。

    核心方法：
    - upsert_chunks：将文本块向量化后写入 ChromaDB（导入数据时调用）。
    - query：根据查询文本检索最相似的文本块（Agent 运行时调用）。
    - delete_where：按条件删除向量数据（删除会话时清理记忆）。
    - reset：清空所有 collection（重新导入时使用）。
    """

    def __init__(self) -> None:
        """初始化检索服务（__init__ = 构造函数）。

        【中文名称】初始化
        【功能说明】创建 ChromaDB 持久化客户端和 Embedding 客户端。
        """
        settings = get_settings()
        chroma_path = resolve_project_path(settings.chroma_path)
        chroma_path.mkdir(parents=True, exist_ok=True)  # 确保目录存在
        # PersistentClient：数据持久化到磁盘，重启后不丢失
        self.client = chromadb.PersistentClient(
            path=str(chroma_path),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        self.embedding = EmbeddingClient()  # 用于将文本转为向量

    def reset(self) -> None:
        """清空所有 collection（reset = 重置）。

        【中文名称】重置
        【功能说明】删除所有 ChromaDB collection，重新导入数据时使用。
        """
        for name in self.client.list_collections():
            collection_name = name.name if hasattr(name, "name") else str(name)
            self.client.delete_collection(collection_name)

    def collection(self, name: str) -> Collection:
        """获取或创建 collection（collection = 获取集合）。

        【中文名称】获取集合

        【功能说明】
        按名称获取 ChromaDB collection，不存在则自动创建。
        使用余弦相似度（cosine）进行向量检索。

        【参数说明】
        - name: collection 名称

        【返回值】
        - Collection: ChromaDB collection 对象
        """
        return self.client.get_or_create_collection(name=name, metadata={"hnsw:space": "cosine"})

    def upsert_chunks(self, collection_name: str, chunks: list[DocumentChunk], batch_size: int = 10) -> int:
        """写入文本块（upsert_chunks = 插入或更新文本块）。

        【中文名称】插入或更新文本块

        【功能说明】
        将文本块向量化后批量写入 ChromaDB。
        upsert = "存在则更新，不存在则插入"。

        【参数说明】
        - collection_name: 目标 collection 名称
        - chunks: 待写入的文本块列表
        - batch_size: 每批处理数量（上限 10）

        【返回值】
        - int: 写入的文本块总数
        """
        collection = self.collection(collection_name)
        count = 0
        batch_size = min(batch_size, 10)  # 限制批次大小，避免 Embedding API 限流
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start : start + batch_size]
            # 先把文本向量化，再写入 ChromaDB
            embeddings = self.embedding.embed_texts([chunk.text for chunk in batch])
            collection.upsert(
                ids=[chunk.id for chunk in batch],  # 文本块唯一 ID
                documents=[chunk.text for chunk in batch],  # 原始文本
                embeddings=embeddings,  # 向量
                metadatas=[chunk.metadata for chunk in batch],  # 元数据（来源、可见性等）
            )
            count += len(batch)
        return count

    def delete_where(self, collection_name: str, where: dict[str, Any]) -> int:
        """按元数据条件删除向量数据。用于删除会话时清理对应的记忆向量。

        参数：
            where：ChromaDB metadata 过滤条件，如 {"session_id": "xxx"}
        返回：
            删除的记录数
        """
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
        """检索相似文本（query = 查询）。

        【中文名称】查询

        【功能说明】
        根据查询文本在指定 collection 中检索最相似的文本块。

        【执行流程】
        1. 将 query 文本向量化
        2. 在 ChromaDB 中用向量做近似最近邻搜索（ANN）
        3. 返回最相似的 n_results 个文本块

        【参数说明】
        - collection_name: 目标 collection
        - query: 查询文本
        - n_results: 返回条数
        - where: 可选的元数据过滤条件

        【返回值】
        - list[dict]: 检索结果列表，每项包含 id、document、metadata、distance
        """
        collection = self.collection(collection_name)
        collection_count = collection.count()
        if collection_count <= 0:
            return []
        n_results = min(n_results, collection_count)  # 不能超过 collection 中的总记录数
        embedding = self.embedding.embed_texts([query])[0]  # 将查询文本向量化
        result = collection.query(query_embeddings=[embedding], n_results=n_results, where=where)
        # ChromaDB 返回的结果是嵌套列表，取第一个查询的结果
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
    """过滤出实际存在的文件路径，跳过缺失的规则书或剧本文件。"""
    return [path for path in paths if path.exists() and path.is_file()]
