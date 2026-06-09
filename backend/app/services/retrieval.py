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
        # 1. 读取项目配置；这里主要使用 chroma_path 来确定向量库落盘目录。
        settings = get_settings()  # settings = 应用配置对象
        # 2. 将配置里的相对路径解析成项目内绝对路径，避免运行目录变化导致 Chroma 写错地方。
        chroma_path = resolve_project_path(settings.chroma_path)  # chroma_path = ChromaDB 持久化目录
        # 3. 确保目录存在；PersistentClient 需要一个可写目录保存索引和元数据。
        chroma_path.mkdir(parents=True, exist_ok=True)  # 确保目录存在
        # PersistentClient：数据持久化到磁盘，重启后不丢失
        self.client = chromadb.PersistentClient(  # client = ChromaDB 持久化客户端
            path=str(chroma_path),  # path = 向量库文件存储位置
            settings=ChromaSettings(anonymized_telemetry=False),  # settings = 禁用匿名遥测
        )
        # 4. 创建向量化客户端；upsert/query 都要先把文本变成 embedding。
        self.embedding = EmbeddingClient()  # 用于将文本转为向量

    def reset(self) -> None:
        """清空所有 collection（reset = 重置）。

        【中文名称】重置
        【功能说明】删除所有 ChromaDB collection，重新导入数据时使用。
        """
        for name in self.client.list_collections():
            # Chroma 不同版本 list_collections 返回值略有差异：可能是对象，也可能是名称字符串。
            collection_name = name.name if hasattr(name, "name") else str(name)  # collection_name = 兼容后的集合名称
            # 删除整个 collection；导入接口 reset_chroma=True 时会用到它。
            self.client.delete_collection(collection_name)  # delete_collection = 删除该集合全部向量数据

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
        # get_or_create_collection 保证调用方不用关心集合是否已存在。
        return self.client.get_or_create_collection(name=name, metadata={"hnsw:space": "cosine"})  # hnsw:space=cosine 表示按余弦距离检索

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
        # 1. 获取目标 collection；不存在时自动创建。
        collection = self.collection(collection_name)  # collection = 目标向量集合
        # 2. count 记录成功写入的文本块数量，最终返回给导入接口。
        count = 0  # count = 已写入块数量
        # 3. 限制批次大小，避免一次向 Embedding API 发送过多文本导致限流或超时。
        batch_size = min(batch_size, 10)  # batch_size = 实际每批处理数量
        for start in range(0, len(chunks), batch_size):
            # 4. 按 batch_size 切分文本块；start 是当前批次的起始下标。
            batch = chunks[start : start + batch_size]  # batch = 当前要向量化并写入的一批 DocumentChunk
            # 先把文本向量化，再写入 ChromaDB
            embeddings = self.embedding.embed_texts([chunk.text for chunk in batch])  # embeddings = 当前批次文本对应的向量列表
            collection.upsert(  # upsert = 按 id 插入或覆盖，重复导入不会产生重复块
                ids=[chunk.id for chunk in batch],  # 文本块唯一 ID
                documents=[chunk.text for chunk in batch],  # 原始文本
                embeddings=embeddings,  # 向量
                metadatas=[chunk.metadata for chunk in batch],  # 元数据（来源、可见性等）
            )
            # 5. 累计本批写入数量。
            count += len(batch)  # count += 当前批次大小
        # 6. 返回写入总数，import_default_content 会把它放进导入结果。
        return count

    def delete_where(self, collection_name: str, where: dict[str, Any]) -> int:
        """按元数据条件删除向量数据。用于删除会话时清理对应的记忆向量。

        参数：
            where：ChromaDB metadata 过滤条件，如 {"session_id": "xxx"}
        返回：
            删除的记录数
        """
        # 1. 获取目标 collection；即使集合不存在，也会被创建为空集合。
        collection = self.collection(collection_name)  # collection = 要清理的向量集合
        # 2. 空集合直接返回 0，避免后续 get/delete 做无意义工作。
        if collection.count() <= 0:
            return 0
        # 3. 先按 metadata 条件查出匹配 id；Chroma delete 需要明确 id 列表。
        result = collection.get(where=where)  # result = 符合过滤条件的记录
        # 4. 提取匹配 id；没有命中时直接返回 0。
        ids = result.get("ids", [])  # ids = 待删除向量 id 列表
        if not ids:
            return 0
        # 5. 按 id 删除向量；删除会话时用于清理 session_memory_chunks。
        collection.delete(ids=ids)  # delete = 删除匹配向量
        # 6. 返回删除数量，API 会把它展示为 deleted_memory_chunks。
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
        # 1. 获取目标 collection；Agent/Tool 不需要关心集合是否预先创建。
        collection = self.collection(collection_name)  # collection = 要查询的向量集合
        # 2. 读取集合内记录数；空集合没有可检索内容，直接返回空列表。
        collection_count = collection.count()  # collection_count = 该集合中文档块数量
        if collection_count <= 0:
            return []
        # 3. Chroma 要求返回条数不能超过集合总量，所以这里做上限保护。
        n_results = min(n_results, collection_count)  # n_results = 实际返回条数上限
        # 4. 将自然语言查询转换为向量；后续用它做 ANN 相似度搜索。
        embedding = self.embedding.embed_texts([query])[0]  # embedding = 查询文本向量
        # 5. 执行向量查询；where 可限制 session_id 等 metadata 条件。
        result = collection.query(query_embeddings=[embedding], n_results=n_results, where=where)  # result = Chroma 原始查询结果
        # ChromaDB 返回的结果是嵌套列表，取第一个查询的结果
        # 6. rows 是本项目统一使用的扁平结果结构，方便 ContextAgent/Tool 处理。
        rows: list[dict[str, Any]] = []  # rows = 规范化后的检索结果列表
        # 7. 取第一个查询对应的 id/document/metadata/distance 列表；本函数一次只查一个 query。
        ids = result.get("ids", [[]])[0]  # ids = 命中文档 id 列表
        documents = result.get("documents", [[]])[0]  # documents = 命中文本内容列表
        metadatas = result.get("metadatas", [[]])[0]  # metadatas = 命中文档元数据列表
        distances = result.get("distances", [[]])[0]  # distances = 余弦距离，越小通常越相似
        for index, doc_id in enumerate(ids):
            # 8. 将 Chroma 的并行数组合并成一条 dict，减少下游按 index 对齐的负担。
            rows.append(  # rows += 单条命中结果
                {
                    "id": doc_id,  # id = 文档块唯一 id
                    "document": documents[index],  # document = 原始文本片段
                    "metadata": metadatas[index] or {},  # metadata = 来源、可见性、标题等信息
                    "distance": distances[index] if index < len(distances) else None,  # distance = 相似度距离，可能因版本缺失
                }
            )
        # 9. 返回统一结构，供 RAG prompt、ToolObservation 和引用生成使用。
        return rows


def existing_source_paths(paths: list[Path]) -> list[Path]:
    """过滤出实际存在的文件路径，跳过缺失的规则书或剧本文件。"""
    return [path for path in paths if path.exists() and path.is_file()]
