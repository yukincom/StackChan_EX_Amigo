#!/usr/bin/env python3
"""Compatibility entry point: exec the native CLI, never fall back to Python."""

from __future__ import annotations

import os
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    filename = "stackchanctl.exe" if os.name == "nt" else "stackchanctl"
    default = REPO_ROOT / "native" / "stackchanctl" / "target" / "release" / filename
    binary = Path(os.environ.get("STACKCHANCTL_BIN") or default).expanduser().resolve()
    if not binary.is_file() or not os.access(binary, os.X_OK):
        print("stackchanctl: native binary is unavailable; build with: cargo build --release --locked --manifest-path native/stackchanctl/Cargo.toml", file=sys.stderr)
        return 1
    environment = os.environ.copy()
    environment["STACKCHAN_REPO_ROOT"] = str(REPO_ROOT)
    environment["STACKCHAN_PYTHON"] = sys.executable
    try:
        os.execve(str(binary), [str(binary), *(sys.argv[1:] if argv is None else argv)], environment)
    except OSError as exc:
        print(f"stackchanctl: cannot execute native binary: {exc.strerror}", file=sys.stderr)
        return 1
    raise AssertionError("exec returned unexpectedly")


if __name__ == "__main__":
    raise SystemExit(main())
