from __future__ import annotations

import memory_manager


def test_today_write_is_atomic_and_recoverable(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_manager, "MEMORY_DIR", tmp_path)

    memory_manager.replace_today("first\n")
    memory_manager.replace_today("second\n")

    today = tmp_path / "today.md"
    backup = tmp_path / "today.md.bak"
    assert today.read_text(encoding="utf-8") == "second\n"
    assert backup.read_text(encoding="utf-8") == "first\n"

    today.write_text("broken partial write", encoding="utf-8")
    assert memory_manager.recover_today_from_backup() is True
    assert today.read_text(encoding="utf-8") == "first\n"
    assert backup.read_text(encoding="utf-8") == "first\n"


def test_today_append_uses_checked_replace(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_manager, "MEMORY_DIR", tmp_path)
    memory_manager.append_today("one\n")
    memory_manager.append_today("two\n")
    assert (tmp_path / "today.md").read_text(encoding="utf-8") == "one\ntwo\n"
    assert (tmp_path / "today.md.bak").read_text(encoding="utf-8") == "one\n"
