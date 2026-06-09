from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from queue import Empty, Queue
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models
from app.database import SessionLocal


# 【Agent 监控核心配置】
# 这些限制不是业务规则，而是“保护监控系统自己”的护栏。
# Agent payload 里可能包含很长的 prompt、检索结果、ORM 对象甚至循环引用；
# 如果不做限制，监控系统可能反过来拖慢或拖垮主游戏流程。
DEFAULT_MAX_RECORDS = 5000  # 默认最多保存多少条步骤记录
MAX_STRING_LENGTH = 200_000  # 单个字符串字段的最大保存长度
MAX_CONTAINER_ITEMS = 200  # 单个 list/dict/set 最多展开多少项
MAX_DEPTH = 8  # 嵌套对象最多递归多少层
_SKIP_KEYS = {"db", "debug_emit", "trace_recorder", "client", "llm", "retrieval"}
_event_subscribers: set[Queue] = set()
_event_lock = threading.Lock()
_sequence_lock = threading.Lock()
_run_sequences: dict[str, int] = {}


class AgentTraceRecorder:
    """一次 action / assistant 请求的监控记录器。

    【中文名称】Agent 追踪记录器

    【功能说明】
    这个对象会被 `api.py` 创建，然后沿着 `trace_recorder` 参数传给
    Supervisor、各 Agent、Skill 和 Tool。

    学习时可以这样理解：
    - AgentTraceRun = 一次完整请求
    - AgentTraceRecorder = 这次请求的“记录笔”
    - AgentTraceRecord = 记录笔写下的一行步骤日志

    重要设计：best-effort。
    监控写入失败时不会影响主流程，所以 recorder 内部大多数错误都会被吞掉。
    """

    def __init__(self, run_id: str | None, session_id: str | None, source: str) -> None:
        self.run_id = run_id
        self.session_id = session_id
        self.source = source

    @property
    def enabled(self) -> bool:
        return bool(self.run_id)

    @contextmanager
    def step(
        self,
        *,
        agent_name: str,
        step_name: str,
        phase: str,
        input_payload: Any = None,
    ) -> Iterator[dict[str, Any]]:
        """记录一个步骤的输入、输出、耗时和异常。

        用法：

            with recorder.step(..., input_payload=payload) as trace_step:
                result = do_work()
                trace_step["output"] = result

        这个模式很适合 Agent 项目，因为 Agent 步骤通常是：
        输入上下文 -> 调 LLM/Tool -> 得到输出。
        """
        state: dict[str, Any] = {"output": None}
        started = time.perf_counter()
        error = None
        try:
            yield state
        except Exception as exc:
            error = str(exc)
            state.setdefault("output", {"error": error})
            raise
        finally:
            status = "error" if error else "success"
            self.record(
                agent_name=agent_name,
                step_name=step_name,
                phase=phase,
                status=status,
                input_payload=input_payload,
                output_payload=state.get("output"),
                error=error,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )

    def record(
        self,
        *,
        agent_name: str,
        step_name: str,
        phase: str,
        status: str,
        input_payload: Any = None,
        output_payload: Any = None,
        error: str | None = None,
        duration_ms: int | None = None,
    ) -> None:
        if not self.run_id:
            return
        record_trace_step(
            run_id=self.run_id,
            session_id=self.session_id,
            source=self.source,
            agent_name=agent_name,
            step_name=step_name,
            phase=phase,
            status=status,
            input_payload=input_payload,
            output_payload=output_payload,
            error=error,
            duration_ms=duration_ms,
        )


def create_trace_run(*, session_id: str | None, source: str, metadata: dict[str, Any] | None = None) -> AgentTraceRecorder:
    """创建一次监控 run，并返回对应 recorder。

    【调用位置】
    - 玩家行动：`backend/app/api.py` 的 actions/actions stream
    - 游戏助手：`backend/app/api.py` 的 assistant/chat

    如果数据库不可用或写入失败，返回一个 disabled recorder。
    这样主业务仍然可以继续执行。
    """
    try:
        db = SessionLocal()
        try:
            run = models.AgentTraceRun(
                session_id=session_id,
                source=source,
                status="running",
                metadata_=safe_serialize(metadata or {}),
            )
            db.add(run)
            db.commit()
            db.refresh(run)
            with _sequence_lock:
                _run_sequences[run.id] = 0
            publish_monitor_event("run", run_to_payload(run))
            return AgentTraceRecorder(run.id, session_id, source)
        finally:
            db.close()
    except Exception:
        return AgentTraceRecorder(None, session_id, source)


