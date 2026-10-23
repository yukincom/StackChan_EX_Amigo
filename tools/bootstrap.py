#!/usr/bin/env python3
"""Bootstrap secure packaging tools without trusting an unpinned first download."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import venv


ROOT = Path(__file__).resolve().parents[1]
PIP_VERSION = "26.2.2"
UV_VERSION = "0.13.4"
# Python security announcement, 2026-10-20. The hash is authenticated out of band.
PIP_WHEEL = (
    "https://files.pythonhosted.org/packages/3f/06/"
    "3e4420302363fbb71be3c7f35ce042f54bfca330e3e66b3a2e3c4cca579f/"
    "pip-26.2.2-py3-none-any.whl"
    "#sha256=fd7f4dfa8190e014013b9f08f3e973f4e4c26ffd179a204895533a7254813a70"
)


def bootstrap() -> Path:
    if sys.version_info[:2] != (3, 11):
        raise RuntimeError("Use Python 3.11 to recreate this project's environment")
    environment = ROOT / ".bootstrap-venv"
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.exists():
        venv.EnvBuilder(with_pip=True).create(environment)
    pip = [str(python), "-I", "-m", "pip", "--isolated", "--disable-pip-version-check", "--no-cache-dir"]
    # No dependency/index lookup occurs before installing the hash-pinned wheel.
    subprocess.run([*pip, "install", "--upgrade", "--no-deps", PIP_WHEEL], check=True)
    subprocess.run([*pip, "install", "--upgrade", "--only-binary=:all:", f"uv=={UV_VERSION}"], check=True)
    uv = environment / ("Scripts/uv.exe" if os.name == "nt" else "bin/uv")
    subprocess.run([str(uv), "--version"], check=True)
    return uv


if __name__ == "__main__":
    print(f"Secure uv: {bootstrap()}")
