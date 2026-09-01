from __future__ import annotations

from urllib.parse import urlparse

from tools.stackchan_lab.scenario import run_smoke_scenario
from tools.stackchan_lab.server_runner import LiveServer
from tools.stackchan_lab.virtual_m5 import VirtualM5State, create_virtual_m5_app

from services import voice_service


def test_self_contained_smoke_scenario():
    report = run_smoke_scenario()
    assert report["status"] == "degraded"
    assert all(report["checks"].values())
    assert report["known_gaps"]["camera_mode_not_transmitted_by_current_firmware"] is True


def test_server_push_reaches_virtual_device(monkeypatch):
    state = VirtualM5State(fetch_loopback_audio=False)
    with LiveServer(create_virtual_m5_app(state)) as device:
        parsed = urlparse(device.base_url)
        monkeypatch.setattr(voice_service.config, "M5STACK_IP", parsed.hostname)
        monkeypatch.setattr(voice_service.config, "M5STACK_PORT", parsed.port)
        assert voice_service.push_to_m5stack(
            "http://127.0.0.1:5001/voice/cache_fixture.mp3",
            fire_and_forget=False,
        ) is True
    assert state.events[-1]["event"] == "play_accepted"