def finish_trace_run(recorder: AgentTraceRecorder | None, status: str, error: str | None = None) -> None:
    """结束一次监控 run。

    status 通常是：
    - success：主流程正常完成
    - error：主流程抛出异常

    注意：这个函数只更新监控状态，不负责业务事务提交。
    """
    if recorder is None or not recorder.run_id:
        return
    try:
        db = SessionLocal()
        try:
            run = db.get(models.AgentTraceRun, recorder.run_id)
            if run is None:
                return
            run.status = status
            run.ended_at = datetime.now(timezone.utc)
            if error:
                meta = dict(run.metadata_ or {})
                meta["error"] = error[:2000]
                run.metadata_ = meta
            db.commit()
            db.refresh(run)
            publish_monitor_event("run", run_to_payload(run))
        finally:
            db.close()
    except Exception:
        return


def record_trace_step(
    *,
    run_id: str,
    session_id: str | None,
    source: str,
    agent_name: str,
    step_name: str,
    phase: str,
    status: str,
    input_payload: Any = None,
    output_payload: Any = None,
    error: str | None = None,
    duration_ms: int | None = None,
) -> None:
    """写入一条步骤记录。

    这是 `AgentTraceRecorder.record()` 最终调用的底层函数。
    它会：
    1. 给当前 run 分配 sequence
    2. 安全序列化输入输出
    3. 写入 `agent_trace_records`
    4. 推送实时事件给监控页
    5. 执行全局保留条数裁剪
    """
    try:
        db = SessionLocal()
        try:
            record = models.AgentTraceRecord(
                run_id=run_id,
                sequence=next_sequence(run_id),
                session_id=session_id,
                source=source,
                agent_name=agent_name[:120],
                step_name=step_name[:200],
                phase=phase[:80],
                status=status[:50],
                input_payload=payload_object(input_payload),
                output_payload=payload_object(output_payload),
                error=error[:4000] if error else None,
                duration_ms=duration_ms,
            )
            db.add(record)
            db.commit()
            db.refresh(record)
            publish_monitor_event("record", record_to_payload(record))
            enforce_retention(db)
        finally:
            db.close()
    except Exception:
        return


def next_sequence(run_id: str) -> int:
    with _sequence_lock:
        current = _run_sequences.get(run_id, 0) + 1
        _run_sequences[run_id] = current
        return current


def safe_serialize(value: Any, *, _depth: int = 0, _seen: set[int] | None = None) -> Any:
    """把任意 Python 对象转换成可写入 JSONB 的安全结构。

    为什么不能直接 `json.dumps(value)`？
    因为 Agent payload 里经常有：
    - SQLAlchemy Session
    - ORM 对象
    - 函数回调
    - LLM/Retrieval 客户端
    - 超长 prompt
    - 循环引用

    这个函数的目标不是“完美还原对象”，而是“尽量保留学习有用的信息，
    同时保证监控系统不会因为对象太复杂而失败”。
    """
    if _seen is None:
        _seen = set()
    if _depth > MAX_DEPTH:
        return {"__truncated__": True, "reason": "max_depth"}
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if len(value) > MAX_STRING_LENGTH:
            return {"__truncated__": True, "length": len(value), "value": value[:MAX_STRING_LENGTH]}
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    value_id = id(value)
    if isinstance(value, (dict, list, tuple, set)):
        if value_id in _seen:
            return {"__cycle__": True, "type": type(value).__name__}
        _seen.add(value_id)
        try:
            if isinstance(value, dict):
                result: dict[str, Any] = {}
                for index, (key, item) in enumerate(value.items()):
                    if index >= MAX_CONTAINER_ITEMS:
                        result["__truncated_items__"] = len(value) - MAX_CONTAINER_ITEMS
                        break
                    key_text = str(key)
                    if key_text in _SKIP_KEYS:
                        result[key_text] = {"__omitted__": True, "type": type(item).__name__}
                        continue
                    result[key_text] = safe_serialize(item, _depth=_depth + 1, _seen=_seen)
                return result
            items = list(value)
            result_list = [
                safe_serialize(item, _depth=_depth + 1, _seen=_seen)
                for item in items[:MAX_CONTAINER_ITEMS]
            ]
            if len(items) > MAX_CONTAINER_ITEMS:
                result_list.append({"__truncated_items__": len(items) - MAX_CONTAINER_ITEMS})
            return result_list
        finally:
            _seen.discard(value_id)
    if hasattr(value, "__table__"):
        return serialize_orm(value, _depth=_depth, _seen=_seen)
    if callable(value):
        return {"__omitted__": True, "type": "callable", "name": getattr(value, "__name__", type(value).__name__)}
    try:
        json.dumps(value)
        return value
    except Exception:
        return {"__repr__": repr(value)[:4000], "type": type(value).__name__}


def payload_object(value: Any) -> dict[str, Any]:
    serialized = safe_serialize(value)
    if isinstance(serialized, dict):
        return serialized
    return {"value": serialized}


