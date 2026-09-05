from __future__ import annotations

import json
import queue

import requests

from operation_journal import OperationJournal
from services import voice_service


class _AcceptedResponse:
    def raise_for_status(self):
        return None


def _install_isolated_delivery(monkeypatch, tmp_path):
    journal = OperationJournal(tmp_path / "journal")
    monkeypatch.setattr(voice_service, "get_operation_journal", lambda: journal)
    monkeypatch.setattr(voice_service, "_VOICE_DELIVERY_QUEUE", queue.Queue(maxsize=10))
    monkeypatch.setattr(voice_service, "_VOICE_DELIVERY_QUEUED", set())
    monkeypatch.setattr(voice_service, "ensure_voice_delivery_worker", lambda: None)
    return journal


def test_fire_and_forget_is_durable_before_return_and_hides_url(monkeypatch, tmp_path):
    journal = _install_isolated_delivery(monkeypatch, tmp_path)
    sentinel = "DO_NOT_LEAK_private_voice_url"

    assert voice_service.push_to_m5stack(f"http://127.0.0.1/voice/{sentinel}") is True

    op_id = voice_service._VOICE_DELIVERY_QUEUE.get_nowait()
    assert journal.latest()[op_id]["state"] == "accepted"
    assert sentinel.encode() not in journal.journal_path.read_bytes()
    assert sentinel in journal.load_payload(op_id, "request.json").decode()


def test_acknowledged_delivery_completes_and_deletes_payload(monkeypatch, tmp_path):
    journal = _install_isolated_delivery(monkeypatch, tmp_path)
    monkeypatch.setattr(voice_service.requests, "post", lambda *args, **kwargs: _AcceptedResponse())

    assert voice_service.push_to_m5stack("http://127.0.0.1/voice/fixture", fire_and_forget=False)

    operation = next(iter(journal.latest().values()))
    assert operation["state"] == "succeeded"
    assert not (journal.spool_dir / operation["op_id"]).exists()


def test_lost_response_becomes_uncertain_and_is_never_auto_retried(monkeypatch, tmp_path):
    journal = _install_isolated_delivery(monkeypatch, tmp_path)

    def timeout(*args, **kwargs):
        raise requests.Timeout("fixture response lost")

    monkeypatch.setattr(voice_service.requests, "post", timeout)
    assert not voice_service.push_to_m5stack(
        "http://127.0.0.1/voice/fixture",
        fire_and_forget=False,
    )

    operation = next(iter(journal.latest().values()))
    assert operation["state"] == "uncertain"
    assert journal.pending("voice_push") == []
    assert (journal.spool_dir / operation["op_id"]).is_dir()


def test_idempotency_key_reuses_one_persisted_delivery(monkeypatch, tmp_path):
    journal = _install_isolated_delivery(monkeypatch, tmp_path)

    assert voice_service.push_to_m5stack(
        "http://127.0.0.1/voice/original",
        idempotency_key="line-operation-1",
    )
    assert voice_service.push_to_m5stack(
        "http://127.0.0.1/voice/retry-must-not-replace",
        idempotency_key="line-operation-1",
    )

    assert len(journal.latest()) == 1
    operation = next(iter(journal.latest().values()))
    payload = journal.load_payload(operation["op_id"], "request.json").decode()
    assert "original" in payload
    assert "retry-must-not-replace" not in payload


def test_new_async_push_has_no_fixed_wait_and_keeps_timeout(monkeypatch, tmp_path):
    journal = _install_isolated_delivery(monkeypatch, tmp_path)
    waits, calls = [], []
    monkeypatch.setattr(voice_service.time, "sleep", waits.append)
    def accepted(url, **kwargs):
        calls.append((url, kwargs))
        return _AcceptedResponse()
    monkeypatch.setattr(voice_service.requests, "post", accepted)
    assert voice_service.push_to_m5stack("http://127.0.0.1/voice/fixture")
    op_id = voice_service._VOICE_DELIVERY_QUEUE.get_nowait()
    assert json.loads(journal.load_payload(op_id, "request.json"))["delay_ms"] == 0
    assert calls == []  # asynchronous enqueue is preserved
    assert voice_service._deliver_voice_operation(op_id)
    assert waits == []
    assert len(calls) == 1 and calls[0][1]["timeout"] == 30


def test_legacy_persisted_delay_remains_compatible(monkeypatch, tmp_path):
    journal = _install_isolated_delivery(monkeypatch, tmp_path)
    operation = journal.create("voice_push", owner="worker", payloads={
        "request.json": json.dumps({"voice_url": "http://127.0.0.1/voice/fixture", "delay_ms": 1500}).encode()})
    waits = []
    monkeypatch.setattr(voice_service.time, "sleep", waits.append)
    monkeypatch.setattr(voice_service.requests, "post", lambda *a, **k: _AcceptedResponse())
    assert voice_service._deliver_voice_operation(operation["op_id"])
    assert waits == [1.5]
