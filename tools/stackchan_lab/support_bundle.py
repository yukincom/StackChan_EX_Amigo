"""Create and verify a deliberately small, secret-safe support bundle."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import platform
import re
import subprocess
import tempfile
import zipfile
from collections import deque
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from types import ModuleType
from typing import Callable

from .doctor import doctor_summary, read_env_keys, run_doctor


SUPPORT_SCHEMA_VERSION = 1
PRIVACY_POLICY_VERSION = 1
_TRACE_FILE_RE = re.compile(r"trace\.jsonl(?:\.(\d+))?")
_EXPECTED_ENTRIES = {
    "README.txt",
    "doctor.json",
    "redaction_report.json",
    "runtime.json",
    "traces/events.jsonl",
}
_MAX_MANIFEST_BYTES = 256 * 1024
_MAX_ENTRY_BYTES = 16 * 1024 * 1024


class SupportBundleError(RuntimeError):
    pass


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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


def _trace_schema(repo_root: Path) -> ModuleType:
    path = repo_root / "server" / "trace_schema.py"
    spec = importlib.util.spec_from_file_location("stackchan_support_trace_schema", path)
    if spec is None or spec.loader is None:
        raise SupportBundleError("trace schema is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _read_selected_env(env_dir: Path) -> dict[str, str]:
    merged: dict[str, str] = {}
    for filename in (".env", ".env.local"):
        values, _duplicates = read_env_keys(env_dir / filename)
        merged.update(values)
    return merged


def default_trace_dir(repo_root: Path, env_dir: Path) -> Path:
    values = _read_selected_env(env_dir)
    raw_trace = values.get("TRACE_DIR", "").strip()
    if raw_trace:
        path = Path(raw_trace).expanduser()
        return path if path.is_absolute() else (repo_root / "server" / path).resolve()
    raw_state = values.get("STACKCHAN_STATE_DIR", "").strip()
    if raw_state:
        state = Path(raw_state).expanduser()
        if not state.is_absolute():
            state = (repo_root / "server" / state).resolve()
    else:
        state = Path.home() / ".local" / "state" / "stackchan-ex-amigo"
    return state / "trace"


def _trace_paths(trace_dir: Path) -> list[Path]:
    if trace_dir.is_symlink() or not trace_dir.is_dir():
        return []
    candidates: list[tuple[int, Path]] = []
    for path in trace_dir.iterdir():
        match = _TRACE_FILE_RE.fullmatch(path.name)
        if not match or not path.is_file() or path.is_symlink():
            continue
        order = int(match.group(1)) if match.group(1) else 0
        candidates.append((order, path))
    return [path for _, path in sorted(candidates, key=lambda item: item[0], reverse=True)]


def collect_sanitized_traces(
    repo_root: Path,
    trace_dir: Path,
    *,
    limit: int,
) -> tuple[list[dict], dict[str, int]]:
    schema = _trace_schema(repo_root)
    accepted: deque[dict] = deque(maxlen=max(0, int(limit)))
    counts = {"accepted": 0, "dropped": 0, "malformed": 0, "truncated": 0}
    for path in _trace_paths(trace_dir):
        try:
            source = path.open("r", encoding="utf-8")
        except (OSError, UnicodeError):
            counts["dropped"] += 1
            continue
        with source:
            for line in source:
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    counts["malformed"] += 1
                    continue
                event = schema.sanitize_event(raw)
                if event is None:
                    counts["dropped"] += 1
                    continue
                if accepted.maxlen and len(accepted) == accepted.maxlen:
                    counts["truncated"] += 1
                if accepted.maxlen:
                    accepted.append(event)
                counts["accepted"] += 1
    return list(accepted), counts


def _git_runtime(repo_root: Path) -> dict[str, object]:
    def run(*arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(repo_root), *arguments],
            text=True,
            capture_output=True,
            check=False,
        )

    head = run("rev-parse", "HEAD")
    status = run("status", "--porcelain=v1", "--untracked-files=all")
    lines = status.stdout.splitlines() if status.returncode == 0 else []
    raw_head = head.stdout.strip()
    return {
        "head": raw_head if head.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", raw_head) else "unknown",
        "dirty": bool(lines),
        "dirty_count": len(lines),
    }


def _runtime_report(repo_root: Path) -> dict[str, object]:
    lock_path = repo_root / "uv.lock"
    return {
        "python": platform.python_version(),
        "system": platform.system(),
        "system_release": platform.release(),
        "machine": platform.machine(),
        "git": _git_runtime(repo_root),
        "uv_lock_sha256": _sha256(lock_path.read_bytes()) if lock_path.is_file() else "missing",
    }


def _zip_write(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o600 << 16
    archive.writestr(info, data)


def _safe_manifest_path(value: object) -> PurePosixPath:
    if not isinstance(value, str):
        raise SupportBundleError("invalid bundle entry path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise SupportBundleError("unsafe bundle entry path")
    return path


def create_support_bundle(
    repo_root: Path,
    output_path: Path,
    *,
    env_dir: Path | None = None,
    trace_dir: Path | None = None,
    max_events: int = 500,
    now: Callable[[], datetime] | None = None,
) -> dict:
    repo_root = repo_root.expanduser().resolve()
    if not (repo_root / "server" / "app.py").is_file():
        raise SupportBundleError("repository layout is invalid")
    env_dir = (env_dir or Path.home() / "env").expanduser().resolve()
    unresolved_output = output_path.expanduser()
    if unresolved_output.is_symlink():
        raise SupportBundleError("bundle output must not be a symlink")
    output_path = unresolved_output.resolve()
    try:
        output_path.relative_to(repo_root)
    except ValueError:
        pass
    else:
        raise SupportBundleError("bundle output must be outside the repository")
    if output_path.exists():
        raise SupportBundleError("bundle output already exists")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    unresolved_trace_dir = (trace_dir or default_trace_dir(repo_root, env_dir)).expanduser()
    if unresolved_trace_dir.is_symlink():
        events = []
        redaction = {"accepted": 0, "dropped": 0, "malformed": 0, "truncated": 0}
    else:
        events, redaction = collect_sanitized_traces(
            repo_root,
            unresolved_trace_dir.resolve(),
            limit=max(0, min(10_000, int(max_events))),
        )
    generated_at = (now or (lambda: datetime.now(timezone.utc)))().astimezone(timezone.utc).isoformat()
    trace_bytes = b"".join(
        json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
        for event in events
    )
    contents = {
        "README.txt": (
            "StackChan secret-safe support bundle\n"
            "Contains only allowlisted diagnostics and sanitized trace metadata.\n"
            "It never includes dotenv values, prompts, transcripts, images, audio, logs,\n"
            "memory, archives, hostnames, usernames, absolute paths, Git remotes or diffs.\n"
            "Review every artifact before sharing.\n"
        ).encode("utf-8"),
        "doctor.json": _json_bytes(doctor_summary(run_doctor(repo_root, env_dir))),
        "redaction_report.json": _json_bytes(redaction),
        "runtime.json": _json_bytes(_runtime_report(repo_root)),
        "traces/events.jsonl": trace_bytes,
    }
    manifest = {
        "schema_version": SUPPORT_SCHEMA_VERSION,
        "privacy_policy_version": PRIVACY_POLICY_VERSION,
        "created_at": generated_at,
        "entries": [
            {"path": name, "size": len(data), "sha256": _sha256(data)}
            for name, data in sorted(contents.items())
        ],
    }

    fd, tmp_name = tempfile.mkstemp(
        dir=output_path.parent,
        prefix=f".{output_path.name}.",
        suffix=".tmp",
    )
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        with zipfile.ZipFile(tmp_path, "w") as archive:
            for name, data in sorted(contents.items()):
                _zip_write(archive, name, data)
            _zip_write(archive, "manifest.json", _json_bytes(manifest))
        with tmp_path.open("rb") as source:
            os.fsync(source.fileno())
        os.replace(tmp_path, output_path)
        _fsync_directory(output_path.parent)
    finally:
        tmp_path.unlink(missing_ok=True)
    return manifest


def verify_support_bundle(bundle_path: Path) -> dict:
    bundle_path = bundle_path.expanduser().resolve()
    if not bundle_path.is_file():
        raise SupportBundleError("bundle does not exist")
    try:
        with zipfile.ZipFile(bundle_path, "r") as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise SupportBundleError("bundle contains duplicate entries")
            try:
                if archive.getinfo("manifest.json").file_size > _MAX_MANIFEST_BYTES:
                    raise SupportBundleError("bundle manifest is too large")
                manifest = json.loads(archive.read("manifest.json"))
            except (KeyError, json.JSONDecodeError, UnicodeError) as exc:
                raise SupportBundleError("bundle manifest is invalid") from exc
            if not isinstance(manifest, dict) or manifest.get("schema_version") != SUPPORT_SCHEMA_VERSION:
                raise SupportBundleError("unsupported bundle schema")
            entries = manifest.get("entries")
            if not isinstance(entries, list):
                raise SupportBundleError("bundle manifest entries are invalid")
            expected = {"manifest.json"}
            seen: set[str] = set()
            for entry in entries:
                if not isinstance(entry, dict):
                    raise SupportBundleError("bundle manifest entry is invalid")
                relative = _safe_manifest_path(entry.get("path"))
                name = relative.as_posix()
                if name in seen:
                    raise SupportBundleError("bundle manifest path is duplicated")
                seen.add(name)
                expected.add(name)
                try:
                    info = archive.getinfo(name)
                    if info.file_size > _MAX_ENTRY_BYTES:
                        raise SupportBundleError(f"bundle entry is too large: {name}")
                    data = archive.read(name)
                except KeyError as exc:
                    raise SupportBundleError(f"bundle entry is missing: {name}") from exc
                if type(entry.get("size")) is not int or entry["size"] != len(data):
                    raise SupportBundleError(f"bundle size verification failed: {name}")
                digest = entry.get("sha256")
                if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                    raise SupportBundleError(f"bundle hash is invalid: {name}")
                if _sha256(data) != digest:
                    raise SupportBundleError(f"bundle hash verification failed: {name}")
            if set(names) != expected or seen != _EXPECTED_ENTRIES:
                raise SupportBundleError("bundle contains unknown or missing entries")
            return manifest
    except zipfile.BadZipFile as exc:
        raise SupportBundleError("bundle is not a valid ZIP") from exc
