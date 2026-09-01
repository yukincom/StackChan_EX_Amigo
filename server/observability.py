"""Privacy-preserving structured traces for local StackChan diagnostics.

Only bounded metadata is accepted. Raw prompts, transcripts, images, URLs,
headers, exception messages and configuration values have no field in the
event schema and therefore cannot be written by this module.
"""

from __future__ import annotations

import contextvars
import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from config import config
from trace_schema import (
    TRACE_ID_RE,
    TRACE_SCHEMA_VERSION,
    clean_component,
    clean_error_type,
    clean_metrics,
    clean_operation,
    clean_status,
    clean_tags,
    sanitize_event,
)


TRACE_DIR = Path(config.TRACE_DIR)
TRACE_ENABLED = bool(config.TRACE_ENABLED)
TRACE_MAX_BYTES = max(4096, int(config.TRACE_MAX_BYTES))
TRACE_KEEP_FILES = max(1, min(20, int(config.TRACE_KEEP_FILES)))

_TRACE_FILE = "trace.jsonl"
_TRACE_ID = contextvars.ContextVar("stackchan_trace_id", default="")
_WRITE_LOCK = threading.RLock()


def new_trace_id(candidate: str | None = None) -> str:
    value = str(candidate or "").strip()
    if TRACE_ID_RE.fullmatch(value):
        return value.lower()
    return uuid.uuid4().hex


def current_trace_id() -> str:
    value = _TRACE_ID.get()
    if value:
        return value
    value = new_trace_id()
    _TRACE_ID.set(value)
    return value


@contextmanager
def trace_context(trace_id: str | None = None) -> Iterator[str]:
    value = new_trace_id(trace_id)
    token = _TRACE_ID.set(value)
    try:
        yield value
    finally:
        _TRACE_ID.reset(token)


def _rotate_if_needed(next_bytes: int) -> None:
    path = TRACE_DIR / _TRACE_FILE
    try:
        current_size = path.stat().st_size
    except FileNotFoundError:
        current_size = 0
    if current_size + next_bytes <= TRACE_MAX_BYTES:
        return
    oldest = TRACE_DIR / f"{_TRACE_FILE}.{TRACE_KEEP_FILES}"
    oldest.unlink(missing_ok=True)
    for index in range(TRACE_KEEP_FILES - 1, 0, -1):
        source = TRACE_DIR / f"{_TRACE_FILE}.{index}"
        if source.exists():
            os.replace(source, TRACE_DIR / f"{_TRACE_FILE}.{index + 1}")
    if path.exists():
        os.replace(path, TRACE_DIR / f"{_TRACE_FILE}.1")


def _append_event(event: dict) -> None:
    data = (json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    with _WRITE_LOCK:
        TRACE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(TRACE_DIR, 0o700)
        _rotate_if_needed(len(data))
        path = TRACE_DIR / _TRACE_FILE
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            view = memoryview(data)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("trace append made no progress")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def emit_trace(
    component: str,
    operation: str,
    status: str,
    *,
    trace_id: str | None = None,
    error_type: str | None = None,
    tags: dict[str, object] | None = None,
    metrics: dict[str, object] | None = None,
) -> dict | None:
    if not TRACE_ENABLED:
        return None
    event = {
        "schema_version": TRACE_SCHEMA_VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "trace_id": new_trace_id(trace_id or current_trace_id()),
        "component": clean_component(component),
        "operation": clean_operation(operation),
        "status": clean_status(status),
    }
    if error_type:
        event["error_type"] = clean_error_type(error_type)
    safe_tags = clean_tags(tags)
    safe_metrics = clean_metrics(metrics)
    if safe_tags:
        event["tags"] = safe_tags
    if safe_metrics:
        event["metrics"] = safe_metrics
    try:
        _append_event(event)
    except Exception:
        return None
    return event


@dataclass
class TraceSpan:
    component: str
    operation: str
    tags: dict[str, object] = field(default_factory=dict)
    metrics: dict[str, object] = field(default_factory=dict)

    def add_metrics(self, **values: object) -> None:
        self.metrics.update(values)


@contextmanager
def trace_span(
    component: str,
    operation: str,
    *,
    tags: dict[str, object] | None = None,
    metrics: dict[str, object] | None = None,
) -> Iterator[TraceSpan]:
    span = TraceSpan(component, operation, dict(tags or {}), dict(metrics or {}))
    started = time.monotonic()
    try:
        yield span
    except Exception as exc:
        span.metrics["duration_ms"] = (time.monotonic() - started) * 1000
        emit_trace(
            component,
            operation,
            "error",
            error_type=type(exc).__name__,
            tags=span.tags,
            metrics=span.metrics,
        )
        raise
    else:
        span.metrics["duration_ms"] = (time.monotonic() - started) * 1000
        emit_trace(component, operation, "ok", tags=span.tags, metrics=span.metrics)


def trace_files(trace_dir: Path | None = None) -> list[Path]:
    directory = Path(trace_dir or TRACE_DIR)
    files = [directory / f"{_TRACE_FILE}.{index}" for index in range(TRACE_KEEP_FILES, 0, -1)]
    files.append(directory / _TRACE_FILE)
    return [path for path in files if path.is_file()]


def read_sanitized_events(trace_dir: Path | None = None, *, limit: int = 500) -> list[dict]:
    events: list[dict] = []
    for path in trace_files(trace_dir):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            continue
        for line in lines:
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            event = sanitize_event(raw)
            if event is not None:
                events.append(event)
    return events[-max(0, int(limit)):]


def install_flask_tracing(flask_app) -> None:
    """Attach request metadata tracing without reading request/response bodies."""
    if flask_app.extensions.get("stackchan_tracing"):
        return
    flask_app.extensions["stackchan_tracing"] = True

    from flask import g, request

    @flask_app.before_request
    def _trace_request_start():
        trace_id = new_trace_id(request.headers.get("X-Trace-ID"))
        g._stackchan_trace_token = _TRACE_ID.set(trace_id)
        g._stackchan_trace_started = time.monotonic()

    @flask_app.after_request
    def _trace_request_finish(response):
        trace_id = current_trace_id()
        started = getattr(g, "_stackchan_trace_started", time.monotonic())
        rule = request.url_rule.rule if request.url_rule is not None else "unmatched"
        metrics = {
            "status_code": response.status_code,
            "duration_ms": (time.monotonic() - started) * 1000,
        }
        if request.content_length is not None:
            metrics["request_bytes"] = request.content_length
        if response.content_length is not None:
            metrics["response_bytes"] = response.content_length
        emit_trace(
            "http",
            rule,
            "ok" if response.status_code < 400 else "error",
            trace_id=trace_id,
            tags={"http_method": request.method},
            metrics=metrics,
        )
        response.headers["X-Trace-ID"] = trace_id
        return response

    @flask_app.teardown_request
    def _trace_request_reset(_error):
        token = getattr(g, "_stackchan_trace_token", None)
        if token is not None:
            _TRACE_ID.reset(token)
