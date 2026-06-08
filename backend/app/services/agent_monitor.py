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


DEFAULT_MAX_RECORDS = 5000
MAX_STRING_LENGTH = 200_000
MAX_CONTAINER_ITEMS = 200
MAX_DEPTH = 8
_SKIP_KEYS = {"db", "debug_emit", "trace_recorder", "client", "llm", "retrieval"}
_event_subscribers: set[Queue] = set()
_event_lock = threading.Lock()
_sequence_lock = threading.Lock()
_run_sequences: dict[str, int] = {}


class AgentTraceRecorder:
    """Best-effort recorder for one action or assistant request."""

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
