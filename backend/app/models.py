from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def new_id() -> str:
    return str(uuid4())


class Scenario(Base):
    __tablename__ = "scenarios"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    source_path: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    characters: Mapped[list["Character"]] = relationship(back_populates="scenario")
    sessions: Mapped[list["GameSession"]] = relationship(back_populates="scenario")


class Character(Base):
    __tablename__ = "characters"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    scenario_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("scenarios.id"))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    archetype: Mapped[str] = mapped_column(String(200), nullable=False)
    occupation: Mapped[str | None] = mapped_column(String(200))
    hp_current: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    hp_max: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    san_current: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    san_max: Mapped[int] = mapped_column(Integer, default=99, nullable=False)
    mp_current: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    mp_max: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    luck: Mapped[int] = mapped_column(Integer, default=50, nullable=False)
    attributes: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    skills: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    inventory: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    background: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    scenario: Mapped[Scenario | None] = relationship(back_populates="characters")
    sessions: Mapped[list["GameSession"]] = relationship(back_populates="character")


class GameSession(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    scenario_id: Mapped[str] = mapped_column(String(36), ForeignKey("scenarios.id"), nullable=False)
    character_id: Mapped[str] = mapped_column(String(36), ForeignKey("characters.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(200), default="无光的灯塔", nullable=False)
    current_location: Mapped[str] = mapped_column(String(200), default="波浪起伏的水面", nullable=False)
    current_scene: Mapped[str] = mapped_column(String(200), default="导入", nullable=False)
    current_time: Mapped[str] = mapped_column(String(100), default="1926-04-12 20:15", nullable=False)
    story_phase: Mapped[str] = mapped_column(String(100), default="opening", nullable=False)
    danger_level: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    state: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    scenario: Mapped[Scenario] = relationship(back_populates="sessions")
    character: Mapped[Character] = relationship(back_populates="sessions")
    turn_logs: Mapped[list["TurnLog"]] = relationship(back_populates="session", cascade="all, delete-orphan")
    clues: Mapped[list["Clue"]] = relationship(back_populates="session", cascade="all, delete-orphan")
    inventory_items: Mapped[list["InventoryItem"]] = relationship(back_populates="session", cascade="all, delete-orphan")
    flags: Mapped[list["StoryFlag"]] = relationship(back_populates="session", cascade="all, delete-orphan")


class TurnLog(Base):
    __tablename__ = "turn_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id"), nullable=False)
    turn_index: Mapped[int] = mapped_column(Integer, nullable=False)
    player_input: Mapped[str] = mapped_column(Text, nullable=False)
    intent: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    retrieval: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    dice_results: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    keeper_response: Mapped[str] = mapped_column(Text, nullable=False)
    state_delta: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    session: Mapped[GameSession] = relationship(back_populates="turn_logs")


class Clue(Base):
    __tablename__ = "clues"
    __table_args__ = (UniqueConstraint("session_id", "clue_key", name="uq_session_clue_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id"), nullable=False)
    clue_key: Mapped[str] = mapped_column(String(200), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source_location: Mapped[str | None] = mapped_column(String(200))
    discovered_turn: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    session: Mapped[GameSession] = relationship(back_populates="clues")


class InventoryItem(Base):
    __tablename__ = "inventory_items"
    __table_args__ = (UniqueConstraint("session_id", "item_key", name="uq_session_item_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id"), nullable=False)
    item_key: Mapped[str] = mapped_column(String(200), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    session: Mapped[GameSession] = relationship(back_populates="inventory_items")


class StoryFlag(Base):
    __tablename__ = "story_flags"
    __table_args__ = (UniqueConstraint("session_id", "key", name="uq_session_flag_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id"), nullable=False)
    key: Mapped[str] = mapped_column(String(200), nullable=False)
    value: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    session: Mapped[GameSession] = relationship(back_populates="flags")
