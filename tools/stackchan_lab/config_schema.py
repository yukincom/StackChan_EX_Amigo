"""Read-only, value-redacting configuration contract checks."""

from __future__ import annotations

import ast
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from .doctor import read_env_keys


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    severity: str
    location: str
    message: str
    secret: bool = False
    migration: str = "none"

    def as_dict(self) -> dict:
        return asdict(self)


_INTERNAL_ENV_KEYS = {
    "STACKCHAN_ENV_DIR", "STACKCHAN_JSON_DIR", "STACKCHAN_SKIP_DOTENV",
}
_HELPER_NAMES = {"_int_env", "_float_env", "_path_env", "_bool_env", "_csv_env"}


def _issue(code, severity, location, message, *, secret=False, migration="none"):
    return ValidationIssue(code, severity, location, message, secret, migration)


def discover_python_env_keys(repo_root: Path) -> set[str]:
    keys: set[str] = set()
    for path in sorted((repo_root / "server").rglob("*.py")):
        if any(part in {"venv", ".venv", ".pio"} for part in path.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            function_name = ""
            if isinstance(node.func, ast.Name):
                function_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                function_name = node.func.attr
            if function_name not in _HELPER_NAMES | {"getenv"}:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                keys.add(first.value)
    return keys


def validate_env_contract(repo_root: Path, env_dir: Path | None = None) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    example, duplicates = read_env_keys(repo_root / ".env.example")
    for key in duplicates:
        issues.append(_issue("ENV_DUPLICATE", "error", f".env.example:{key}", "key is duplicated"))
    code_keys = discover_python_env_keys(repo_root) - _INTERNAL_ENV_KEYS
    example_keys = set(example)
    for key in sorted(code_keys - example_keys):
        issues.append(_issue("ENV_EXAMPLE_MISSING", "error", key, "code key is absent from .env.example"))
    for key in sorted(example_keys - code_keys):
        issues.append(_issue("ENV_EXAMPLE_UNUSED", "warning", key, "example key is not read by Python code"))

    if env_dir is not None:
        merged: dict[str, str] = {}
        for filename in (".env", ".env.local"):
            values, file_duplicates = read_env_keys(env_dir / filename)
            merged.update(values)
            for key in file_duplicates:
                issues.append(_issue("ENV_DUPLICATE", "error", f"{filename}:{key}", "key is duplicated"))
        for key in sorted(set(merged) - example_keys - _INTERNAL_ENV_KEYS):
            issues.append(_issue("ENV_UNKNOWN", "warning", key, "configured key is not in .env.example"))
    return issues


def _load_json(path: Path, issues: list[ValidationIssue]):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        issues.append(_issue("JSON_INVALID", "error", path.name, "document is not valid JSON"))
        return None


def _validate_announcements(value, location: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not isinstance(value, list):
        return [_issue("JSON_SHAPE", "error", location, "announcements must be an array")]
    for index, item in enumerate(value):
        here = f"{location}/{index}"
        if not isinstance(item, dict):
            issues.append(_issue("JSON_SHAPE", "error", here, "announcement must be an object"))
            continue
        if type(item.get("hour")) is not int or not 0 <= item["hour"] <= 23:
            issues.append(_issue("ANNOUNCEMENT_HOUR", "error", f"{here}/hour", "hour must be 0..23"))
        if type(item.get("minute")) is not int or not 0 <= item["minute"] <= 59:
            issues.append(_issue("ANNOUNCEMENT_MINUTE", "error", f"{here}/minute", "minute must be 0..59"))
        if not isinstance(item.get("message"), str) or not item["message"].strip():
            issues.append(_issue("ANNOUNCEMENT_MESSAGE", "error", f"{here}/message", "message is required", secret=True))
        for key in ("with_weather", "weekday_only", "holiday_only"):
            if key not in item:
                issues.append(_issue(
                    "ANNOUNCEMENT_FLAG_MISSING", "warning", f"{here}/{key}",
                    "day/weather flag is missing", migration="safe",
                ))
            elif type(item[key]) is not bool:
                issues.append(_issue("ANNOUNCEMENT_FLAG_TYPE", "error", f"{here}/{key}", "flag must be boolean"))
    return issues


def _validate_member(value, location: str) -> list[ValidationIssue]:
    if not isinstance(value, dict):
        return [_issue("JSON_SHAPE", "error", location, "member document must be an object")]
    issues: list[ValidationIssue] = []
    if "children" in value and "master" not in value:
        issues.append(_issue(
            "MEMBER_CHILDREN_DRIFT", "error", f"{location}/children",
            "children cannot be assigned to master automatically", migration="manual",
        ))
    if not isinstance(value.get("master"), dict):
        issues.append(_issue("MEMBER_MASTER", "error", f"{location}/master", "master object is required", secret=True))
    for key in ("family", "friends"):
        if not isinstance(value.get(key), list):
            issues.append(_issue("MEMBER_LIST", "error", f"{location}/{key}", "member group must be an array", secret=True))
    return issues


def _validate_string_map(value, location: str) -> list[ValidationIssue]:
    if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
        return [_issue("JSON_STRING_MAP", "error", location, "document must map strings to strings", secret=True)]
    return []


def _validate_voice_catalog(value, location: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not isinstance(value, dict):
        return [_issue("JSON_SHAPE", "error", location, "voice catalog must be an object")]
    for group in ("pc_cache", "stack_sd"):
        entries = value.get(group)
        if not isinstance(entries, list):
            issues.append(_issue("VOICE_CATALOG_GROUP", "error", f"{location}/{group}", "catalog group must be an array"))
            continue
        for index, item in enumerate(entries):
            required = {"id", "text", "filename"}
            if not isinstance(item, dict) or not all(isinstance(item.get(key), str) and item[key] for key in required):
                issues.append(_issue("VOICE_CATALOG_ENTRY", "error", f"{location}/{group}/{index}", "catalog entry fields are invalid", secret=True))
    return issues


def validate_json_documents(json_dir: Path) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    validators = {
        "announcements.json": _validate_announcements,
        "member.json": _validate_member,
        "reading_map.json": _validate_string_map,
        "voice_cache_catalog.json": _validate_voice_catalog,
    }
    for filename, validator in validators.items():
        value = _load_json(json_dir / filename, issues)
        if value is not None:
            issues.extend(validator(value, filename))
    return issues


def validate_sd_templates(repo_root: Path) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    basic_path = repo_root / "Copy-to-SD" / "yaml" / "SC_BasicConfig.yaml"
    ex_path = repo_root / "Copy-to-SD" / "app" / "AiStackChanEx" / "SC_ExConfig.yaml"
    try:
        basic = basic_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        issues.append(_issue("SD_BASIC_MISSING", "error", basic_path.name, "basic template is unavailable"))
    else:
        match = re.search(r"(?m)^secret_config_show:\s*(true|false)\b", basic, re.IGNORECASE)
        if not match:
            issues.append(_issue("SD_SECRET_LOG_FLAG", "error", basic_path.name, "secret log flag is missing", secret=True))
        elif match.group(1).lower() != "false":
            issues.append(_issue("SD_SECRET_LOG_ENABLED", "error", "SC_BasicConfig.yaml:secret_config_show", "secret serial logging must be disabled", secret=True, migration="safe"))
        for key in ("lower_limit", "upper_limit"):
            if not re.search(rf"(?m)^\s+{key}:\s*", basic):
                issues.append(_issue("SD_SERVO_LIMIT_MISSING", "warning", f"SC_BasicConfig.yaml:servo.{key}", "servo limit is absent and requires device-specific review", migration="manual"))
    try:
        ex = ex_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        issues.append(_issue("SD_EX_MISSING", "error", ex_path.name, "extended template is unavailable"))
    else:
        if re.search(r"https?://[^\s\"']*\*", ex):
            issues.append(_issue("SD_URL_PLACEHOLDER", "warning", ex_path.name, "URL still contains a non-runnable wildcard placeholder", secret=True, migration="manual"))
    return issues


def validate_all(repo_root: Path, env_dir: Path | None = None) -> dict:
    repo_root = repo_root.expanduser().resolve()
    issues = [
        *validate_env_contract(repo_root, env_dir),
        *validate_json_documents(repo_root / "server" / "json_example"),
        *validate_sd_templates(repo_root),
    ]
    counts = {severity: sum(issue.severity == severity for issue in issues) for severity in ("error", "warning", "info")}
    return {
        "schema_version": 1,
        "status": "error" if counts["error"] else "ok",
        "counts": counts,
        "issues": [issue.as_dict() for issue in issues],
    }


def plan_migrations(json_dir: Path) -> dict:
    path = json_dir / "announcements.json"
    issues: list[ValidationIssue] = []
    value = _load_json(path, issues)
    safe = 0
    manual = 0
    if isinstance(value, list):
        for item in value:
            if not isinstance(item, dict):
                continue
            if "weekday_only" not in item and "holiday_only" not in item:
                safe += 1
            elif (
                item.get("weekday_only") is False
                and item.get("holiday_only") is False
                and not item.get("_explicit_long_vacation")
            ):
                safe += 1
            if "children" in item:
                manual += 1
    member = _load_json(json_dir / "member.json", issues)
    if isinstance(member, dict) and "children" in member and "master" not in member:
        manual += 1
    return {
        "schema_version": 1,
        "mode": "read_only",
        "safe_changes": safe,
        "manual_changes": manual,
        "apply_performed": False,
    }