def serialize_orm(value: Any, *, _depth: int, _seen: set[int]) -> dict[str, Any]:
    result: dict[str, Any] = {"__orm__": type(value).__name__}
    try:
        for column in value.__table__.columns:
            result[column.name] = safe_serialize(getattr(value, column.name), _depth=_depth + 1, _seen=_seen)
    except Exception:
        result["__repr__"] = repr(value)[:1000]
    return result


def get_settings_row(db: Session) -> models.AgentTraceSettings:
    settings = db.get(models.AgentTraceSettings, "global")
    if settings is None:
        settings = models.AgentTraceSettings(id="global", max_records=DEFAULT_MAX_RECORDS)
        db.add(settings)
        db.flush()
    return settings


def get_monitor_settings_payload(db: Session) -> dict[str, int]:
    settings = get_settings_row(db)
    return {
        "max_records": settings.max_records,
        "record_count": db.query(models.AgentTraceRecord).count(),
        "run_count": db.query(models.AgentTraceRun).count(),
    }


def update_monitor_settings(db: Session, max_records: int) -> dict[str, int]:
    settings = get_settings_row(db)
    settings.max_records = max(0, int(max_records))
    db.commit()
    enforce_retention(db)
    payload = get_monitor_settings_payload(db)
    publish_monitor_event("settings", payload)
    return payload


def enforce_retention(db: Session) -> None:
    """执行全局记录上限。

    当步骤记录数量超过 `agent_trace_settings.max_records` 时，
    删除最旧的 record。随后清理已经结束且没有任何 record 的 run。

    这就是监控页里“存储条数上限”的后端实现。
    """
    settings = get_settings_row(db)
    max_records = max(0, int(settings.max_records))
    count = db.query(models.AgentTraceRecord).count()
    overflow = count - max_records
    if overflow > 0:
        old_ids = [
            item.id for item in db.query(models.AgentTraceRecord.id)
            .order_by(models.AgentTraceRecord.created_at.asc(), models.AgentTraceRecord.sequence.asc())
            .limit(overflow)
            .all()
        ]
        if old_ids:
            db.query(models.AgentTraceRecord).filter(models.AgentTraceRecord.id.in_(old_ids)).delete(synchronize_session=False)
            db.commit()
    cleanup_empty_runs(db)


def cleanup_empty_runs(db: Session) -> None:
    empty_run_ids = [
        run_id for (run_id,) in db.query(models.AgentTraceRun.id)
        .outerjoin(models.AgentTraceRecord)
        .filter(models.AgentTraceRun.ended_at.isnot(None))
        .group_by(models.AgentTraceRun.id)
        .having(func.count(models.AgentTraceRecord.id) == 0)
        .all()
    ]
    if empty_run_ids:
        db.query(models.AgentTraceRun).filter(models.AgentTraceRun.id.in_(empty_run_ids)).delete(synchronize_session=False)
        db.commit()


def publish_monitor_event(event_type: str, payload: dict[str, Any]) -> None:
    event = {"type": event_type, event_type: payload, "timestamp": datetime.now(timezone.utc).isoformat()}
    with _event_lock:
        subscribers = list(_event_subscribers)
    for queue in subscribers:
        try:
            queue.put_nowait(event)
        except Exception:
            continue


@contextmanager
def subscribe_monitor_events() -> Iterator[Queue]:
    queue: Queue = Queue(maxsize=500)
    with _event_lock:
        _event_subscribers.add(queue)
    try:
        yield queue
    finally:
        with _event_lock:
            _event_subscribers.discard(queue)


def monitor_event_stream() -> Iterator[dict[str, Any]]:
    """实时监控事件流。

    FastAPI 会把这里 yield 的 dict 编码成 NDJSON。
    监控前端 `frontend/src/monitor.tsx` 会保持一个 fetch 连接，
    每收到一行 JSON 就立即更新 Runs 或 Records 列表。
    """
    with subscribe_monitor_events() as queue:
        yield {"type": "start", "timestamp": datetime.now(timezone.utc).isoformat()}
        while True:
            try:
                yield queue.get(timeout=15)
            except Empty:
                yield {"type": "heartbeat", "timestamp": datetime.now(timezone.utc).isoformat()}


def run_to_payload(run: models.AgentTraceRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "session_id": run.session_id,
        "source": run.source,
        "status": run.status,
        "metadata_": run.metadata_ or {},
        "started_at": run.started_at.isoformat() if run.started_at else "",
        "ended_at": run.ended_at.isoformat() if run.ended_at else None,
    }


def record_to_payload(record: models.AgentTraceRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "run_id": record.run_id,
        "sequence": record.sequence,
        "session_id": record.session_id,
        "source": record.source,
        "agent_name": record.agent_name,
        "step_name": record.step_name,
        "phase": record.phase,
        "status": record.status,
        "input_payload": record.input_payload or {},
        "output_payload": record.output_payload or {},
        "error": record.error,
        "duration_ms": record.duration_ms,
        "created_at": record.created_at.isoformat() if record.created_at else "",
    }
