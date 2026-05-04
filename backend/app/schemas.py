from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ScenarioOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    source_path: str | None = None
    metadata_: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class CharacterOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    scenario_id: str | None = None
    name: str
    archetype: str
    occupation: str | None = None
    hp_current: int
    hp_max: int
    san_current: int
    san_max: int
    mp_current: int
    mp_max: int
    luck: int
    attributes: dict[str, Any]
    skills: dict[str, Any]
    inventory: list[Any]
    background: dict[str, Any]


class ClueOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    clue_key: str
    name: str
    content: str
    source_location: str | None = None
    discovered_turn: int
    metadata_: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class InventoryItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    item_key: str
    name: str
    description: str
    quantity: int
    metadata_: dict[str, Any] = Field(default_factory=dict)


class StoryFlagOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    value: dict[str, Any]


class TurnLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    turn_index: int
    player_input: str
    intent: dict[str, Any]
    retrieval: dict[str, Any]
    dice_results: list[Any]
    keeper_response: str
    state_delta: dict[str, Any]
    created_at: datetime


class SessionCreate(BaseModel):
    character_id: str | None = None
    title: str = "无光的灯塔"


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    scenario_id: str
    character_id: str
    title: str
    current_location: str
    current_scene: str
    current_time: str
    story_phase: str
    danger_level: int
    summary: str
    state: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    character: CharacterOut
    clues: list[ClueOut]
    inventory_items: list[InventoryItemOut]
    flags: list[StoryFlagOut]
    recent_turns: list[TurnLogOut] = Field(default_factory=list)


class PlayerActionIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class DiceResultOut(BaseModel):
    expression: str
    rolls: list[int]
    modifier: int = 0
    total: int


class SkillCheckOut(BaseModel):
    skill: str
    skill_value: int
    difficulty: str
    roll: int
    success_level: str
    success: bool


class ActionResponse(BaseModel):
    session: SessionOut
    narration: str
    options: list[str]
    dice_results: list[dict[str, Any]]
    skill_checks: list[dict[str, Any]]
    sanity_checks: list[dict[str, Any]]
    discovered_clues: list[ClueOut]
    state_delta: dict[str, Any]
    needs_clarification: bool = False


class ImportRequest(BaseModel):
    reset_chroma: bool = False
    import_characters: bool = True


class ImportResponse(BaseModel):
    scenario_id: str
    scenario_chunks: int
    rule_chunks: int
    scenario_entities: int = 0
    clue_index: int = 0
    characters: int
