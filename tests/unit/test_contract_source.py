from __future__ import annotations

from pathlib import Path

from tools.stackchan_lab.contracts import validate_firmware_route_presence


def test_current_firmware_exposes_virtualized_routes():
    repo_root = Path(__file__).resolve().parents[2]
    assert validate_firmware_route_presence(repo_root) == []
