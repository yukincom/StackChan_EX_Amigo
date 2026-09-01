from __future__ import annotations

import json
import os
import stat

import pytest

from operation_journal import JournalCorruptionError, JournalError, OperationJournal


def test_private_payload_is_durable_but_absent_from_journal(tmp_path):
    journal = OperationJournal(tmp_path / "journal")
    sentinel = b"DO_NOT_LEAK_private_transcript_and_image"
    record = journal.create(
        "vision",
        owner="api",
        payloads={"image.jpg": b"JPEG" + sentinel, "request.json": sentinel},
    )

    raw_journal = journal.journal_path.read_bytes()
    assert sentinel not in raw_journal
    assert record["meta"] == {
        "payload_count": 2,
        "payload_bytes": len(b"JPEG" + sentinel) + len(sentinel),
    }
    assert journal.load_payload(record["op_id"], "request.json") == sentinel
    assert stat.S_IMODE(journal.root.stat().st_mode) == 0o700
    assert stat.S_IMODE(journal.journal_path.stat().st_mode) == 0o600
    spool = journal.spool_dir / record["op_id"]
    assert stat.S_IMODE(spool.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in spool.iterdir())


def test_torn_final_record_is_ignored_but_malformed_middle_fails(tmp_path):
    journal = OperationJournal(tmp_path / "journal")
    record = journal.create("daily_summary", owner="scheduler")
    with journal.journal_path.open("ab") as output:
        output.write(b'{"partial":')
    assert journal.latest()[record["op_id"]]["state"] == "accepted"

    raw = journal.journal_path.read_bytes()
    journal.journal_path.write_bytes(b"{malformed}\n" + raw)
    with pytest.raises(JournalCorruptionError, match="malformed"):
        journal.latest()
    assert journal.readiness() == (False, "journal_corrupt")


def test_transition_validation_and_crash_recovery(tmp_path):
    journal = OperationJournal(tmp_path / "journal")
    processing = journal.create("vision", owner="api")
    dispatch = journal.create("voice_push", owner="worker")
    acknowledged = journal.create("voice_push", owner="worker")
    journal.transition(processing["op_id"], "running", owner="worker", stage="processing")
    journal.transition(dispatch["op_id"], "dispatching", owner="worker", stage="dispatch")
    journal.transition(acknowledged["op_id"], "dispatching", owner="worker", stage="dispatch")
    journal.transition(acknowledged["op_id"], "acked", owner="worker", stage="delivered")

    recovered = journal.recover_interrupted()
    states = {record["op_id"]: record["state"] for record in recovered}
    assert states == {
        processing["op_id"]: "retry_wait",
        dispatch["op_id"]: "uncertain",
        acknowledged["op_id"]: "succeeded",
    }
    assert journal.pending("vision")[0]["op_id"] == processing["op_id"]
    assert journal.pending("voice_push") == []
    with pytest.raises(JournalError, match="not allowed"):
        journal.transition(dispatch["op_id"], "succeeded", owner="worker", stage="complete")


def test_payload_hash_mismatch_is_detected(tmp_path):
    journal = OperationJournal(tmp_path / "journal")
    record = journal.create("vision", owner="api", payloads={"image.jpg": b"original"})
    path = journal.spool_dir / record["op_id"] / "image.jpg"
    path.write_bytes(b"changed")
    with pytest.raises(JournalError, match="verification failed"):
        journal.load_payload(record["op_id"], "image.jpg")


def test_journal_never_accepts_free_form_metadata_or_payload_names(tmp_path):
    journal = OperationJournal(tmp_path / "journal")
    with pytest.raises(JournalError, match="name"):
        journal.create("vision", owner="api", payloads={"../secret.txt": b"x"})
    with pytest.raises(JournalError, match="kind"):
        journal.create("free_form_kind", owner="api")
