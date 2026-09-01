"""Verified snapshot/restore primitives that never target live data implicitly."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Iterable


SNAPSHOT_SCHEMA_VERSION = 1
DEFAULT_INCLUDE = ("env", "server/json", "server/memory", "server/archive")


class SnapshotError(RuntimeError):
    pass


def _fsync_directory(directory: Path) -> None:
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


def _hash_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


def _safe_relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise SnapshotError(f"unsafe snapshot path: {value!r}")
    return path


def _iter_source_files(source_root: Path, include: Iterable[str]) -> list[tuple[Path, PurePosixPath]]:
    source_root = source_root.resolve()
    files: list[tuple[Path, PurePosixPath]] = []
    for raw in include:
        relative = _safe_relative(raw)
        unresolved = source_root
        for part in relative.parts:
            unresolved /= part
            if unresolved.is_symlink():
                raise SnapshotError(f"symlink is not allowed in snapshot: {raw}")
        candidate = unresolved.resolve()
        try:
            candidate.relative_to(source_root)
        except ValueError as exc:
            raise SnapshotError(f"include path escapes source root: {raw}") from exc
        if not candidate.exists():
            continue
        if candidate.is_symlink():
            raise SnapshotError(f"symlink is not allowed in snapshot: {raw}")
        candidates = [candidate] if candidate.is_file() else sorted(candidate.rglob("*"))
        for path in candidates:
            if path.is_symlink():
                raise SnapshotError(f"symlink is not allowed in snapshot: {path}")
            if not path.is_file():
                continue
            rel = PurePosixPath(path.relative_to(source_root).as_posix())
            files.append((path, rel))
    deduplicated = {relative.as_posix(): (path, relative) for path, relative in files}
    return [deduplicated[key] for key in sorted(deduplicated)]


def create_snapshot(
    source_root: Path,
    output_path: Path,
    include: Iterable[str] = DEFAULT_INCLUDE,
) -> dict:
    """Create an atomic, checksummed ZIP from explicitly selected relative roots."""
    source_root = source_root.expanduser().resolve()
    output_path = output_path.expanduser().resolve()
    if not source_root.is_dir():
        raise SnapshotError("source root must be an existing directory")
    try:
        output_path.relative_to(source_root)
    except ValueError:
        pass
    else:
        raise SnapshotError("snapshot output must be outside the source root")
    files = _iter_source_files(source_root, include)
    if not files:
        raise SnapshotError("snapshot contains no files")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(
        dir=output_path.parent,
        prefix=f".{output_path.name}.",
        suffix=".tmp",
    )
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        entries = []
        with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path, relative in files:
                digest = hashlib.sha256()
                size = 0
                with path.open("rb") as source:
                    with archive.open(f"data/{relative.as_posix()}", "w") as destination:
                        while chunk := source.read(1024 * 1024):
                            destination.write(chunk)
                            digest.update(chunk)
                            size += len(chunk)
                entries.append(
                    {
                        "path": relative.as_posix(),
                        "size": size,
                        "sha256": digest.hexdigest(),
                    }
                )
            manifest = {
                "schema_version": SNAPSHOT_SCHEMA_VERSION,
                "created_at": datetime.now(timezone.utc).isoformat(),
                # Never persist an absolute user path in a portable snapshot.
                "source_name": source_root.name,
                "entries": entries,
            }
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        with tmp_path.open("rb") as source:
            os.fsync(source.fileno())
        os.replace(tmp_path, output_path)
        _fsync_directory(output_path.parent)
    finally:
        tmp_path.unlink(missing_ok=True)
    return manifest


def verify_snapshot(snapshot_path: Path) -> dict:
    snapshot_path = snapshot_path.expanduser().resolve()
    if not snapshot_path.is_file():
        raise SnapshotError("snapshot file does not exist")
    try:
        with zipfile.ZipFile(snapshot_path, "r") as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise SnapshotError("snapshot contains duplicate archive names")
            try:
                manifest = json.loads(archive.read("manifest.json"))
            except (KeyError, json.JSONDecodeError, UnicodeError) as exc:
                raise SnapshotError("snapshot manifest is missing or invalid") from exc
            if not isinstance(manifest, dict):
                raise SnapshotError("snapshot manifest must be an object")
            if manifest.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
                raise SnapshotError("unsupported snapshot schema")
            entries = manifest.get("entries")
            if not isinstance(entries, list) or not entries:
                raise SnapshotError("snapshot manifest contains no entries")
            expected_names = {"manifest.json"}
            seen_paths: set[str] = set()
            for entry in entries:
                if not isinstance(entry, dict):
                    raise SnapshotError("invalid manifest entry")
                raw_path = entry.get("path")
                if not isinstance(raw_path, str):
                    raise SnapshotError("invalid manifest path")
                relative = _safe_relative(raw_path)
                if relative.as_posix() in seen_paths:
                    raise SnapshotError(f"duplicate manifest path: {relative}")
                seen_paths.add(relative.as_posix())
                archive_name = f"data/{relative.as_posix()}"
                expected_names.add(archive_name)
                expected_size = entry.get("size")
                expected_digest = entry.get("sha256")
                if type(expected_size) is not int or expected_size < 0:
                    raise SnapshotError(f"invalid payload size: {relative}")
                if not isinstance(expected_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_digest):
                    raise SnapshotError(f"invalid payload hash: {relative}")
                try:
                    payload = archive.open(archive_name, "r")
                except KeyError as exc:
                    raise SnapshotError(f"missing payload: {relative}") from exc
                digest = hashlib.sha256()
                size = 0
                with payload:
                    while chunk := payload.read(1024 * 1024):
                        digest.update(chunk)
                        size += len(chunk)
                        if size > expected_size:
                            raise SnapshotError(f"payload verification failed: {relative}")
                if size != expected_size or digest.hexdigest() != expected_digest:
                    raise SnapshotError(f"payload verification failed: {relative}")
            if set(names) != expected_names:
                raise SnapshotError("snapshot contains unlisted payloads")
            return manifest
    except zipfile.BadZipFile as exc:
        raise SnapshotError("snapshot is not a valid ZIP") from exc


def restore_snapshot(snapshot_path: Path, target_root: Path) -> dict:
    """Restore into a new or empty directory, then verify every restored file."""
    manifest = verify_snapshot(snapshot_path)
    target_root = target_root.expanduser().resolve()
    if target_root.exists():
        if not target_root.is_dir():
            raise SnapshotError("restore target must be a directory")
        if any(target_root.iterdir()):
            raise SnapshotError("restore target must be empty")
    target_root.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(snapshot_path.expanduser().resolve(), "r") as archive:
        for entry in manifest["entries"]:
            relative = _safe_relative(entry["path"])
            destination = target_root / Path(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".tmp",
            )
            tmp_path = Path(tmp_name)
            try:
                with os.fdopen(fd, "wb") as output:
                    with archive.open(f"data/{relative.as_posix()}", "r") as source:
                        while chunk := source.read(1024 * 1024):
                            output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(tmp_path, destination)
                _fsync_directory(destination.parent)
            finally:
                tmp_path.unlink(missing_ok=True)

    for entry in manifest["entries"]:
        destination = target_root / entry["path"]
        size, digest = _hash_file(destination)
        if size != entry["size"] or digest != entry["sha256"]:
            raise SnapshotError(f"restored payload verification failed: {entry['path']}")
    return manifest
