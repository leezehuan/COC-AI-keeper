# 【阅读顺序 4：API 数据模型（Pydantic Schemas）】
# 这个文件定义了 API 请求和响应的数据结构（Schema）。
# 对初学者来说，可以把它理解为"前端和后端之间的数据契约"：
# - *Out 后缀：响应模型，从 ORM 对象序列化成 JSON 返回给前端。
# - *In 后缀：请求模型，前端发来的 JSON 被解析和校验后传入业务逻辑。
# - from_attributes=True：允许直接从 SQLAlchemy ORM 对象构造 Pydantic 模型。
#
# 与 models.py 的区别：models.py 定义数据库表结构，schemas.py 定义 API 传输结构。
# 两者字段可能重叠，但目的不同——models.py 面向数据库，schemas.py 面向网络传输。
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ScenarioOut(BaseModel):
    """剧本响应模型：返回给前端的剧本信息。"""
    model_config = ConfigDict(from_attributes=True)  # 允许从 ORM 对象构造

    id: str  # id = 主键
    name: str  # name = 剧本名
    source_path: str | None = None  # source_path = 源文件路径
    metadata_: dict[str, Any] = Field(default_factory=dict)  # metadata_ = 扩展元数据
    created_at: datetime  # created_at = 创建时间


class CharacterOut(BaseModel):
    """角色响应模型：返回给前端的角色卡信息，包含属性、技能和背景。"""
    model_config = ConfigDict(from_attributes=True)

    id: str  # id = 主键
    scenario_id: str | None = None  # scenario_id = 所属剧本ID
    name: str  # name = 角色名
    archetype: str  # archetype = 原型
    occupation: str | None = None  # occupation = 职业
    hp_current: int  # hp_current = 当前生命值
    hp_max: int  # hp_max = 最大生命值
    san_current: int  # san_current = 当前理智值
    san_max: int  # san_max = 最大理智值
    mp_current: int  # mp_current = 当前魔法点
    mp_max: int  # mp_max = 最大魔法点
    luck: int  # luck = 幸运值
    attributes: dict[str, Any]  # attributes = 属性字典
    skills: dict[str, Any]  # skills = 技能字典
    inventory: list[Any]  # inventory = 初始物品
    background: dict[str, Any]  # background = 背景故事


class ClueOut(BaseModel):
    """线索响应模型：返回给前端的已发现线索信息。"""
    model_config = ConfigDict(from_attributes=True)

    id: str  # id = 主键
    clue_key: str  # clue_key = 线索唯一键
    name: str  # name = 线索名
    content: str  # content = 线索内容
    source_location: str | None = None  # source_location = 发现地点
    discovered_turn: int  # discovered_turn = 发现回合
    metadata_: dict[str, Any] = Field(default_factory=dict)  # metadata_ = 扩展元数据
    created_at: datetime  # created_at = 创建时间


class InventoryItemOut(BaseModel):
    """物品响应模型：返回给前端的物品栏信息。"""
    model_config = ConfigDict(from_attributes=True)

    id: str  # id = 主键
    item_key: str  # item_key = 物品唯一键
    name: str  # name = 物品名
    description: str  # description = 物品描述
    quantity: int  # quantity = 数量
    metadata_: dict[str, Any] = Field(default_factory=dict)  # metadata_ = 扩展元数据


class StoryFlagOut(BaseModel):
    """剧情标记响应模型：返回给前端的关键剧情节点状态。"""
    model_config = ConfigDict(from_attributes=True)

    key: str  # key = 标记键名
    value: dict[str, Any]  # value = 标记值


