"""Crash-tolerant operation metadata and private payload spool.

The append-only journal deliberately cannot store user content. Private payloads
live in mode-0600 spool files and are referenced only by an opaque operation ID.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Mapping

from config import config


JOURNAL_SCHEMA_VERSION = 1
KINDS = {"vision", "voice_push", "daily_summary", "discord"}
STATES = {
    "accepted", "running", "dispatching", "acked", "succeeded",
    "retry_wait", "failed", "rejected", "uncertain",
}
OWNERS = {"api", "worker", "scheduler", "startup", "manual", "recovery"}
STAGES = {"queued", "processing", "dispatch", "delivered", "complete", "error", "recovery"}
TERMINAL_STATES = {"succeeded", "failed", "rejected", "uncertain"}
RETRYABLE_STATES = {"accepted", "retry_wait"}
_TRANSITIONS = {
    "accepted": {"running", "dispatching", "retry_wait", "failed", "rejected"},
    "retry_wait": {"running", "dispatching", "failed", "rejected"},
    "running": {"dispatching", "succeeded", "retry_wait", "failed"},
    "dispatching": {"acked", "uncertain", "failed"},
    "acked": {"succeeded", "failed"},
    "succeeded": set(),
    "failed": set(),
    "rejected": set(),
    "uncertain": set(),
}
_OP_ID_RE = re.compile(r"[0-9a-f]{32}")
_PAYLOAD_NAME_RE = re.compile(r"[a-z][a-z0-9_-]{0,31}\.(?:bin|jpg|json|txt|wav)")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_MAX_JOURNAL_BYTES = 64 * 1024 * 1024
_MAX_RECORD_BYTES = 4096
_MAX_PAYLOAD_BYTES = 32 * 1024 * 1024
_PROCESS_LOCK = threading.RLock()


class JournalError(RuntimeError):
    pass


class JournalCorruptionError(JournalError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_private_atomic(path: Path, data: bytes) -> None:
    if path.exists() or path.is_symlink():
        raise JournalError("private spool target already exists")
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("private spool write made no progress")
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


class OperationJournal:
    def __init__(self, root: Path | None = None):
        self.root = Path(root or config.JOURNAL_DIR).expanduser().resolve()
        self.journal_path = self.root / "operations.jsonl"
        self.lock_path = self.root / "operations.lock"
        self.spool_dir = self.root / "spool"

    def _ensure_layout(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.spool_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        os.chmod(self.spool_dir, 0o700)

    @contextmanager
    def _exclusive(self) -> Iterator[None]:
        with _PROCESS_LOCK:
            self._ensure_layout()
            descriptor = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                os.fchmod(descriptor, 0o600)
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)

    @staticmethod
    def _validate_record(record: object) -> dict:
        if not isinstance(record, dict) or set(record) != {
            "v", "ts", "op_id", "kind", "state", "stage", "attempt", "owner", "meta",
        }:
            raise JournalCorruptionError("journal record shape is invalid")
        if record["v"] != JOURNAL_SCHEMA_VERSION:
            raise JournalCorruptionError("journal schema version is unsupported")
        if not isinstance(record["ts"], str):
            raise JournalCorruptionError("journal timestamp is invalid")
        try:
            datetime.fromisoformat(record["ts"].replace("Z", "+00:00"))
        except ValueError as exc:
            raise JournalCorruptionError("journal timestamp is invalid") from exc
        if not isinstance(record["op_id"], str) or not _OP_ID_RE.fullmatch(record["op_id"]):
            raise JournalCorruptionError("journal operation ID is invalid")
        if record["kind"] not in KINDS or record["state"] not in STATES:
            raise JournalCorruptionError("journal kind or state is invalid")
        if record["stage"] not in STAGES or record["owner"] not in OWNERS:
            raise JournalCorruptionError("journal stage or owner is invalid")
        if type(record["attempt"]) is not int or not 0 <= record["attempt"] <= 10_000:
            raise JournalCorruptionError("journal attempt is invalid")
        meta = record["meta"]
        if not isinstance(meta, dict) or set(meta) - {"payload_count", "payload_bytes"}:
            raise JournalCorruptionError("journal metadata is invalid")
        for key, value in meta.items():
            if type(value) is not int or value < 0:
                raise JournalCorruptionError(f"journal {key} is invalid")
        return record

    def _read_records_unlocked(self) -> list[dict]:
        if not self.journal_path.exists():
            return []
        if self.journal_path.is_symlink():
            raise JournalCorruptionError("journal file must not be a symlink")
        if self.journal_path.stat().st_size > _MAX_JOURNAL_BYTES:
            raise JournalCorruptionError("journal file is too large")
        raw = self.journal_path.read_bytes()
        lines = raw.splitlines(keepends=True)
        records: list[dict] = []
        for index, line in enumerate(lines):
            if not line.endswith(b"\n"):
                if index == len(lines) - 1:
                    break
                raise JournalCorruptionError("journal contains an incomplete record")
            if len(line) > _MAX_RECORD_BYTES:
                raise JournalCorruptionError("journal record is too large")
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise JournalCorruptionError("journal record is malformed") from exc
            records.append(self._validate_record(record))
        return records

    def _append_unlocked(self, record: dict) -> None:
        self._validate_record(record)
        data = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        if len(data) > _MAX_RECORD_BYTES:
            raise JournalError("journal record is too large")
        if self.journal_path.is_symlink():
            raise JournalError("journal file must not be a symlink")
        descriptor = os.open(self.journal_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            view = memoryview(data)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("journal append made no progress")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _latest(records: list[dict]) -> dict[str, dict]:
        latest: dict[str, dict] = {}
        kinds: dict[str, str] = {}
        for record in records:
            previous = latest.get(record["op_id"])
            if previous is None:
                if record["state"] != "accepted":
                    raise JournalCorruptionError("operation does not start in accepted state")
                kinds[record["op_id"]] = record["kind"]
            else:
                if record["kind"] != kinds[record["op_id"]]:
                    raise JournalCorruptionError("operation kind changed")
                if record["state"] not in _TRANSITIONS[previous["state"]]:
                    raise JournalCorruptionError("operation state transition is invalid")
                if record["attempt"] < previous["attempt"]:
                    raise JournalCorruptionError("operation attempt moved backwards")
            latest[record["op_id"]] = record
        return latest

    def records(self) -> list[dict]:
        with self._exclusive():
            records = self._read_records_unlocked()
            self._latest(records)
            return records

    def latest(self) -> dict[str, dict]:
        with self._exclusive():
            return self._latest(self._read_records_unlocked())

    def _spool_path(self, op_id: str) -> Path:
        if not _OP_ID_RE.fullmatch(op_id):
            raise JournalError("operation ID is invalid")
        return self.spool_dir / op_id

    def create(
        self,
        kind: str,
        *,
        owner: str,
        payloads: Mapping[str, bytes] | None = None,
    ) -> dict:
        if kind not in KINDS or owner not in OWNERS:
            raise JournalError("operation kind or owner is invalid")
        checked_payloads: dict[str, bytes] = {}
        total_bytes = 0
        for name, value in (payloads or {}).items():
            if (
                not isinstance(name, str)
                or name == "manifest.json"
                or not _PAYLOAD_NAME_RE.fullmatch(name)
            ):
                raise JournalError("private payload name is invalid")
            if not isinstance(value, bytes):
                raise JournalError("private payload must be bytes")
            total_bytes += len(value)
            if total_bytes > _MAX_PAYLOAD_BYTES:
                raise JournalError("private payload is too large")
            checked_payloads[name] = value

        op_id = uuid.uuid4().hex
        spool_path = self._spool_path(op_id)
        manifest: dict[str, dict[str, int | str]] = {}
        if checked_payloads:
            self._ensure_layout()
            spool_path.mkdir(mode=0o700)
            os.chmod(spool_path, 0o700)
            try:
                for name, value in sorted(checked_payloads.items()):
                    _write_private_atomic(spool_path / name, value)
                    manifest[name] = {"size": len(value), "sha256": _sha256(value)}
                _write_private_atomic(
                    spool_path / "manifest.json",
                    (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"),
                )
                _fsync_directory(self.spool_dir)
            except Exception:
                shutil.rmtree(spool_path, ignore_errors=True)
                raise

        record = {
            "v": JOURNAL_SCHEMA_VERSION,
            "ts": _utc_now(),
            "op_id": op_id,
            "kind": kind,
            "state": "accepted",
            "stage": "queued",
            "attempt": 0,
            "owner": owner,
            "meta": {"payload_count": len(checked_payloads), "payload_bytes": total_bytes},
        }
        try:
            with self._exclusive():
                records = self._read_records_unlocked()
                self._latest(records)
                self._append_unlocked(record)
        except Exception:
            shutil.rmtree(spool_path, ignore_errors=True)
            raise
        return record

    def transition(self, op_id: str, state: str, *, owner: str, stage: str) -> dict:
        if state not in STATES or owner not in OWNERS or stage not in STAGES:
            raise JournalError("operation transition metadata is invalid")
        with self._exclusive():
            records = self._read_records_unlocked()
            latest = self._latest(records)
            previous = latest.get(op_id)
            if previous is None:
                raise JournalError("operation does not exist")
            if state not in _TRANSITIONS[previous["state"]]:
                raise JournalError("operation transition is not allowed")
            attempt = previous["attempt"] + (1 if state in {"running", "dispatching"} else 0)
            record = {
                **previous,
                "ts": _utc_now(),
                "state": state,
                "stage": stage,
                "attempt": attempt,
                "owner": owner,
            }
            self._append_unlocked(record)
            return record

    def pending(self, kind: str) -> list[dict]:
        if kind not in KINDS:
            raise JournalError("operation kind is invalid")
        latest = self.latest()
        return [
            record for record in latest.values()
            if record["kind"] == kind and record["state"] in RETRYABLE_STATES
        ]

    def load_payload(self, op_id: str, name: str) -> bytes:
        if not _PAYLOAD_NAME_RE.fullmatch(name):
            raise JournalError("private payload name is invalid")
        spool_path = self._spool_path(op_id)
        manifest_path = spool_path / "manifest.json"
        payload_path = spool_path / name
        if any(path.is_symlink() for path in (spool_path, manifest_path, payload_path)):
            raise JournalError("private spool must not contain symlinks")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            entry = manifest[name]
            data = payload_path.read_bytes()
        except (OSError, KeyError, json.JSONDecodeError, UnicodeError, TypeError) as exc:
            raise JournalError("private payload is unavailable") from exc
        if (
            not isinstance(entry, dict)
            or type(entry.get("size")) is not int
            or entry["size"] != len(data)
            or not isinstance(entry.get("sha256"), str)
            or not _SHA256_RE.fullmatch(entry["sha256"])
            or entry["sha256"] != _sha256(data)
        ):
            raise JournalError("private payload verification failed")
        return data

    def discard_payloads(self, op_id: str) -> None:
        spool_path = self._spool_path(op_id)
        if spool_path.is_symlink():
            raise JournalError("private spool must not be a symlink")
        if spool_path.is_dir():
            shutil.rmtree(spool_path)
            _fsync_directory(self.spool_dir)

    def recover_interrupted(self) -> list[dict]:
        recovered: list[dict] = []
        cleanup: list[str] = []
        with self._exclusive():
            records = self._read_records_unlocked()
            latest = self._latest(records)
            for op_id, previous in sorted(latest.items()):
                if previous["state"] == "running":
                    state = "retry_wait"
                elif previous["state"] == "dispatching":
                    state = "uncertain"
                elif previous["state"] == "acked":
                    state = "succeeded"
                else:
                    continue
                record = {
                    **previous,
                    "ts": _utc_now(),
                    "state": state,
                    "stage": "recovery",
                    "owner": "recovery",
                }
                self._append_unlocked(record)
                recovered.append(record)
                if previous["state"] == "acked":
                    cleanup.append(op_id)
        for op_id in cleanup:
            try:
                self.discard_payloads(op_id)
            except JournalError:
                pass
        return recovered

    def readiness(self) -> tuple[bool, str]:
        try:
            self.latest()
        except (JournalError, OSError):
            return False, "journal_corrupt"
        return True, "ready"


_DEFAULT_JOURNAL: OperationJournal | None = None


def get_operation_journal() -> OperationJournal:
    global _DEFAULT_JOURNAL
    if _DEFAULT_JOURNAL is None:
        _DEFAULT_JOURNAL = OperationJournal()
    return _DEFAULT_JOURNAL
