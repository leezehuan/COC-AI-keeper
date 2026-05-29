import json
import time
from collections.abc import Iterator
from queue import Queue
from threading import Thread

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, selectinload

from app import models, schemas
from app.config import get_settings
from app.database import SessionLocal, get_db, init_db
from app.services.agent import KeeperAgent
from app.services.assistant_agent import GameAssistantAgent
from app.services.characters import ensure_character_attributes
from app.services.debug_events import emit_debug
from app.services.importer import ensure_default_scenario, import_default_content
from app.services.inventory import sync_character_inventory_to_session
from app.services.retrieval import RetrievalService
from app.services.story_state import ensure_story_state
from app.utils import resolve_project_path

# 【阅读顺序 4：后端 HTTP API】
# 这个文件是“Web 请求”和“游戏业务”的连接层：
# 1. 前端请求 /coc/api/characters、/sessions、/actions/stream。
# 2. FastAPI 根据下面的 @router.get / @router.post 找到对应函数。
# 3. 普通接口直接返回 JSON；流式接口用 StreamingResponse 持续返回 NDJSON。
# 4. 真正的守秘人推理在 KeeperAgent.run_turn，也就是 backend/app/services/agent.py。
router = APIRouter(prefix="/api")
_agent: KeeperAgent | None = None
_assistant_agent: GameAssistantAgent | None = None


def get_agent() -> KeeperAgent:
    # KeeperAgent 初始化较重，使用进程内单例复用 LangGraph、LLM 与检索服务。
    # 初学者注意：这里不是每次请求都 new 一个 Agent，否则会重复构建图和客户端，浪费资源。
    global _agent
    if _agent is None:
        _agent = KeeperAgent()
    return _agent


def get_assistant_agent() -> GameAssistantAgent:
    global _assistant_agent
    if _assistant_agent is None:
        _assistant_agent = GameAssistantAgent()
    return _assistant_agent


def ensure_current_character_attributes(db: Session) -> models.Scenario:
    # 每次读取角色前同步默认剧本与预设角色属性，避免资料导入后前端拿到旧结构。
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
    # 【Web 流程 8】创建会话：前端选择角色后调用这里，后端创建 GameSession 并返回页面需要的会话视图。
    scenario = ensure_current_character_attributes(db)
    character = None
    if payload.character_id:
        character = db.get(models.Character, payload.character_id)
    # 未指定角色时优先使用推荐的“调查局探员”，否则退回任意可用角色。
    if character is None:
        character = db.query(models.Character).filter(models.Character.scenario_id == scenario.id, models.Character.archetype == "调查局探员").one_or_none()
    if character is None:
        character = db.query(models.Character).filter(models.Character.scenario_id == scenario.id).first()
    if character is None:
        raise HTTPException(status_code=400, detail="没有可用角色。请先调用 /coc/api/import 导入资料。")
    session = models.GameSession(scenario_id=scenario.id, character_id=character.id, title=payload.title)
    # 新会话立即初始化结构化剧情状态，后续回合只在此结构上增量推进。
    session.state = ensure_story_state({}, session.current_location, session.current_scene, session.current_time)
    db.add(session)
    db.flush()
    sync_character_inventory_to_session(db, session, character)
    db.commit()
    return build_session_out(db, session.id)


@router.get("/sessions", response_model=list[schemas.SessionOut])
def list_sessions(db: Session = Depends(get_db)) -> list[schemas.SessionOut]:
    ensure_current_character_attributes(db)
    # 预加载前端展示所需关联对象，减少序列化时的额外数据库查询。
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
        # 删除数据库会话时同步清理向量库中的会话记忆，避免旧记忆影响新游戏。
        deleted_memory_chunks = RetrievalService().delete_where("session_memory_chunks", {"session_id": session_id})
    except Exception:
        deleted_memory_chunks = 0
    db.delete(session)
    db.commit()
    return {"status": "已删除", "deleted_memory_chunks": deleted_memory_chunks}


@router.post("/sessions/{session_id}/actions", response_model=schemas.ActionResponse)
def submit_action(session_id: str, payload: schemas.PlayerActionIn, db: Session = Depends(get_db)) -> schemas.ActionResponse:
    # 【Web 流程 9】非流式行动接口：适合调试或脚本调用；页面主要使用下面的 stream 版本。
    if db.get(models.GameSession, session_id) is None:
        raise HTTPException(status_code=404, detail="未找到指定会话")
    # 非流式接口直接等待守秘人完整回合执行完毕后返回。
    result = get_agent().run_turn(db, session_id, payload.message)
    return build_action_response(db, session_id, result)


