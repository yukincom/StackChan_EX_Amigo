"""Shared, testable dotenv loading policy for the voice server modules."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


_TRUE_VALUES = {"1", "true", "yes"}


def load_voice_environment() -> None:
    """Load the selected voice environment unless no-device mode disables it."""
    if os.getenv("STACKCHAN_SKIP_DOTENV", "").strip().lower() in _TRUE_VALUES:
        return
    env_dir = Path(
        os.getenv("STACKCHAN_ENV_DIR", str(Path.home() / "env"))
    ).expanduser()
    load_dotenv(env_dir / ".env")
    load_dotenv(env_dir / ".env.local", override=True)
