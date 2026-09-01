"""Versioned HTTP contract plus a deliberately narrow route-presence check."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


CONTRACT_VERSION = "1.0"


@dataclass(frozen=True)
class Endpoint:
    method: str
    path: str
    required_fields: tuple[str, ...] = ()


DEVICE_ENDPOINTS = (
    Endpoint("POST", "/play", ("voice_url",)),
    Endpoint("POST", "/play_local", ("endpoint",)),
    Endpoint("GET", "/audio/status"),
    Endpoint("POST", "/camera/trigger", ("requester", "context", "mode")),
)

SERVER_ENDPOINTS = (
    Endpoint("POST", "/v1/audio/transcriptions", ("file", "model", "language")),
    Endpoint("POST", "/v1/chat/completions", ("messages",)),
    Endpoint("POST", "/v1/audio/speech", ("input", "response_format")),
    Endpoint("POST", "/vision/upload"),
)

_FIRMWARE_ROUTE_RE = re.compile(r'server\.on\("([^"]+)"')


def firmware_routes(source: str) -> set[str]:
    return set(_FIRMWARE_ROUTE_RE.findall(source))


def validate_firmware_route_presence(repo_root: Path) -> list[str]:
    """Check route names only; HTTP semantics are exercised by live tests."""
    path = repo_root / "firmware" / "src" / "WebAPI.cpp"
    if not path.is_file():
        return [f"missing firmware source: {path}"]
    actual = firmware_routes(path.read_text(encoding="utf-8"))
    return [
        f"firmware route missing: {endpoint.path}"
        for endpoint in DEVICE_ENDPOINTS
        if endpoint.path not in actual
    ]
