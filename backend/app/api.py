from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, selectinload

from app import models, schemas
from app.config import get_settings
from app.database import get_db, init_db
from app.services.agent import KeeperAgent
from app.services.characters import ensure_character_attributes
from app.services.importer import ensure_default_scenario, import_default_content
from app.services.inventory import sync_character_inventory_to_session
from app.services.retrieval import RetrievalService
from app.services.story_state import ensure_story_state
from app.utils import resolve_project_path

router = APIRouter(prefix="/api")
_agent: KeeperAgent | None = None


def get_agent() -> KeeperAgent:
    global _agent
    if _agent is None:
        _agent = KeeperAgent()
    return _agent


def ensure_current_character_attributes(db: Session) -> models.Scenario:
    scenario = ensure_default_scenario(db)
    ensure_character_attributes(db, scenario, resolve_project_path(get_settings().character_dir))
    return scenario


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "正常"}


@router.post("/init")
def initialize_database() -> dict[str, str]:
    init_db()
    return {"status": "已初始化"}


@router.post("/import", response_model=schemas.ImportResponse)
def import_content(payload: schemas.ImportRequest, db: Session = Depends(get_db)) -> dict:
    return import_default_content(db, reset_chroma=payload.reset_chroma, include_characters=payload.import_characters)


@router.get("/characters", response_model=list[schemas.CharacterOut])
def list_characters(db: Session = Depends(get_db)) -> list[models.Character]:
    scenario = ensure_current_character_attributes(db)
    return db.query(models.Character).filter(models.Character.scenario_id == scenario.id).order_by(models.Character.archetype).all()


@router.post("/sessions", response_model=schemas.SessionOut)
def create_session(payload: schemas.SessionCreate, db: Session = Depends(get_db)) -> schemas.SessionOut:
    scenario = ensure_current_character_attributes(db)
    character = None
    if payload.character_id:
        character = db.get(models.Character, payload.character_id)
    if character is None:
        character = db.query(models.Character).filter(models.Character.scenario_id == scenario.id, models.Character.archetype == "调查局探员").one_or_none()
    if character is None:
        character = db.query(models.Character).filter(models.Character.scenario_id == scenario.id).first()
    if character is None:
        raise HTTPException(status_code=400, detail="没有可用角色。请先调用 /api/import 导入资料。")
    session = models.GameSession(scenario_id=scenario.id, character_id=character.id, title=payload.title)
    session.state = ensure_story_state({}, session.current_location, session.current_scene, session.current_time)
    db.add(session)
    db.flush()
    sync_character_inventory_to_session(db, session, character)
    db.commit()
    return build_session_out(db, session.id)


@router.get("/sessions", response_model=list[schemas.SessionOut])
def list_sessions(db: Session = Depends(get_db)) -> list[schemas.SessionOut]:
    ensure_current_character_attributes(db)
    sessions = (
        db.query(models.GameSession)
        .options(
            selectinload(models.GameSession.character),
            selectinload(models.GameSession.clues),
            selectinload(models.GameSession.inventory_items),
            selectinload(models.GameSession.flags),
            selectinload(models.GameSession.turn_logs),
        )
        .order_by(models.GameSession.updated_at.desc())
        .limit(20)
        .all()
    )
    return [build_session_out(db, session.id) for session in sessions]


@router.get("/sessions/{session_id}", response_model=schemas.SessionOut)
def get_session(session_id: str, db: Session = Depends(get_db)) -> schemas.SessionOut:
    ensure_current_character_attributes(db)
    return build_session_out(db, session_id)


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str, db: Session = Depends(get_db)) -> dict[str, int | str]:
    session = db.get(models.GameSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="未找到指定会话")
    deleted_memory_chunks = 0
    try:
        deleted_memory_chunks = RetrievalService().delete_where("session_memory_chunks", {"session_id": session_id})
    except Exception:
        deleted_memory_chunks = 0
    db.delete(session)
    db.commit()
    return {"status": "已删除", "deleted_memory_chunks": deleted_memory_chunks}


@router.post("/sessions/{session_id}/actions", response_model=schemas.ActionResponse)
def submit_action(session_id: str, payload: schemas.PlayerActionIn, db: Session = Depends(get_db)) -> schemas.ActionResponse:
    if db.get(models.GameSession, session_id) is None:
        raise HTTPException(status_code=404, detail="未找到指定会话")
    result = get_agent().run_turn(db, session_id, payload.message)
    session_out = build_session_out(db, session_id)
    return schemas.ActionResponse(
        session=session_out,
        narration=result.get("narration", ""),
        options=result.get("options", []),
        dice_results=result.get("dice_results", []),
        skill_checks=result.get("skill_checks", []),
        sanity_checks=result.get("sanity_checks", []),
        discovered_clues=[schemas.ClueOut.model_validate(clue) for clue in result.get("discovered_clues", [])],
        state_delta=result.get("state_delta", {}),
        needs_clarification=bool(result.get("needs_clarification")),
    )


def build_session_out(db: Session, session_id: str) -> schemas.SessionOut:
    session = (
        db.query(models.GameSession)
        .options(
            selectinload(models.GameSession.character),
            selectinload(models.GameSession.clues),
            selectinload(models.GameSession.inventory_items),
            selectinload(models.GameSession.flags),
            selectinload(models.GameSession.turn_logs),
        )
        .filter(models.GameSession.id == session_id)
        .one_or_none()
    )
    if session is None:
        raise HTTPException(status_code=404, detail="未找到指定会话")
    recent_turns = sorted(session.turn_logs, key=lambda item: item.turn_index)[-10:]
    return schemas.SessionOut(
        id=session.id,
        scenario_id=session.scenario_id,
        character_id=session.character_id,
        title=session.title,
        current_location=session.current_location,
        current_scene=session.current_scene,
        current_time=session.current_time,
        story_phase=session.story_phase,
        danger_level=session.danger_level,
        summary=session.summary,
        state=session.state,
        created_at=session.created_at,
        updated_at=session.updated_at,
        character=schemas.CharacterOut.model_validate(session.character),
        clues=[schemas.ClueOut.model_validate(clue) for clue in session.clues],
        inventory_items=[schemas.InventoryItemOut.model_validate(item) for item in session.inventory_items],
        flags=[schemas.StoryFlagOut.model_validate(flag) for flag in session.flags],
        recent_turns=[schemas.TurnLogOut.model_validate(turn) for turn in recent_turns],
    )
