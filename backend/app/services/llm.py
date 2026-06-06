# 【LLM 与 Embedding 客户端】
# 这个文件封装了与大语言模型（LLM）和文本向量化（Embedding）的交互。
# 对初学者来说，核心概念：
# - LLMClient：调用 LLM 生成文本或 JSON 响应（守秘人叙事、意图解析等）。
# - EmbeddingClient：将文本转为向量，用于 RAG 检索时的相似度搜索。
# - extract_json：从 LLM 返回的文本中提取 JSON（LLM 有时会在 JSON 外加 ``` 或多余文字）。
# - fallback_embedding：当 Embedding API 不可用时，用 SHA256 哈希生成伪向量，保证系统可启动。
import hashlib
import json
import math
from typing import Any

from openai import OpenAI  # OpenAI 兼容的 Python SDK，支持任何兼容接口

from app.config import get_settings


class LLMClient:
    """大语言模型客户端（可以理解为"AI 对话接口"）。

    【中文名称】LLM 客户端

    【功能说明】封装 OpenAI 兼容接口的调用逻辑。

    两种调用方式：
    - chat_text：返回纯文本，用于叙事生成等场景。
    - chat_json：返回 JSON 对象，用于意图解析、计划生成等需要结构化输出的场景。
    """

    def __init__(self) -> None:
        """初始化 LLM 客户端（__init__ = 构造函数）。

        【中文名称】初始化
        【功能说明】创建 OpenAI 兼容客户端实例。
        """
        self.settings = get_settings()
        # 使用 OpenAI SDK，但 base_url 可以指向任何兼容服务（如千问、DeepSeek 等）
        self.client = OpenAI(api_key=self.settings.llm_api_key or "empty", base_url=self.settings.llm_base_url or None)

    def chat_text(self, messages: list[dict[str, str]], temperature: float | None = None) -> str:
        """生成文本响应（chat_text = 文本对话）。

        【中文名称】文本对话

        【功能说明】
        调用 LLM 生成纯文本响应，用于叙事生成等场景。

        【参数说明】
        - messages: OpenAI 格式的消息列表
        - temperature: 生成温度，None 时使用配置默认值

        【返回值】
        - str: LLM 生成的文本；API 未配置时返回空字符串
        """
        if not is_configured(self.settings.llm_api_key, self.settings.llm_model):
            return ""
        response = self.client.chat.completions.create(
            model=self.settings.llm_model,
            messages=messages,
            temperature=self.settings.llm_temperature if temperature is None else temperature,
        )
        return response.choices[0].message.content or ""

    def chat_json(self, messages: list[dict[str, str]], fallback: dict[str, Any]) -> dict[str, Any]:
        """生成 JSON 响应（chat_json = JSON 对话）。

        【中文名称】JSON 对话

        【功能说明】
        调用 LLM 并解析为 JSON，用于意图解析、计划生成等场景。

        【参数说明】
        - messages: 消息列表
        - fallback: 解析失败时的兜底返回值

        【返回值】
        - dict: 解析后的 JSON 字典；解析失败则返回 fallback
        """
        text = self.chat_text(messages, temperature=0.2)  # JSON 输出用低温度提高确定性
        if not text:
            return fallback
        try:
            return json.loads(extract_json(text))
        except json.JSONDecodeError:
            return fallback


class EmbeddingClient:
    """文本向量化客户端：将文本转为浮点向量，用于 RAG 检索的相似度计算。

    工作原理：
    - 把一段文本（如"灯塔地下室"）转成一个 1024 维的浮点向量。
    - 语义相近的文本，向量距离也近；这样就能通过向量距离找到相关内容。
    - 当 API 不可用时，使用 fallback_embedding 生成伪向量，保证系统可启动。
    """

    def __init__(self) -> None:
        """初始化 LLM 客户端（__init__ = 构造函数）。

        【中文名称】初始化
        【功能说明】创建 OpenAI 兼容客户端实例。
        """
        self.settings = get_settings()
        self.client = OpenAI(api_key=self.settings.embedding_api_key or "empty", base_url=self.settings.embedding_base_url)

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """文本转向量（embed_texts = 向量化文本）。

        【中文名称】向量化文本

        【功能说明】
        将文本列表转为向量列表，每个向量是一个浮点数列表。
        API 不可用时使用基于哈希的伪向量。

        【参数说明】
        - texts: 待向量化的文本列表

        【返回值】
        - list[list[float]]: 对应的向量列表
        """
        if not texts:
            return []
        # API 未配置时使用基于哈希的伪向量，保证系统可启动但检索质量较低
        if not is_configured(self.settings.embedding_api_key, self.settings.embedding_model):
            return [fallback_embedding(text, self.settings.embedding_dimensions or 1024) for text in texts]
        kwargs: dict[str, Any] = {"model": self.settings.embedding_model, "input": texts}
        if self.settings.embedding_dimensions:
            kwargs["dimensions"] = self.settings.embedding_dimensions  # 指定向量维度
        response = self.client.embeddings.create(**kwargs)
        return [item.embedding for item in response.data]


def extract_json(text: str) -> str:
    """从 LLM 返回的文本中提取 JSON 部分。

    LLM 有时会在 JSON 外加 markdown 代码块（```json ... ```）或多余文字。
    这个函数先去除代码块标记，再找到第一个 { 和最后一个 } 之间的内容。
    """
    stripped = text.strip()
    # 去除 markdown 代码块标记
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]  # 去除开头的 ```json
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]  # 去除结尾的 ```
        stripped = "\n".join(lines).strip()
    # 找到最外层的 { ... }
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end >= start:
        return stripped[start : end + 1]
    return stripped


def is_configured(api_key: str, model: str) -> bool:
    """检查 API 密钥和模型名是否已正确配置（非空且非占位符）。

    初学者注意：.env 文件中的占位符值（如"replace-with-your-key"）会被识别为未配置。
    """
    invalid_tokens = {
        "",
        "replace-with-your-openai-compatible-key",
        "replace-with-your-chat-model",
        "replace-with-your-qwen-api-key",
        "请填写你的聊天模型密钥",
        "请填写你的聊天模型名称",
        "请填写你的千问密钥",
    }
    return api_key.strip() not in invalid_tokens and model.strip() not in invalid_tokens


def fallback_embedding(text: str, dimensions: int) -> list[float]:
    """当 Embedding API 不可用时，基于 SHA256 哈希生成伪向量。

    这个伪向量没有语义信息，但保证：
    - 相同文本总是生成相同向量（确定性）
    - 向量已归一化（长度为 1），不会导致检索系统报错
    - 系统可以在没有 Embedding API 的情况下启动和运行
    """
    values: list[float] = []
    seed = text.encode("utf-8", errors="ignore")
    counter = 0
    while len(values) < dimensions:
        digest = hashlib.sha256(seed + str(counter).encode("ascii")).digest()
        values.extend((byte / 127.5) - 1.0 for byte in digest)  # 字节值映射到 [-1, 1]
        counter += 1
    vector = values[:dimensions]
    # L2 归一化：让向量长度为 1，与真实 Embedding 向量格式一致
    norm = math.sqrt(sum(item * item for item in vector)) or 1.0
    return [item / norm for item in vector]
