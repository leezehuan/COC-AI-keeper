import hashlib
import json
import math
from typing import Any

from openai import OpenAI

from app.config import get_settings


class LLMClient:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.client = OpenAI(api_key=self.settings.llm_api_key or "empty", base_url=self.settings.llm_base_url or None)

    def chat_text(self, messages: list[dict[str, str]], temperature: float | None = None) -> str:
        if not is_configured(self.settings.llm_api_key, self.settings.llm_model):
            return ""
        response = self.client.chat.completions.create(
            model=self.settings.llm_model,
            messages=messages,
            temperature=self.settings.llm_temperature if temperature is None else temperature,
        )
        return response.choices[0].message.content or ""

    def chat_json(self, messages: list[dict[str, str]], fallback: dict[str, Any]) -> dict[str, Any]:
        text = self.chat_text(messages, temperature=0.2)
        if not text:
            return fallback
        try:
            return json.loads(extract_json(text))
        except json.JSONDecodeError:
            return fallback


class EmbeddingClient:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.client = OpenAI(api_key=self.settings.embedding_api_key or "empty", base_url=self.settings.embedding_base_url)

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if not is_configured(self.settings.embedding_api_key, self.settings.embedding_model):
            return [fallback_embedding(text, self.settings.embedding_dimensions or 1024) for text in texts]
        kwargs: dict[str, Any] = {"model": self.settings.embedding_model, "input": texts}
        if self.settings.embedding_dimensions:
            kwargs["dimensions"] = self.settings.embedding_dimensions
        response = self.client.embeddings.create(**kwargs)
        return [item.embedding for item in response.data]


def extract_json(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end >= start:
        return stripped[start : end + 1]
    return stripped


def is_configured(api_key: str, model: str) -> bool:
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
    values: list[float] = []
    seed = text.encode("utf-8", errors="ignore")
    counter = 0
    while len(values) < dimensions:
        digest = hashlib.sha256(seed + str(counter).encode("ascii")).digest()
        values.extend((byte / 127.5) - 1.0 for byte in digest)
        counter += 1
    vector = values[:dimensions]
    norm = math.sqrt(sum(item * item for item in vector)) or 1.0
    return [item / norm for item in vector]
