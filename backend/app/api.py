import json
import time
from collections.abc import Iterator
from queue import Queue
from threading import Thread

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, selectinload

from app import models, schemas
from app.config import get_settings
from app.database import SessionLocal, get_db, init_db
from app.services.agent_monitor import (
    cleanup_empty_runs,
    create_trace_run,
    finish_trace_run,
    get_monitor_settings_payload,
    monitor_event_stream,
    update_monitor_settings,
)
from app.services.agents import KeeperSupervisor
from app.services.assistant_agent import GameAssistantAgent
from app.services.characters import ensure_character_attributes
from app.services.debug_events import emit_debug
from app.services.importer import ensure_default_scenario, import_default_content
from app.services.inventory import sync_character_inventory_to_session
from app.services.image_generator import ImageGenerator
from app.services.retrieval import RetrievalService
from app.services.story_state import ensure_story_state
from app.utils import resolve_project_path

# 【阅读顺序 4：后端 HTTP API】
# 这个文件是“Web 请求”和“游戏业务”的连接层：
# 1. 前端请求 /coc/api/characters、/sessions、/actions/stream。
# 2. FastAPI 根据下面的 @router.get / @router.post 找到对应函数。
# 3. 普通接口直接返回 JSON；流式接口用 StreamingResponse 持续返回 NDJSON。
# 4. 真正的守秘人推理在 KeeperSupervisor.run_turn，也就是 backend/app/services/agents/supervisor.py。
router = APIRouter(prefix="/api")  # router = 路由：FastAPI 路由对象，所有API端点注册在此
_agent: KeeperSupervisor | None = None  # _agent = 守秘人调度器单例：进程内复用，避免重复初始化LLM和检索服务
_assistant_agent: GameAssistantAgent | None = None  # _assistant_agent = 游戏助手单例：进程内复用


def get_agent() -> KeeperSupervisor:
    # KeeperSupervisor 初始化较重，使用进程内单例复用各子 Agent、LLM 与检索服务。
    # 初学者注意：这里不是每次请求都 new 一个 Supervisor，否则会重复构建客户端，浪费资源。
    global _agent
    if _agent is None:
        _agent = KeeperSupervisor()
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
    trace_recorder = create_trace_run(session_id=session_id, source="action", metadata={"stream": False, "message": payload.message})
    try:
        result = get_agent().run_turn(db, session_id, payload.message, trace_recorder=trace_recorder)
        finish_trace_run(trace_recorder, "success")
    except Exception as exc:
        finish_trace_run(trace_recorder, "error", str(exc))
        raise
    if result.get("needs_image"):
        session = db.get(models.GameSession, session_id)
        if session and session.turn_logs:
            latest_turn = max(session.turn_logs, key=lambda log: log.turn_index)
            image_gen = ImageGenerator()
            image_url = image_gen.generate_and_save(db, latest_turn.id, result["narration"], result.get("image_scene_type", ""))
            if image_url:
                result["image_url"] = image_url
                result["image_metadata"] = latest_turn.image_metadata
    return build_action_response(db, session_id, result)