class TurnLogOut(BaseModel):
    """回合日志响应模型：返回给前端的单回合完整记录。"""
    model_config = ConfigDict(from_attributes=True)

    id: str  # id = 主键
    turn_index: int  # turn_index = 回合序号
    player_input: str  # player_input = 玩家输入
    intent: dict[str, Any]  # intent = 结构化意图
    retrieval: dict[str, Any]  # retrieval = 检索结果摘要
    dice_results: list[Any]  # dice_results = 骰点结果
    keeper_response: str  # keeper_response = 守秘人叙事
    state_delta: dict[str, Any]  # state_delta = 状态增量
    image_url: str | None = None  # image_url = 场景图片
    image_metadata: dict[str, Any] = Field(default_factory=dict)  # image_metadata = 图片元数据
    created_at: datetime  # created_at = 创建时间


class SessionCreate(BaseModel):
    """创建会话请求模型：前端发送角色 ID 和会话标题。"""
    character_id: str | None = None  # 选择的角色 ID，None 表示使用默认角色
    title: str = "无光的灯塔"  # 会话标题


class SessionOut(BaseModel):
    """会话响应模型：返回给前端的完整会话信息，包含角色、线索、物品和最近回合。"""
    model_config = ConfigDict(from_attributes=True)

    id: str  # id = 主键
    scenario_id: str  # scenario_id = 所属剧本
    character_id: str  # character_id = 使用的角色
    title: str  # title = 会话标题
    current_location: str  # current_location = 当前地点
    current_scene: str  # current_scene = 当前场景
    current_time: str  # current_time = 游戏内时间
    story_phase: str  # story_phase = 剧情阶段
    danger_level: int  # danger_level = 敌对势力警觉等级
    summary: str  # summary = 会话摘要
    state: dict[str, Any]  # state = 结构化剧情状态
    created_at: datetime  # created_at = 创建时间
    updated_at: datetime  # updated_at = 更新时间
    # 嵌套的关联数据，前端一次请求就能拿到完整信息
    character: CharacterOut  # character = 角色卡
    clues: list[ClueOut]  # clues = 已发现线索
    inventory_items: list[InventoryItemOut]  # inventory_items = 物品栏
    flags: list[StoryFlagOut]  # flags = 剧情标记
    recent_turns: list[TurnLogOut] = Field(default_factory=list)  # recent_turns = 最近回合日志


class PlayerActionIn(BaseModel):
    """玩家行动请求模型：前端发送玩家的行动文本。
    min_length=1 确保不为空，max_length=4000 防止超长输入。
    """
    message: str = Field(min_length=1, max_length=4000)


class DiceResultOut(BaseModel):
    """骰点结果响应模型：如 1d100 => expression='1d100', rolls=[42], total=42。"""
    expression: str  # expression = 骰点表达式，如 "1d100"
    rolls: list[int]  # rolls = 每个骰子的原始值
    modifier: int = 0  # modifier = 修正值
    total: int  # total = 最终结果


class SkillCheckOut(BaseModel):
    """技能检定响应模型：如 侦查 60 vs roll=42 => 成功。"""
    skill: str  # skill = 技能名
    skill_value: int  # skill_value = 技能数值
    difficulty: str  # difficulty = 难度等级
    roll: int  # roll = 骰点结果
    success_level: str  # success_level = 成功等级
    success: bool  # success = 是否成功


class ActionResponse(BaseModel):
    """行动响应模型：守秘人 Agent 处理完一个回合后返回给前端的完整结果。"""
    session: SessionOut  # session = 更新后的会话状态
    narration: str  # narration = 守秘人叙事文本
    options: list[str]  # options = 下一步选项
    dice_results: list[dict[str, Any]]  # dice_results = 骰点结果
    skill_checks: list[dict[str, Any]]  # skill_checks = 技能检定结果
    sanity_checks: list[dict[str, Any]]  # sanity_checks = 理智检定结果
    discovered_clues: list[ClueOut]  # discovered_clues = 本回合新发现的线索
    state_delta: dict[str, Any]  # state_delta = 本回合状态增量
    needs_clarification: bool = False  # needs_clarification = 是否需要玩家澄清行动
    needs_image: bool = False  # needs_image = 是否需要生成场景图片
    image_aspect_ratio: str = ""  # image_aspect_ratio = 图片宽高比
    image_url: str | None = None  # image_url = 场景图片URL
    image_metadata: dict[str, Any] = Field(default_factory=dict)  # image_metadata = 图片元数据


