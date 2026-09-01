from __future__ import annotations

import json
import queue

import app as app_module
from operation_journal import OperationJournal


def _install_isolated_journal(monkeypatch, tmp_path, *, queue_size=2):
    journal = OperationJournal(tmp_path / "journal")
    monkeypatch.setattr(app_module, "get_operation_journal", lambda: journal)
    monkeypatch.setattr(app_module, "_VISION_QUEUE", queue.Queue(maxsize=queue_size))
    monkeypatch.setattr(app_module, "_VISION_QUEUED_OPS", set())
    monkeypatch.setattr(app_module, "_ensure_vision_worker", lambda: None)
    monkeypatch.setattr(app_module, "_VISION_JOB_SEQ", 0)
    return journal


def test_vision_acceptance_persists_private_request_before_response(monkeypatch, tmp_path):
    journal = _install_isolated_journal(monkeypatch, tmp_path)
    sentinel = "DO_NOT_LEAK_vision_transcript"
    response = app_module.create_app({"TESTING": True}).test_client().post(
        f"/vision/upload?t={sentinel}&r=user&s=master",
        data=b"J" * 600,
        content_type="image/jpeg",
    )

    assert response.status_code == 200
    payload = response.get_json()
    op_id = payload["operation_id"]
    assert app_module._VISION_QUEUE.get_nowait() == op_id
    assert journal.latest()[op_id]["state"] == "accepted"
    request_data = json.loads(journal.load_payload(op_id, "request.json"))
    assert request_data["transcript"] == sentinel
    assert journal.load_payload(op_id, "image.jpg") == b"J" * 600
    assert sentinel.encode() not in journal.journal_path.read_bytes()


def test_vision_worker_completes_journal_and_removes_private_spool(monkeypatch, tmp_path):
    journal = _install_isolated_journal(monkeypatch, tmp_path)
    request_data = json.dumps({
        "requester": "user",
        "transcript": "fixture",
        "speaker": "master",
        "speaker_label": "",
    }).encode()
    operation = journal.create(
        "vision",
        owner="api",
        payloads={"image.jpg": b"J" * 600, "request.json": request_data},
    )
    seen = {}

    def fake_handle(image, requester, transcript, speaker, speaker_label):
        seen.update({
            "image": image,
            "requester": requester,
            "transcript": transcript,
            "speaker": speaker,
            "speaker_label": speaker_label,
        })
        return {"success": True}

    monkeypatch.setattr(app_module, "handle_vision_upload", fake_handle)
    app_module._process_vision_operation(operation["op_id"])

    assert seen["image"] == b"J" * 600
    assert seen["transcript"] == "fixture"
    assert journal.latest()[operation["op_id"]]["state"] == "succeeded"
    assert not (journal.spool_dir / operation["op_id"]).exists()


def test_queue_full_records_rejection_and_removes_payload(monkeypatch, tmp_path):
    journal = _install_isolated_journal(monkeypatch, tmp_path, queue_size=1)
    client = app_module.create_app({"TESTING": True}).test_client()
    first = client.post("/vision/upload?t=first", data=b"A" * 600)
    second = client.post("/vision/upload?t=second", data=b"B" * 600)

    assert first.status_code == 200
    assert second.status_code == 429
    rejected = [record for record in journal.latest().values() if record["state"] == "rejected"]
    assert len(rejected) == 1
    assert not (journal.spool_dir / rejected[0]["op_id"]).exists()
