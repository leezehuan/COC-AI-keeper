# 【阅读顺序 3：数据库模型】
# 这个文件定义了所有数据库表对应的 Python 类（ORM 模型）。
# 对初学者来说，每个类 = 一张数据库表，每个属性 = 一列。
# SQLAlchemy 会根据这些类自动生成 CREATE TABLE 语句。
#
# 核心表关系：
#   Scenario（剧本）──1:N── Character（角色）
#   Scenario ──1:N── GameSession（会话）
#   Character ──1:N── GameSession
#   GameSession ──1:N── TurnLog（回合日志）
#   GameSession ──1:N── Clue（线索）
#   GameSession ──1:N── InventoryItem（物品）
#   GameSession ──1:N── StoryFlag（剧情标记）
from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB  # PostgreSQL 专用的 JSON 列类型
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def new_id() -> str:
    """生成 UUID 作为主键，避免自增 ID 暴露业务信息。"""
    return str(uuid4())


class Scenario(Base):
    """剧本：一个独立的冒险故事（如"无光的灯塔"），包含角色和会话。"""
    __tablename__ = "scenarios"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)  # id = 主键：UUID 唯一标识
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)  # name = 剧本名：唯一，如"无光的灯塔"
    source_path: Mapped[str | None] = mapped_column(Text)  # source_path = 源文件路径：剧本 Markdown 文件路径
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)  # metadata_ = 扩展元数据：JSON 格式的附加信息
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)  # created_at = 创建时间

    # relationship 定义反向关联：Scenario.characters 可直接访问关联的所有 Character 对象
    characters: Mapped[list["Character"]] = relationship(back_populates="scenario")
    sessions: Mapped[list["GameSession"]] = relationship(back_populates="scenario")


class Character(Base):
    """角色卡：CoC 规则中的调查员，包含属性、技能、物品和背景。
    角色卡在导入时从 xlsx 文件读取，创建会话时关联到 GameSession。
    """
    __tablename__ = "characters"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    scenario_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("scenarios.id"))  # 所属剧本
    name: Mapped[str] = mapped_column(String(200), nullable=False)  # 角色名
    archetype: Mapped[str] = mapped_column(String(200), nullable=False)  # 原型，如"调查局探员"、"古董商"
    occupation: Mapped[str | None] = mapped_column(String(200))  # 职业
    # ===== CoC 核心数值 =====
    hp_current: Mapped[int] = mapped_column(Integer, default=10, nullable=False)  # hp_current = 当前生命值
    hp_max: Mapped[int] = mapped_column(Integer, default=10, nullable=False)  # hp_max = 最大生命值
    san_current: Mapped[int] = mapped_column(Integer, default=60, nullable=False)  # san_current = 当前理智值
    san_max: Mapped[int] = mapped_column(Integer, default=99, nullable=False)  # san_max = 最大理智值
    mp_current: Mapped[int] = mapped_column(Integer, default=10, nullable=False)  # mp_current = 当前魔法点
    mp_max: Mapped[int] = mapped_column(Integer, default=10, nullable=False)  # mp_max = 最大魔法点
    luck: Mapped[int] = mapped_column(Integer, default=50, nullable=False)  # luck = 幸运值
    # ===== JSON 扩展字段 =====
    attributes: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)  # attributes = 属性字典：力量、敏捷等
    skills: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)  # skills = 技能字典：{侦查:60, 聆听:50}
    inventory: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)  # inventory = 初始物品列表
    background: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)  # background = 背景故事
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    scenario: Mapped[Scenario | None] = relationship(back_populates="characters")
    sessions: Mapped[list["GameSession"]] = relationship(back_populates="character")


class GameSession(Base):
    """游戏会话：一次完整的游戏体验，包含当前场景状态、线索、物品等。
    每次玩家"开始新游戏"就会创建一个 GameSession，所有回合操作都在此会话下进行。
    """
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)  # id = 主键
    scenario_id: Mapped[str] = mapped_column(String(36), ForeignKey("scenarios.id"), nullable=False)  # scenario_id = 所属剧本ID
    character_id: Mapped[str] = mapped_column(String(36), ForeignKey("characters.id"), nullable=False)  # character_id = 使用的角色ID
    title: Mapped[str] = mapped_column(String(200), default="无光的灯塔", nullable=False)  # title = 会话标题
    # ===== 当前场景状态 =====
    current_location: Mapped[str] = mapped_column(String(200), default="波浪起伏的水面", nullable=False)  # current_location = 当前地点
    current_scene: Mapped[str] = mapped_column(String(200), default="导入", nullable=False)  # current_scene = 当前场景名
    current_time: Mapped[str] = mapped_column(String(100), default="1926-04-12 20:15", nullable=False)  # current_time = 游戏内时间
    story_phase: Mapped[str] = mapped_column(String(100), default="opening", nullable=False)  # story_phase = 剧情阶段：opening/act1/act2/climax
    danger_level: Mapped[int] = mapped_column(Integer, default=1, nullable=False)  # danger_level = 敌对势力警觉等级 1-5
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)  # summary = 会话摘要，供LLM上下文使用
    # state = 结构化剧情状态：大型JSONB字段，详见story_state.py中ensure_story_state()的结构定义
    state: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)  # state = 结构化剧情状态
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)  # created_at = 创建时间
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)  # updated_at = 自动更新时间

    # ===== 关联关系 =====
    # cascade="all, delete-orphan" 表示删除会话时级联删除这些子记录
    scenario: Mapped[Scenario] = relationship(back_populates="sessions")
    character: Mapped[Character] = relationship(back_populates="sessions")
    turn_logs: Mapped[list["TurnLog"]] = relationship(back_populates="session", cascade="all, delete-orphan")
    clues: Mapped[list["Clue"]] = relationship(back_populates="session", cascade="all, delete-orphan")
    inventory_items: Mapped[list["InventoryItem"]] = relationship(back_populates="session", cascade="all, delete-orphan")
    flags: Mapped[list["StoryFlag"]] = relationship(back_populates="session", cascade="all, delete-orphan")


