from __future__ import annotations

import json
from datetime import datetime, timedelta

import memory_manager
from operation_journal import OperationJournal
from services import batch_service


def _configure(monkeypatch, tmp_path):
    memory_dir = tmp_path / "memory"
    archive_dir = tmp_path / "archive"
    memory_dir.mkdir()
    archive_dir.mkdir()
    journal = OperationJournal(tmp_path / "journal")
    monkeypatch.setattr(memory_manager, "MEMORY_DIR", memory_dir)
    monkeypatch.setattr(batch_service, "MEMORY_DIR", memory_dir)
    monkeypatch.setattr(batch_service, "ARCHIVE_DIR", archive_dir)
    monkeypatch.setattr(batch_service.config, "ARCHIVE_DIR", str(archive_dir))
    monkeypatch.setattr(batch_service, "get_operation_journal", lambda: journal)
    monkeypatch.setattr(batch_service, "_generate_summary", lambda *args: "- fixture summary")
    monkeypatch.setattr(batch_service, "get_confirmed_weather_summary", lambda *args: None)
    return memory_dir, archive_dir, journal


def test_daily_summary_is_complete_only_after_archive_and_today_commit(monkeypatch, tmp_path):
    memory_dir, archive_dir, journal = _configure(monkeypatch, tmp_path)
    date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    sentinel = "DO_NOT_LEAK_daily_conversation"
    memory_manager.replace_today(
        f"# memory\n\n## 💬 会話履歴\n\n### {date} 10:00\n- master: {sentinel}\n"
    )

    batch_service.run_if_needed()

    assert batch_service._archive_is_complete(date)
    assert sentinel in (archive_dir / f"{date}.md").read_text(encoding="utf-8")
    today = (memory_dir / "today.md").read_text(encoding="utf-8")
    assert sentinel not in today
    assert "fixture summary" in today
    operation = next(iter(journal.latest().values()))
    assert operation["state"] == "succeeded"
    assert sentinel.encode() not in journal.journal_path.read_bytes()
    assert not (journal.spool_dir / operation["op_id"]).exists()


def test_partial_archive_without_matching_completion_is_not_treated_as_done(monkeypatch, tmp_path):
    _memory_dir, archive_dir, _journal = _configure(monkeypatch, tmp_path)
    date = "2026-08-30"
    archive = archive_dir / f"{date}.md"
    archive.write_text("partial", encoding="utf-8")
    completion = archive_dir / f".{date}.complete.json"
    completion.write_text(
        json.dumps({"v": 1, "date": date, "archive_sha256": "0" * 64}),
        encoding="utf-8",
    )

    assert batch_service._archive_is_complete(date) is False