@router.post("/sessions/{session_id}/actions/stream")
def submit_action_stream(session_id: str, payload: schemas.PlayerActionIn, db: Session = Depends(get_db)) -> StreamingResponse:
    # 【Web 流程 10】流式行动接口：玩家输入会在这里进入 KeeperSupervisor，也就是当前多 Agent 回合链路。
    # 对学习者来说，这个接口很值得精读，因为它把“Web 请求”和“Agent 回合”真正接了起来：
    # 1. 浏览器发来一句玩家输入。
    # 2. 这里开启后台线程运行 Supervisor。
    # 3. Supervisor 在执行过程中不断把调试事件放进队列。
    # 4. event_stream() 再把这些事件编码成 NDJSON，持续推给前端。
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
                trace_recorder = create_trace_run(session_id=session_id, source="action", metadata={"stream": True, "message": payload.message})
                try:
                    # 这里单独创建 worker_db，而不是复用外层 db。
                    # 原因是回合执行发生在后台线程中，数据库会话最好在线程内创建和关闭，
                    # 这样更容易避免线程间共享 Session 带来的问题。
                    emit_debug(enqueue_debug, phase="stream", name="action_stream", status="start", message="守秘人回合开始。")
                    result = get_agent().run_turn(worker_db, session_id, payload.message, debug_emit=enqueue_debug, trace_recorder=trace_recorder)
                    response = build_action_response(worker_db, session_id, result)
                    emit_debug(enqueue_debug, phase="stream", name="action_stream", status="success", message="守秘人回合完成。")
                    events.put({"type": "result", "response": response.model_dump(mode="json")})
                    finish_trace_run(trace_recorder, "success")
                    if result.get("needs_image"):
                        session = worker_db.get(models.GameSession, session_id)
                        if session and session.turn_logs:
                            latest_turn = max(session.turn_logs, key=lambda log: log.turn_index)
                            image_gen = ImageGenerator()
                            image_url = image_gen.generate_and_save(worker_db, latest_turn.id, result["narration"], result.get("image_scene_type", ""))
                            if image_url:
                                events.put({"type": "image", "url": image_url, "turn_id": latest_turn.id, "metadata": latest_turn.image_metadata})
                            else:
                                emit_debug(enqueue_debug, phase="stream", name="image_generation", status="warning", message="图片生成失败或配置未启用。")
                except Exception as exc:
                    finish_trace_run(trace_recorder, "error", str(exc))
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
                    # final 事件里会带完整 ActionResponse，但在此之前我们先把 narration 切成小块发送。
                    # 这样前端可以模拟“正在打字”的流式体验，而不必等整个回合结束后一次性显示全文。
                    for chunk in split_stream_text(str(response_payload.get("narration", ""))):
                        yield encode_stream_event({"type": "chunk", "content": chunk})
                        time.sleep(0.015)
                    yield encode_stream_event({"type": "final", "response": response_payload})
                    continue
                if event.get("type") == "image":
                    yield encode_stream_event({"type": "image", "url": event["url"], "turnId": event["turn_id"], "metadata": event.get("metadata", {})})
                    continue
                yield encode_stream_event(event)
        except Exception as exc:
            yield encode_stream_event({"type": "error", "detail": str(exc)})

    return StreamingResponse(
        event_stream(),
        media_type="application/x-ndjson",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


@router.post("/assistant/chat", response_model=schemas.AssistantChatResponse)
def assistant_chat(payload: schemas.AssistantChatRequest, db: Session = Depends(get_db)) -> dict:
    if payload.session_id and db.get(models.GameSession, payload.session_id) is None:
        raise HTTPException(status_code=404, detail="未找到指定会话")
    trace_recorder = create_trace_run(session_id=payload.session_id, source="assistant", metadata={"stream": False, "message": payload.message})
    try:
        result = get_assistant_agent().chat(
            db,
            message=payload.message,
            session_id=payload.session_id,
            mode=payload.mode,
            enable_mqe=payload.enable_mqe,
            mqe_expansions=payload.mqe_expansions,
            enable_hyde=payload.enable_hyde,
            top_k=payload.top_k,
            candidate_pool_multiplier=payload.candidate_pool_multiplier,
            trace_recorder=trace_recorder,
        )
        finish_trace_run(trace_recorder, "success")
        return result
    except Exception as exc:
        finish_trace_run(trace_recorder, "error", str(exc))
        raise


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
                trace_recorder = create_trace_run(session_id=payload.session_id, source="assistant", metadata={"stream": True, "message": payload.message})
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
                        trace_recorder=trace_recorder,
                    )
                    emit_debug(enqueue_debug, phase="stream", name="assistant_stream", status="success", message="助手请求完成。")
                    events.put({"type": "result", "response": result})
                    finish_trace_run(trace_recorder, "success")
                except Exception as exc:
                    finish_trace_run(trace_recorder, "error", str(exc))
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

    return StreamingResponse(
        event_stream(),
        media_type="application/x-ndjson",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


@router.get("/monitor/settings", response_model=schemas.AgentTraceSettingsOut)
def get_monitor_settings(db: Session = Depends(get_db)) -> dict[str, int]:
    return get_monitor_settings_payload(db)


@router.put("/monitor/settings", response_model=schemas.AgentTraceSettingsOut)
def put_monitor_settings(payload: schemas.AgentTraceSettingsUpdate, db: Session = Depends(get_db)) -> dict[str, int]:
    return update_monitor_settings(db, payload.max_records)


@router.get("/monitor/runs", response_model=list[schemas.AgentTraceRunOut])
def list_monitor_runs(
    session_id: str | None = None,
    source: str | None = None,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[models.AgentTraceRun]:
    query = db.query(models.AgentTraceRun)
    if session_id:
        query = query.filter(models.AgentTraceRun.session_id == session_id)
    if source:
        query = query.filter(models.AgentTraceRun.source == source)
    if status:
        query = query.filter(models.AgentTraceRun.status == status)
    return query.order_by(models.AgentTraceRun.started_at.desc()).offset(offset).limit(limit).all()


@router.get("/monitor/records", response_model=list[schemas.AgentTraceRecordOut])
def list_monitor_records(
    run_id: str | None = None,
    session_id: str | None = None,
    agent_name: str | None = None,
    status: str | None = None,
    source: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[models.AgentTraceRecord]:
    query = build_monitor_record_query(db, run_id=run_id, session_id=session_id, agent_name=agent_name, status=status, source=source)
    return query.order_by(models.AgentTraceRecord.created_at.desc(), models.AgentTraceRecord.sequence.desc()).offset(offset).limit(limit).all()


@router.get("/monitor/events/stream")
def monitor_events_stream() -> StreamingResponse:
    def event_stream() -> Iterator[str]:
        for event in monitor_event_stream():
            yield encode_stream_event(event)

    return StreamingResponse(
        event_stream(),
        media_type="application/x-ndjson",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


@router.delete("/monitor/records/{record_id}")
def delete_monitor_record(record_id: str, db: Session = Depends(get_db)) -> dict[str, int | str]:
    record = db.get(models.AgentTraceRecord, record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="未找到指定监控记录")
    db.delete(record)
    db.commit()
    cleanup_empty_runs(db)
    return {"status": "已删除", "deleted": 1}


@router.delete("/monitor/runs/{run_id}")
def delete_monitor_run(run_id: str, db: Session = Depends(get_db)) -> dict[str, int | str]:
    run = db.get(models.AgentTraceRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="未找到指定运行记录")
    record_count = db.query(models.AgentTraceRecord).filter(models.AgentTraceRecord.run_id == run_id).count()
    db.delete(run)
    db.commit()
    return {"status": "已删除", "deleted_runs": 1, "deleted_records": record_count}


@router.delete("/monitor/records")
def delete_monitor_records(
    run_id: str | None = None,
    session_id: str | None = None,
    agent_name: str | None = None,
    status: str | None = None,
    source: str | None = None,
    db: Session = Depends(get_db),
) -> dict[str, int | str]:
    query = build_monitor_record_query(db, run_id=run_id, session_id=session_id, agent_name=agent_name, status=status, source=source)
    deleted = query.delete(synchronize_session=False)
    db.commit()
    cleanup_empty_runs(db)
    return {"status": "已删除", "deleted": deleted}


def build_monitor_record_query(
    db: Session,
    *,
    run_id: str | None = None,
    session_id: str | None = None,
    agent_name: str | None = None,
    status: str | None = None,
    source: str | None = None,
):
    query = db.query(models.AgentTraceRecord)
    if run_id:
        query = query.filter(models.AgentTraceRecord.run_id == run_id)
    if session_id:
        query = query.filter(models.AgentTraceRecord.session_id == session_id)
    if agent_name:
        query = query.filter(models.AgentTraceRecord.agent_name == agent_name)
    if status:
        query = query.filter(models.AgentTraceRecord.status == status)
    if source:
        query = query.filter(models.AgentTraceRecord.source == source)
    return query


def _scene_type_to_aspect_ratio(scene_type: str) -> str:
    return "16:9" if scene_type == "new_scene" else "1:1"


def build_action_response(db: Session, session_id: str, result: dict) -> schemas.ActionResponse:
    # Agent 的内部状态较大，这里只整理前端需要展示和持久化的公开字段。
    # 可以把它理解成“后端 ViewModel 组装层”：
    # - Supervisor 返回的是偏内部的运行结果
    # - 前端真正需要的是 ActionResponse 这个稳定结构
    # 学习前后端对接时，建议把这里和 frontend/src/types.ts 里的 ActionResponse 对照着看。
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
        needs_image=bool(result.get("needs_image")),
        image_aspect_ratio=_scene_type_to_aspect_ratio(result.get("image_scene_type", "")),
        image_url=result.get("image_url"),
        image_metadata=result.get("image_metadata", {}),
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
