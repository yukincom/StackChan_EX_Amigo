from __future__ import annotations

import json
import os

from flask import Flask, jsonify, request

import observability


def _configure_trace(monkeypatch, trace_dir, *, max_bytes=100_000, keep_files=2):
    monkeypatch.setattr(observability, "TRACE_ENABLED", True)
    monkeypatch.setattr(observability, "TRACE_DIR", trace_dir)
    monkeypatch.setattr(observability, "TRACE_MAX_BYTES", max_bytes)
    monkeypatch.setattr(observability, "TRACE_KEEP_FILES", keep_files)


def test_emit_trace_only_writes_allowlisted_metadata(monkeypatch, tmp_path):
    trace_dir = tmp_path / "trace"
    _configure_trace(monkeypatch, trace_dir)
    sentinel = "DO_NOT_LEAK_fixture_secret_and_prompt"

    event = observability.emit_trace(
        sentinel,
        sentinel,
        "ok",
        error_type=sentinel,
        tags={"unlisted": sentinel, "source": sentinel},
        metrics={"unlisted": 42, "input_bytes": 123},
    )

    assert event is not None
    assert event["component"] == "unknown"
    assert event["operation"] == "unknown"
    assert event["error_type"] == "InternalError"
    assert event["tags"] == {"source": "unknown"}
    assert event["metrics"] == {"input_bytes": 123}
    raw = (trace_dir / "trace.jsonl").read_text(encoding="utf-8")
    assert sentinel not in raw
    assert (trace_dir / "trace.jsonl").stat().st_mode & 0o077 == 0


def test_flask_trace_excludes_query_body_and_invalid_header(monkeypatch, tmp_path):
    trace_dir = tmp_path / "trace"
    _configure_trace(monkeypatch, trace_dir)
    app = Flask(__name__)

    @app.post("/echo/<kind>")
    def echo(kind):
        return jsonify({"ok": bool(request.get_data()), "kind": kind})

    observability.install_flask_tracing(app)
    sentinel = "DO_NOT_LEAK_query_body_header"
    response = app.test_client().post(
        f"/echo/demo?q={sentinel}",
        data=sentinel,
        headers={"X-Trace-ID": sentinel},
    )

    assert response.status_code == 200
    trace_id = response.headers["X-Trace-ID"]
    assert observability.TRACE_ID_RE.fullmatch(trace_id)
    raw = (trace_dir / "trace.jsonl").read_text(encoding="utf-8")
    assert sentinel not in raw
    event = json.loads(raw)
    assert event["operation"] == "/echo/<kind>"
    assert event["tags"] == {"http_method": "POST"}


def test_trace_write_failure_never_changes_http_response(monkeypatch):
    app = Flask(__name__)

    @app.get("/healthy")
    def healthy():
        return "healthy", 200

    monkeypatch.setattr(observability, "_append_event", lambda _event: (_ for _ in ()).throw(OSError()))
    observability.install_flask_tracing(app)

    response = app.test_client().get("/healthy")
    assert response.status_code == 200
    assert response.get_data(as_text=True) == "healthy"


def test_trace_rotation_is_bounded(monkeypatch, tmp_path):
    trace_dir = tmp_path / "trace"
    _configure_trace(monkeypatch, trace_dir, max_bytes=350, keep_files=2)

    for index in range(20):
        observability.emit_trace("lab", "request", "ok", metrics={"job_id": index})

    names = sorted(path.name for path in trace_dir.iterdir())
    assert names == ["trace.jsonl", "trace.jsonl.1", "trace.jsonl.2"]
    assert all(path.stat().st_mode & 0o077 == 0 for path in trace_dir.iterdir())
    assert not (trace_dir / "trace.jsonl.3").exists()


def test_reader_drops_malformed_and_rebuilds_allowlisted_events(monkeypatch, tmp_path):
    trace_dir = tmp_path / "trace"
    trace_dir.mkdir()
    _configure_trace(monkeypatch, trace_dir)
    sentinel = "DO_NOT_LEAK_extra_field"
    valid = {
        "schema_version": 1,
        "timestamp": "2026-09-01T00:00:00+00:00",
        "trace_id": "0123456789abcdef",
        "component": "runtime",
        "operation": "startup",
        "status": "ok",
        "extra": sentinel,
    }
    (trace_dir / "trace.jsonl").write_text(
        json.dumps(valid) + "\n{malformed\n" + json.dumps({**valid, "timestamp": sentinel}) + "\n",
        encoding="utf-8",
    )

    events = observability.read_sanitized_events()
    assert len(events) == 1
    assert sentinel not in json.dumps(events)


def test_trace_writer_never_follows_existing_file_symlink(monkeypatch, tmp_path):
    trace_dir = tmp_path / "trace"
    trace_dir.mkdir()
    _configure_trace(monkeypatch, trace_dir)
    target = tmp_path / "target.txt"
    target.write_text("keep", encoding="utf-8")
    try:
        (trace_dir / "trace.jsonl").symlink_to(target)
    except (OSError, NotImplementedError):
        return

    assert observability.emit_trace("runtime", "startup", "ok") is None
    assert target.read_text(encoding="utf-8") == "keep"
