from __future__ import annotations

from flask import Flask, jsonify, request

from tools.stackchan_lab.server_runner import LiveServer
from tools.stackchan_lab.virtual_m5 import VirtualM5State, create_virtual_m5_app


def _callback_app(received: list[int]) -> Flask:
    app = Flask("camera-callback")

    @app.post("/vision/upload")
    def upload():
        received.append(len(request.get_data(cache=False)))
        return jsonify(success=True, status="processing")

    return app


def _trigger_and_advance(device_url: str):
    import requests

    trigger = requests.post(
        f"{device_url}/camera/trigger",
        json={"requester": "user", "context": "fixture", "mode": "transient"},
        timeout=2,
    )
    advance = requests.post(f"{device_url}/__lab__/advance", timeout=2)
    return trigger, advance


def test_response_lost_after_full_body_is_202_without_retry():
    received: list[int] = []
    with LiveServer(_callback_app(received)) as callback:
        state = VirtualM5State(callback_server_url=callback.base_url)
        state.camera_transport.add("response_lost_after_body")
        with LiveServer(create_virtual_m5_app(state)) as device:
            trigger, advance = _trigger_and_advance(device.base_url)

    assert trigger.status_code == 200
    assert advance.status_code == 200
    assert received == [838]
    attempts = [event for event in state.events if event["event"] == "camera_transport_attempt"]
    assert len(attempts) == 1
    assert attempts[0]["body_sent"] is True
    assert attempts[0]["status"] == 202
    assert state.camera_await is True


def test_partial_body_retries_once_then_succeeds():
    received: list[int] = []
    with LiveServer(_callback_app(received)) as callback:
        state = VirtualM5State(callback_server_url=callback.base_url)
        state.camera_transport.add("partial_send")
        state.camera_transport.add("normal")
        with LiveServer(create_virtual_m5_app(state)) as device:
            trigger, advance = _trigger_and_advance(device.base_url)

    assert trigger.status_code == 200
    assert advance.status_code == 200
    assert received == [838]
    attempts = [event for event in state.events if event["event"] == "camera_transport_attempt"]
    assert [attempt["outcome"] for attempt in attempts] == ["partial_send", "normal"]
    assert [attempt["body_sent"] for attempt in attempts] == [False, True]
    assert state.camera_await is True
