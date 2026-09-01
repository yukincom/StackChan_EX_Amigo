from __future__ import annotations

from pathlib import Path

import pytest

import atomic_io


def test_atomic_write_preserves_existing_file_if_replace_fails(tmp_path, monkeypatch):
    destination = tmp_path / "settings.json"
    destination.write_text("old\n", encoding="utf-8")

    real_replace = atomic_io.os.replace

    def fail_only_final_replace(source: Path, target: Path) -> None:
        if Path(target) == destination:
            raise OSError("simulated interruption")
        real_replace(source, target)

    monkeypatch.setattr(atomic_io.os, "replace", fail_only_final_replace)

    with pytest.raises(OSError, match="simulated interruption"):
        atomic_io.atomic_write_text(destination, "new\n")

    assert destination.read_text(encoding="utf-8") == "old\n"
    assert destination.with_suffix(".json.bak").read_text(encoding="utf-8") == "old\n"
    assert destination.with_suffix(".json.bak").stat().st_mode & 0o077 == 0
    assert list(tmp_path.glob("*.tmp")) == []