class TurnLog(Base):
    """回合日志：记录每个回合的完整信息，包括玩家输入、意图、检索结果、骰点和叙事。
    这是守秘人 Agent 的"审计记录"，可用于调试和回放。
    """
    __tablename__ = "turn_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)  # id = 主键
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id"), nullable=False)  # session_id = 所属会话ID
    turn_index: Mapped[int] = mapped_column(Integer, nullable=False)  # turn_index = 回合序号（从1开始递增）
    player_input: Mapped[str] = mapped_column(Text, nullable=False)  # player_input = 玩家输入的原始文本
    intent: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)  # intent = LLM解析的结构化意图
    retrieval: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)  # retrieval = 检索结果和调试摘要
    dice_results: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)  # dice_results = 骰点结果列表
    keeper_response: Mapped[str] = mapped_column(Text, nullable=False)  # keeper_response = 守秘人叙事文本
    state_delta: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)  # state_delta = 本回合状态增量
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)  # image_url = 场景图片URL
    image_metadata: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)  # image_metadata = 图片生成元数据
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)  # created_at = 创建时间

    session: Mapped[GameSession] = relationship(back_populates="turn_logs")


class Clue(Base):
    """线索：玩家在游戏中发现的线索，由守秘人 Agent 在回合中创建。
    UniqueConstraint 保证同一会话中同一线索不会重复创建（幂等去重）。
    """
    __tablename__ = "clues"
    __table_args__ = (UniqueConstraint("session_id", "clue_key", name="uq_session_clue_key"),)  # 联合唯一约束

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)  # id = 主键
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id"), nullable=False)  # session_id = 所属会话ID
    clue_key: Mapped[str] = mapped_column(String(200), nullable=False)  # clue_key = 线索唯一键（用于去重）
    name: Mapped[str] = mapped_column(String(200), nullable=False)  # name = 线索名称
    content: Mapped[str] = mapped_column(Text, nullable=False)  # content = 线索内容描述
    source_location: Mapped[str | None] = mapped_column(String(200))  # source_location = 发现地点
    discovered_turn: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # discovered_turn = 发现时的回合序号
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)  # metadata_ = 扩展元数据
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)  # created_at = 创建时间

    session: Mapped[GameSession] = relationship(back_populates="clues")


class InventoryItem(Base):
    """物品栏：玩家持有的物品，可从角色初始物品同步或由守秘人回合中动态增减。
    """
    __tablename__ = "inventory_items"
    __table_args__ = (UniqueConstraint("session_id", "item_key", name="uq_session_item_key"),)  # 联合唯一

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)  # id = 主键
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id"), nullable=False)  # session_id = 所属会话ID
    item_key: Mapped[str] = mapped_column(String(200), nullable=False)  # item_key = 物品唯一键（用于去重）
    name: Mapped[str] = mapped_column(String(200), nullable=False)  # name = 物品名称
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)  # description = 物品描述
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)  # quantity = 数量
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)  # metadata_ = 扩展元数据
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)  # created_at = 创建时间

    session: Mapped[GameSession] = relationship(back_populates="inventory_items")


class StoryFlag(Base):
    """剧情标记：记录关键剧情节点的状态，如"是否已进入灯塔"、"是否已遇见NPC"等。
    用于跨回合持久化布尔型或结构化的剧情分支状态。
    """
    __tablename__ = "story_flags"
    __table_args__ = (UniqueConstraint("session_id", "key", name="uq_session_flag_key"),)  # 联合唯一

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)  # id = 主键
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id"), nullable=False)  # session_id = 所属会话ID
    key: Mapped[str] = mapped_column(String(200), nullable=False)  # key = 标记键名，如 "已进入灯塔"
    value: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)  # value = 标记值（JSON结构）
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)  # created_at = 创建时间
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)  # updated_at = 自动更新时间

    session: Mapped[GameSession] = relationship(back_populates="flags")
