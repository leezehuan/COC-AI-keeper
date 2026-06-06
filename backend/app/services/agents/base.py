# =============================================================================
# 【Agent 基类模块】
# =============================================================================
# 这个文件定义了多 Agent 架构中最基础的三个组件。
# 你可以把它们理解为"盖房子用的砖块"——所有专业 Agent 都建立在这些基础之上。
#
# 三个组件分别是：
#
# 1. AgentMessage（消息信封）
#    - 作用：Agent 之间传递数据的"快递包裹"
#    - 类比：就像寄快递需要一个信封，信封上写"发件人、收件人、内容"
#    - 每个 Agent 处理完自己的工作后，把结果装进信封，传给下一个 Agent
#
# 2. AgentContext（共享服务上下文）
#    - 作用：存放所有 Agent 都需要用到的"公共工具"
#    - 包含：LLM 客户端（用来调用大模型）和检索服务（用来查 ChromaDB）
#    - 类比：就像办公室里的公用打印机和饮水机，每个人都能用
#
# 3. BaseAgent（Agent 抽象基类）
#    - 作用：定义所有 Agent 的"统一模板"
#    - 每个专业 Agent 必须实现 run() 方法
#    - 类比：就像所有汽车都有方向盘和油门，但每辆车的内部实现不同
#
# 多 Agent 架构的核心思想：
# - Supervisor（调度器）像"项目经理"，按顺序分配任务给各个 Agent
# - 每个 Agent 像"专业工人"，只负责自己擅长的工作
# - 好处：每个 Agent 可以独立测试、独立修改，互不影响
# =============================================================================
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, TypedDict

from app.services.llm import LLMClient
from app.services.retrieval import RetrievalService


class AgentMessage(TypedDict, total=False):
    """Agent 间传递的消息信封（可以理解为"快递包裹"）。

    【中文名称】消息信封

    【功能说明】
    这是 Agent 之间传递数据的唯一方式。每个 Agent 从信封中取出自己需要的
    输入数据，处理完后把结果放回信封，传给下一个 Agent。

    【为什么需要它】
    旧版使用一个巨大的 KeeperState 字典在所有节点间共享数据，任何节点
    都能修改任何字段，导致调试困难。AgentMessage 让数据流向更清晰：
    每个 Agent 只读取自己需要的字段，只写入自己负责的字段。

    【字段说明】
    - from_agent: 发送方 Agent 的名字，如 "ContextAgent"
    - to_agent:   接收方 Agent 的名字，如 "PlannerAgent"
    - phase:      当前阶段名，如 "context"、"plan"、"execute"
    - payload:    实际数据内容（一个字典），各 Agent 的输入/输出字段不同
    - context_summary: 一句话摘要，让下游 Agent 快速了解当前状态
    - metadata:   附加信息，如调试数据、时间戳等

    total=False 表示所有字段都是可选的——不是每个信封都需要填满所有字段。
    """

    from_agent: str  # 来源 Agent 名称，如 "ContextAgent"
    to_agent: str  # 目标 Agent 名称，如 "PlannerAgent"
    phase: str  # 当前阶段名，如 "context"、"plan"、"execute"
    payload: dict[str, Any]  # 负载数据，各 Agent 的输入/输出字段不同
    context_summary: str  # 上下文摘要，供下游 Agent 快速理解当前状态
    metadata: dict[str, Any]  # 元数据，如调试信息、时间戳等


class AgentContext:
    """所有 Agent 共享的只读服务上下文（可以理解为"公用工具箱"）。

    【中文名称】Agent 共享上下文

    【功能说明】
    存放所有 Agent 都需要使用的公共服务。由 Supervisor 在启动时创建，
    然后注入到每个 Agent 中。Agent 通过 self.context.llm 和
    self.context.retrieval 来使用这些服务。

    【包含的服务】
    - llm: LLM 客户端 → 用来调用大语言模型（生成文本、解析 JSON 等）
    - retrieval: 检索服务 → 用来查询 ChromaDB 向量数据库

    【为什么是"只读"】
    Agent 可以调用这些服务的方法，但不会修改服务本身。
    这保证了所有 Agent 看到的是同一套工具，避免状态不一致。
    """

    def __init__(self, llm: LLMClient, retrieval: RetrievalService) -> None:
        """初始化共享上下文（__init__ = 构造函数/初始化方法）。

        【中文名称】初始化

        【功能说明】
        创建 AgentContext 实例时自动调用，把外部传入的 LLM 客户端和
        检索服务保存到 self 上，供后续使用。

        【参数说明】
        - llm: LLM 客户端实例，用于调用大语言模型
        - retrieval: 检索服务实例，用于查询 ChromaDB

        【返回值】无（返回 None）
        """
        self.llm = llm  # 保存 LLM 客户端，用于调用大语言模型
        self.retrieval = retrieval  # 保存检索服务，用于查询 ChromaDB


class BaseAgent(ABC):
    """专业 Agent 的抽象基类（可以理解为"Agent 模板"）。

    【中文名称】Agent 基类 / Agent 抽象父类

    【功能说明】
    所有专业 Agent（ContextAgent、PlannerAgent 等）都必须继承这个类。
    它定义了 Agent 的基本结构：
    - 每个 Agent 有一个名字（name）
    - 每个 Agent 持有一个共享上下文（context）
    - 每个 Agent 必须实现 run() 方法

    【什么是抽象类（ABC）】
    ABC = Abstract Base Class，抽象基类。
    它不能直接实例化（不能直接创建对象），只能被继承。
    它要求子类必须实现标记了 @abstractmethod 的方法。
    类比："水果"是抽象类，不能直接吃"水果"，只能吃具体的"苹果"或"香蕉"。

    【子类列表】
    - ContextAgent:  上下文加载与意图解析
    - PlannerAgent:  回合计划生成
    - ExecutorAgent: 计划执行与规则检定
    - NarratorAgent: 守秘人叙事生成
    - GuardAgent:    守卫校验与防剧透
    """

    name: str = "BaseAgent"  # Agent 名称，子类应该覆盖这个属性

    def __init__(self, context: AgentContext) -> None:
        """初始化 Agent（__init__ = 构造函数/初始化方法）。

        【中文名称】初始化

        【功能说明】
        创建 Agent 实例时自动调用。接收一个共享上下文并保存到 self.context，
        这样 Agent 的各个方法就可以通过 self.context 使用 LLM 和检索服务。

        【参数说明】
        - context: AgentContext 实例，包含 LLM 客户端和检索服务

        【返回值】无（返回 None）
        """
        self.context = context  # 保存共享服务上下文

    @abstractmethod
    def run(self, envelope: AgentMessage) -> AgentMessage:
        """执行 Agent 的核心逻辑（run = 运行/执行）。

        【中文名称】运行

        【功能说明】
        这是 Agent 最重要的方法。接收一个输入信封，处理其中的数据，
        返回一个输出信封。每个子类必须实现这个方法。

        【为什么是抽象方法】
        @abstractmethod 装饰器强制子类必须实现这个方法。
        如果子类没有实现 run()，Python 会在创建实例时报错。
        这保证了所有 Agent 都有统一的调用接口。

        【参数说明】
        - envelope: 输入的消息信封，payload 中包含该 Agent 需要的输入数据

        【返回值】
        - AgentMessage: 输出的消息信封，payload 中包含该 Agent 的处理结果

        【调用示例】
        result = agent.run(AgentMessage(payload={"db": db, "session_id": "xxx"}))
        output_data = result["payload"]
        """
        ...
