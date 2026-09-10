"""Offline, secret-safe environment diagnostics for StackChan_EX_Amigo."""

from __future__ import annotations

import importlib.util
import json
import os
import platform
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    message: str
    hint: str = ""

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


def read_env_keys(path: Path) -> tuple[dict[str, str], list[str]]:
    """Read dotenv-like key/value pairs without returning values in diagnostics."""
    values: dict[str, str] = {}
    duplicates: list[str] = []
    if not path.is_file():
        return values, duplicates
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or not key.replace("_", "A").isalnum():
            continue
        if key in values and key not in duplicates:
            duplicates.append(key)
        values[key] = value.strip().strip('"').strip("'")
    return values, duplicates


def _ok(name: str, message: str) -> CheckResult:
    return CheckResult(name, "ok", message)


def _warn(name: str, message: str, hint: str = "") -> CheckResult:
    return CheckResult(name, "warn", message, hint)


def _error(name: str, message: str, hint: str = "") -> CheckResult:
    return CheckResult(name, "error", message, hint)


def _find_writable_ancestor(path: Path) -> Path | None:
    candidate = path.expanduser()
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate if candidate.exists() else None


def _module_available(module: str) -> bool:
    """Return False when either a module or one of its parents is absent."""
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _check_number(
    env: dict[str, str],
    key: str,
    cast: Callable[[str], float],
    minimum: float,
    maximum: float,
) -> CheckResult | None:
    raw = env.get(key, "").strip()
    if not raw:
        return None
    try:
        value = cast(raw)
    except ValueError:
        return _error(f"env:{key}", "configured value is not numeric")
    if not minimum <= value <= maximum:
        return _error(f"env:{key}", f"configured value is outside {minimum:g}..{maximum:g}")
    return _ok(f"env:{key}", "numeric value is valid")


