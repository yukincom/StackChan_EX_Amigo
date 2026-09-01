from __future__ import annotations

from flask import Flask

from tools.stackchan_lab.server_runner import LiveServer
from tools.stackchan_lab.virtual_m5 import VirtualM5State, create_virtual_m5_app


def test_play_contract_and_state_machine():
    state = VirtualM5State(fetch_loopback_audio=False)
    client = create_virtual_m5_app(state).test_client()

    assert client.post("/play", data="not-json").status_code == 400
    assert client.post("/play", json={}).get_json()["error"] == "voice_url_required"

    accepted = client.post("/play", json={"voice_url": "http://127.0.0.1:9/audio.mp3"})
    assert accepted.status_code == 200
    assert client.get("/audio/status").get_json() == {"ready": False, "mode": "play_queue"}
    assert client.post("/play", json={"voice_url": "http://127.0.0.1:9/other.mp3"}).status_code == 409

    assert client.post("/__lab__/advance").get_json()["state"]["mode"] == "play"
    assert client.post("/__lab__/advance").get_json()["state"]["mode"] == "idle"


def test_play_local_rejects_path_traversal():
    client = create_virtual_m5_app(VirtualM5State()).test_client()
    assert client.post("/play_local", json={"endpoint": "ok_japanese"}).status_code == 200
    client.post("/__lab__/reset")
    for unsafe in ("../secret", "folder/file", "folder\\file", ""):
        assert client.post("/play_local", json={"endpoint": unsafe}).status_code == 400


def test_fault_plan_is_consumed_once():
    state = VirtualM5State()
    client = create_virtual_m5_app(state).test_client()
    injected = client.post(
        "/__lab__/fault",
        json={"path": "/play", "kind": "malformed_json", "status": 503, "times": 1},
    )
    assert injected.status_code == 200
    first = client.post("/play", json={"voice_url": "http://127.0.0.1/audio.mp3"})
    assert first.status_code == 503
    assert first.data == b"{"
    second = client.post("/play", json={"voice_url": "http://127.0.0.1/audio.mp3"})
    assert second.status_code == 200


def test_camera_requires_server_callback_and_honors_disabled_state():
    missing = create_virtual_m5_app(VirtualM5State()).test_client()
    assert missing.post("/camera/trigger", json={"mode": "transient"}).status_code == 500

    disabled = create_virtual_m5_app(
        VirtualM5State(callback_server_url="http://127.0.0.1:9", camera_enabled=False)
    ).test_client()
    assert disabled.post("/camera/trigger", json={"mode": "transient"}).status_code == 503


def test_audio_fetch_rejects_http_error_and_invalid_payload():
    audio = Flask("bad-audio")

    @audio.get("/missing.mp3")
    def missing():
        return "missing", 404

    @audio.get("/not-audio.mp3")
    def not_audio():
        return "not really audio", 200, {"Content-Type": "text/plain"}

    with LiveServer(audio) as upstream:
        for path, expected_reason in (
            ("/missing.mp3", "http_404"),
            ("/not-audio.mp3", "invalid_content_type"),
        ):
            state = VirtualM5State()
            client = create_virtual_m5_app(state).test_client()
            assert client.post("/play", json={"voice_url": upstream.base_url + path}).status_code == 200
            assert client.post("/__lab__/advance").status_code == 200
            failed = next(event for event in state.events if event["event"] == "audio_fetch_failed")
            assert failed["reason"] == expected_reason


def test_camera_and_play_have_independent_pending_flags():
    state = VirtualM5State(callback_server_url="http://127.0.0.1:9", fetch_loopback_audio=False)
    client = create_virtual_m5_app(state).test_client()

    assert client.post("/camera/trigger", json={"mode": "transient"}).status_code == 200
    assert client.post("/play", json={"voice_url": "http://127.0.0.1:9/audio.mp3"}).status_code == 200
    snapshot = client.get("/__lab__/state").get_json()
    assert snapshot["camera_pending"] is True
    assert snapshot["play_pending"] is True
    assert snapshot["mode"] == "play_queue"

    # firmware main loop services play before camera.
    assert client.post("/__lab__/advance").get_json()["transition"]["action"] == "fetch_audio"
    assert client.post("/__lab__/advance").get_json()["transition"]["action"] == "audio_completed"
    assert client.post("/__lab__/advance").get_json()["transition"]["action"] == "upload_camera"
