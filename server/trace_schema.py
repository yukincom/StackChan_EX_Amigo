"""Pure allowlist validation for StackChan trace schema v1."""

from __future__ import annotations

import re


TRACE_SCHEMA_VERSION = 1
TRACE_ID_RE = re.compile(r"[A-Fa-f0-9-]{8,64}")
TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})")
SAFE_ROUTE_RE = re.compile(r"/[A-Za-z0-9_./:<>{}-]{0,127}")
COMPONENTS = {
    "http", "stt", "chat", "llm", "vision", "vlm", "tts", "voice",
    "m5", "batch", "journal", "discord", "runtime", "lab",
}
OPERATIONS = {
    "transcribe", "complete", "search", "summarize", "analyze", "synthesize",
    "fetch", "convert", "push", "enqueue", "process", "archive", "recover",
    "poll", "initialize", "startup", "shutdown", "request", "unknown",
}
TAG_VALUES = {
    "http_method": {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"},
    "result": {"success", "failure", "timeout", "rejected", "degraded", "skipped", "unknown"},
    "requester": {"user", "ai", "system", "unknown"},
    "mode": {"normal", "search", "archive", "transient", "unknown"},
    "source": {"api", "worker", "scheduler", "startup", "manual", "unknown"},
    "job_kind": {"daily_summary", "vision", "voice_push", "discord", "unknown"},
}
ALLOWED_METRICS = {
    "status_code", "duration_ms", "request_bytes", "response_bytes",
    "input_bytes", "output_bytes", "input_chars", "output_chars",
    "queue_size", "queue_max", "attempt", "job_id",
}


def clean_component(value: object) -> str:
    text = str(value or "").strip().lower()
    return text if text in COMPONENTS else "unknown"


def clean_operation(value: object) -> str:
    text = str(value or "").strip()
    if text in OPERATIONS or SAFE_ROUTE_RE.fullmatch(text):
        return text
    return "unknown"


def clean_status(value: object) -> str:
    text = str(value or "").strip().lower()
    return text if text in {"ok", "error", "degraded", "rejected", "skipped"} else "error"


def clean_error_type(value: object) -> str:
    text = str(value or "").strip().lower()
    if "timeout" in text:
        return "TimeoutError"
    if "connection" in text:
        return "ConnectionError"
    if any(part in text for part in ("value", "type", "key", "json", "decode", "unicode")):
        return "InvalidDataError"
    if any(part in text for part in ("oserror", "ioerror", "permission", "file")):
        return "IOError"
    return "InternalError"


def clean_tags(tags: dict[str, object] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in (tags or {}).items():
        allowed = TAG_VALUES.get(key)
        if allowed is None or value is None:
            continue
        cleaned = str(value).strip()
        result[key] = cleaned if cleaned in allowed else "unknown"
    return result


def clean_metrics(metrics: dict[str, object] | None) -> dict[str, int | float]:
    result: dict[str, int | float] = {}
    for key, value in (metrics or {}).items():
        if key not in ALLOWED_METRICS or isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            result[key] = round(value, 3) if isinstance(value, float) else value
    return result


def sanitize_event(value: object) -> dict | None:
    """Rebuild an event from the allowlist; never copy unknown fields."""
    if not isinstance(value, dict):
        return None
    trace_id = str(value.get("trace_id", ""))
    timestamp = str(value.get("timestamp", ""))
    if (
        value.get("schema_version") != TRACE_SCHEMA_VERSION
        or not TRACE_ID_RE.fullmatch(trace_id)
        or not TIMESTAMP_RE.fullmatch(timestamp)
    ):
        return None
    event = {
        "schema_version": TRACE_SCHEMA_VERSION,
        "timestamp": timestamp,
        "trace_id": trace_id.lower(),
        "component": clean_component(value.get("component")),
        "operation": clean_operation(value.get("operation")),
        "status": clean_status(value.get("status")),
    }
    if value.get("error_type"):
        event["error_type"] = clean_error_type(value["error_type"])
    tags = clean_tags(value.get("tags") if isinstance(value.get("tags"), dict) else None)
    metrics = clean_metrics(value.get("metrics") if isinstance(value.get("metrics"), dict) else None)
    if tags:
        event["tags"] = tags
    if metrics:
        event["metrics"] = metrics
    return event