def run_doctor(repo_root: Path, env_dir: Path | None = None) -> list[CheckResult]:
    repo_root = repo_root.expanduser().resolve()
    env_dir = (env_dir or Path.home() / "env").expanduser().resolve()
    results: list[CheckResult] = []

    if (repo_root / "server" / "app.py").is_file() and (repo_root / "firmware" / "platformio.ini").is_file():
        results.append(_ok("repository", "StackChan_EX_Amigo layout detected"))
    else:
        results.append(_error("repository", "required server/firmware files are missing"))

    py_ok = sys.version_info[:2] == (3, 11)
    results.append(
        _ok("python", f"Python {platform.python_version()}")
        if py_ok
        else _warn("python", f"Python {platform.python_version()} is not the pinned 3.11 runtime")
    )

    core_modules = {
        "flask": "Flask",
        "werkzeug": "Werkzeug",
        "requests": "requests",
        "dotenv": "python-dotenv",
        "apscheduler": "APScheduler",
        "jpholiday": "jpholiday",
    }
    optional_modules = {
        "google.genai": "google-genai",
        "openai": "openai",
        "huggingface_hub": "huggingface-hub",
        "mlx_lm": "mlx-lm",
        "mlx_vlm": "mlx-vlm",
        "kokoro": "kokoro",
    }
    for module, package in core_modules.items():
        if not _module_available(module):
            results.append(_error(f"python:{package}", "required package is not importable"))
        else:
            results.append(_ok(f"python:{package}", "package is importable"))
    for module, package in optional_modules.items():
        if not _module_available(module):
            results.append(_warn(f"python:{package}", "optional package is not importable"))
        else:
            results.append(_ok(f"python:{package}", "optional package is importable"))

    merged_env: dict[str, str] = {}
    for filename, required in ((".env", True), (".env.local", False)):
        path = env_dir / filename
        values, duplicates = read_env_keys(path)
        if path.is_file():
            merged_env.update(values)
            results.append(_ok(f"dotenv:{filename}", "file is readable"))
        elif required:
            results.append(_warn(f"dotenv:{filename}", "file is missing", "copy .env.example and configure it"))
        for key in duplicates:
            results.append(_error(f"dotenv:{filename}:{key}", "key is defined more than once"))

    example_values, example_duplicates = read_env_keys(repo_root / ".env.example")
    if example_values:
        results.append(_ok("dotenv:example", "example file is parseable"))
    else:
        results.append(_error("dotenv:example", "example file is missing or empty"))
    for key in example_duplicates:
        results.append(_error(f"dotenv:example:{key}", "key is defined more than once"))

    numeric_checks = (
        ("M5STACK_PORT", int, 1, 65535),
        ("M5STACK_TIMEOUT", int, 1, 300),
        ("VOICE_REQUEST_TIMEOUT", int, 1, 600),
        ("SERVER_PORT", int, 1, 65535),
        ("SPEECH_MIN_BYTES", int, 1, 100_000_000),
        ("AI_CHAT_TEMPERATURE", float, 0, 2),
        ("AI_SUMMARY_TEMPERATURE", float, 0, 2),
        ("WHISPER_NO_SPEECH_THOLD", float, 0, 1),
        ("WEATHER_MORNING_HOUR", int, 0, 23),
        ("WEATHER_MORNING_MINUTE", int, 0, 59),
    )
    for key, cast, minimum, maximum in numeric_checks:
        result = _check_number(merged_env, key, cast, minimum, maximum)
        if result:
            results.append(result)

    for key in ("VOICE_SERVER_URL", "VOICEVOX_URL", "M5STACK_URL", "OPENAI_BASE_URL"):
        raw = merged_env.get(key, "").strip()
        if not raw:
            continue
        parsed = urlparse(raw)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            results.append(_error(f"env:{key}", "configured URL is invalid"))
        else:
            results.append(_ok(f"env:{key}", "configured URL shape is valid"))

    if shutil.which("ffmpeg"):
        results.append(_ok("executable:ffmpeg", "ffmpeg is available"))
    else:
        results.append(_warn("executable:ffmpeg", "ffmpeg is unavailable", "MP3 conversion tests will be skipped"))

    for key in ("WHISPER_CLI", "WHISPER_MODEL", "WHISPER_VAD_MODEL"):
        raw = merged_env.get(key, "").strip()
        if not raw:
            results.append(_warn(f"env:{key}", "path is not configured"))
        elif Path(raw).expanduser().exists():
            results.append(_ok(f"env:{key}", "configured path exists"))
        else:
            results.append(_error(f"env:{key}", "configured path does not exist"))

    json_dirs = [repo_root / "server" / "json_example"]
    configured_json = repo_root / "server" / "json"
    if configured_json.is_dir():
        json_dirs.append(configured_json)
    for json_dir in json_dirs:
        for path in sorted(json_dir.glob("*.json")):
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                results.append(_error(f"json:{json_dir.name}/{path.name}", "JSON is invalid"))
            else:
                results.append(_ok(f"json:{json_dir.name}/{path.name}", "JSON is valid"))

    for key, default in (
        ("MEMORY_DIR", repo_root / "server" / "memory"),
        ("ARCHIVE_DIR", repo_root / "server" / "archive"),
        ("VOICE_STORAGE_DIR", repo_root / "server" / "voice_store"),
    ):
        raw = merged_env.get(key, "").strip()
        path = Path(raw).expanduser() if raw else default
        if not path.is_absolute():
            path = repo_root / "server" / path
        ancestor = _find_writable_ancestor(path)
        if ancestor is not None and os.access(ancestor, os.W_OK):
            results.append(_ok(f"writable:{key}", "target or parent is writable"))
        else:
            results.append(_error(f"writable:{key}", "target and its existing parents are not writable"))

    return results


def doctor_summary(results: list[CheckResult]) -> dict[str, object]:
    counts = {status: sum(item.status == status for item in results) for status in ("ok", "warn", "error")}
    return {
        "status": "error" if counts["error"] else "ok",
        "counts": counts,
        "checks": [item.as_dict() for item in results],
    }