class AssistantChatRequest(BaseModel):
    """游戏助手聊天请求模型：前端发送问题和检索参数。
    MQE（Multi-Query Expansion）：用 LLM 扩展查询，提高检索召回率。
    HyDE（Hypothetical Document Embedding）：用 LLM 生成假设性文档来辅助检索。
    """
    session_id: str | None = None  # session_id = 关联的会话ID
    message: str = Field(min_length=1, max_length=4000)  # message = 玩家问题
    mode: str = "auto"  # mode = 助手模式：auto/rules/session_help
    enable_mqe: bool = True  # enable_mqe = 是否启用多查询扩展
    mqe_expansions: int = Field(default=2, ge=0, le=3)  # mqe_expansions = 扩展查询数量
    enable_hyde: bool | None = None  # enable_hyde = 是否启用HyDE
    top_k: int = Field(default=5, ge=1, le=12)  # top_k = 检索返回条数
    candidate_pool_multiplier: int = Field(default=4, ge=1, le=8)  # candidate_pool_multiplier = 候选池倍数


class AssistantCitationOut(BaseModel):
    """助手引用响应模型：回答中引用的资料来源信息。"""
    id: str = ""  # id = 文档ID
    title: str = ""  # title = 文档标题
    source_type: str = ""  # source_type = 来源类型：rulebook/scenario/memory
    citation: str = ""  # citation = 引用标注文本
    snippet: str = ""  # snippet = 引用片段摘要


class AssistantChatResponse(BaseModel):
    """游戏助手聊天响应模型：包含回答、引用和检索调试信息。"""
    answer: str  # answer = 助手回答文本
    citations: list[AssistantCitationOut] = Field(default_factory=list)  # citations = 引用列表
    retrieval_debug: dict[str, Any] = Field(default_factory=dict)  # retrieval_debug = 检索调试信息
    spoiler_blocked: bool = False  # spoiler_blocked = 是否拦截了剧透内容
    mode: str = "auto"  # mode = 实际使用的助手模式


class AgentTraceRunOut(BaseModel):
    """Agent 监控运行响应模型。"""
    model_config = ConfigDict(from_attributes=True)

    id: str
    session_id: str | None = None
    source: str
    status: str
    metadata_: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime
    ended_at: datetime | None = None


class AgentTraceRecordOut(BaseModel):
    """Agent 监控步骤响应模型。"""
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str
    sequence: int
    session_id: str | None = None
    source: str
    agent_name: str
    step_name: str
    phase: str
    status: str
    input_payload: dict[str, Any] = Field(default_factory=dict)
    output_payload: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    duration_ms: int | None = None
    created_at: datetime


class AgentTraceSettingsOut(BaseModel):
    """Agent 监控配置与当前存量。"""

    max_records: int
    record_count: int
    run_count: int


class AgentTraceSettingsUpdate(BaseModel):
    """更新 Agent 监控全局配置。"""

    max_records: int = Field(ge=0, le=200000)


class ImportRequest(BaseModel):
    """数据导入请求模型：控制是否重置向量库和导入角色卡。"""
    reset_chroma: bool = False  # reset_chroma = 是否重置Chroma向量库
    import_characters: bool = True  # import_characters = 是否导入角色卡


class ImportResponse(BaseModel):
    """数据导入响应模型：返回导入的各类数据数量。"""
    scenario_id: str  # scenario_id = 剧本ID
    scenario_chunks: int  # scenario_chunks = 导入的剧本文本块数
    rule_chunks: int  # rule_chunks = 导入的规则文本块数
    scenario_entities: int = 0  # scenario_entities = 导入的实体数
    clue_index: int = 0  # clue_index = 导入的线索索引数
    characters: int  # characters = 导入的角色数
