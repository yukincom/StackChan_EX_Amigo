"""Reject environments predating the 2026-10-20 CA incident remediation."""

from __future__ import annotations

from importlib import metadata
import re


MINIMUM_CERTIFI = "2026.10.19"
MINIMUM_PIP = "26.2.2"
MINIMUM_UV = "0.13.4"
MIGRATION = "From the repository root, recreate the environment using Python 3.11: python tools/bootstrap.py; then run the documented uv sync command."


def version_at_least(actual: str, minimum: str) -> bool:
    # Fail closed for development/prerelease/unrecognized versions.
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", actual):
        return False
    parts = tuple(map(int, actual.split(".")))
    required = tuple(map(int, minimum.split(".")))
    length = max(len(parts), len(required))
    return parts + (0,) * (length - len(parts)) >= required + (0,) * (length - len(required))


def environment_errors() -> list[str]:
    errors = []
    for package, minimum, required in (
        ("certifi", MINIMUM_CERTIFI, True),
        ("pip", MINIMUM_PIP, False),
        ("uv", MINIMUM_UV, False),
    ):
        try:
            actual = metadata.version(package)
        except metadata.PackageNotFoundError:
            if required:
                errors.append(f"{package}>={minimum} is required")
            continue
        if not version_at_least(actual, minimum):
            errors.append(f"{package}>={minimum} is required (found {actual})")
    return errors


def require_secure_environment() -> None:
    errors = environment_errors()
    if errors:
        raise RuntimeError("; ".join([*errors, MIGRATION]))