@router.post("/sessions/{session_id}/actions/stream")
def submit_action_stream(session_id: str, payload: schemas.PlayerActionIn, db: Session = Depends(get_db)) -> StreamingResponse:
    # 【Web 流程 10】流式行动接口：玩家输入会在这里进入 KeeperAgent，也就是 LangGraph 回合链路。
    if db.get(models.GameSession, session_id) is None:
        raise HTTPException(status_code=404, detail="未找到指定会话")

    def event_stream() -> Iterator[str]:
        # 这个内部生成器会不断 yield 字符串；FastAPI 每 yield 一次，浏览器就可能收到一小段数据。
        try:
            yield encode_stream_event({"type": "start"})
            events = Queue()

            def enqueue_debug(event: dict) -> None:
                events.put({"type": "debug", "event": event})

            def run_agent() -> None:
                worker_db = SessionLocal()
                try:
                    emit_debug(enqueue_debug, phase="stream", name="action_stream", status="start", message="守秘人回合开始。")
                    result = get_agent().run_turn(worker_db, session_id, payload.message, debug_emit=enqueue_debug)
                    response = build_action_response(worker_db, session_id, result)
                    emit_debug(enqueue_debug, phase="stream", name="action_stream", status="success", message="守秘人回合完成。")
                    events.put({"type": "result", "response": response.model_dump(mode="json")})
                except Exception as exc:
                    events.put({"type": "error", "detail": str(exc)})
                finally:
                    worker_db.close()
                    events.put({"type": "done"})

            Thread(target=run_agent, daemon=True).start()
            while True:
                event = events.get()
                if event.get("type") == "done":
                    break
                if event.get("type") == "result":
                    response_payload = event["response"]
                    for chunk in split_stream_text(str(response_payload.get("narration", ""))):
                        yield encode_stream_event({"type": "chunk", "content": chunk})
                        time.sleep(0.015)
                    yield encode_stream_event({"type": "final", "response": response_payload})
                    continue
                yield encode_stream_event(event)
        except Exception as exc:
            yield encode_stream_event({"type": "error", "detail": str(exc)})

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")


@router.post("/assistant/chat", response_model=schemas.AssistantChatResponse)
def assistant_chat(payload: schemas.AssistantChatRequest, db: Session = Depends(get_db)) -> dict:
    if payload.session_id and db.get(models.GameSession, payload.session_id) is None:
        raise HTTPException(status_code=404, detail="未找到指定会话")
    return get_assistant_agent().chat(
        db,
        message=payload.message,
        session_id=payload.session_id,
        mode=payload.mode,
        enable_mqe=payload.enable_mqe,
        mqe_expansions=payload.mqe_expansions,
        enable_hyde=payload.enable_hyde,
        top_k=payload.top_k,
        candidate_pool_multiplier=payload.candidate_pool_multiplier,
    )


@router.post("/assistant/chat/stream")
def assistant_chat_stream(payload: schemas.AssistantChatRequest, db: Session = Depends(get_db)) -> StreamingResponse:
    if payload.session_id and db.get(models.GameSession, payload.session_id) is None:
        raise HTTPException(status_code=404, detail="未找到指定会话")

    def event_stream() -> Iterator[str]:
        try:
            yield encode_stream_event({"type": "start"})
            events = Queue()

            def enqueue_debug(event: dict) -> None:
                events.put({"type": "debug", "event": event})

            def run_assistant() -> None:
                worker_db = SessionLocal()
                try:
                    emit_debug(enqueue_debug, phase="stream", name="assistant_stream", status="start", message="助手请求开始。")
                    result = get_assistant_agent().chat(
                        worker_db,
                        message=payload.message,
                        session_id=payload.session_id,
                        mode=payload.mode,
                        enable_mqe=payload.enable_mqe,
                        mqe_expansions=payload.mqe_expansions,
                        enable_hyde=payload.enable_hyde,
                        top_k=payload.top_k,
                        candidate_pool_multiplier=payload.candidate_pool_multiplier,
                        debug_emit=enqueue_debug,
                    )
                    emit_debug(enqueue_debug, phase="stream", name="assistant_stream", status="success", message="助手请求完成。")
                    events.put({"type": "result", "response": result})
                except Exception as exc:
                    events.put({"type": "error", "detail": str(exc)})
                finally:
                    worker_db.close()
                    events.put({"type": "done"})

            Thread(target=run_assistant, daemon=True).start()
            while True:
                event = events.get()
                if event.get("type") == "done":
                    break
                if event.get("type") == "result":
                    result = event["response"]
                    for chunk in split_stream_text(result["answer"]):
                        yield encode_stream_event({"type": "chunk", "content": chunk})
                        time.sleep(0.01)
                    yield encode_stream_event({"type": "citations", "citations": result.get("citations", [])})
                    yield encode_stream_event({"type": "final", "response": result})
                    continue
                yield encode_stream_event(event)
        except Exception as exc:
            yield encode_stream_event({"type": "error", "detail": str(exc)})

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")


def build_action_response(db: Session, session_id: str, result: dict) -> schemas.ActionResponse:
    # Agent 的内部状态较大，这里只整理前端需要展示和持久化的公开字段。
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


def encode_stream_event(payload: dict) -> str:
    # 使用 NDJSON：一行一个 JSON 事件，便于浏览器流式解析。
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"


def split_stream_text(text: str) -> Iterator[str]:
    # 优先在中文标点处断句；长句则按固定长度切块，避免前端等待过久。
    buffer = ""
    for char in text:
        buffer += char
        if len(buffer) >= 12 or char in "。！？；\n":
            yield buffer
            buffer = ""
    if buffer:
        yield buffer


def build_session_out(db: Session, session_id: str) -> schemas.SessionOut:
    # 会话输出是前端页面的聚合视图，包含角色、线索、物品和最近回合。
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
