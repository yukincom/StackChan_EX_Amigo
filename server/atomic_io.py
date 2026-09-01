"""Crash-safe local file writes with one last-known-good backup."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def _fsync_directory(directory: Path) -> None:
    """Persist directory-entry changes where the platform supports it."""
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _write_temp(parent: Path, name: str, data: bytes, suffix: str) -> Path:
    fd, tmp_name = tempfile.mkstemp(
        dir=parent,
        prefix=f".{name}.",
        suffix=suffix,
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    return tmp_path


def atomic_write_bytes(path: Path, data: bytes, *, keep_backup: bool = True) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    backup_path = path.with_suffix(path.suffix + ".bak")

    if keep_backup and path.exists():
        backup_tmp = _write_temp(
            path.parent,
            path.name,
            path.read_bytes(),
            ".bak.tmp",
        )
        try:
            os.replace(backup_tmp, backup_path)
            _fsync_directory(path.parent)
        finally:
            backup_tmp.unlink(missing_ok=True)

    tmp_path = _write_temp(path.parent, path.name, data, ".tmp")
    try:
        os.replace(tmp_path, path)
        _fsync_directory(path.parent)
    finally:
        tmp_path.unlink(missing_ok=True)


def atomic_write_text(
    path: Path,
    text: str,
    *,
    encoding: str = "utf-8",
    keep_backup: bool = True,
) -> None:
    atomic_write_bytes(path, text.encode(encoding), keep_backup=keep_backup)
