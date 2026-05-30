from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, TypedDict

from app.services.llm import LLMClient
from app.services.retrieval import RetrievalService


class AgentMessage(TypedDict, total=False):
    """Agent 间传递的消息信封。

    每个 Agent 只读取自己需要的 payload 字段，不依赖完整的 KeeperState。
    Supervisor 负责组装信封并路由给下一个 Agent。
    """

    from_agent: str
    to_agent: str
    phase: str
    payload: dict[str, Any]
    context_summary: str
    metadata: dict[str, Any]


class AgentContext:
    """所有 Agent 共享的只读服务上下文，由 Supervisor 初始化并注入。"""

    def __init__(self, llm: LLMClient, retrieval: RetrievalService) -> None:
        self.llm = llm
        self.retrieval = retrieval


class BaseAgent(ABC):
    """专业 Agent 的抽象基类。

    子类只需实现 run(envelope) -> envelope，内部逻辑可完全独立测试。
    """

    name: str = "BaseAgent"

    def __init__(self, context: AgentContext) -> None:
        self.context = context

    @abstractmethod
    def run(self, envelope: AgentMessage) -> AgentMessage:
        """处理输入信封，返回输出信封。

        输入和输出的 payload 字段由各 Agent 的文档约定，Supervisor 负责拼接。
        """
        ...
