from __future__ import annotations

import requests
import pytest

from tools.stackchan_lab.fake_services import FakeServicesState, create_fake_services_app
from tools.stackchan_lab.faults import Fault
from tools.stackchan_lab.firmware_client import FirmwareClient
from tools.stackchan_lab.server_runner import LiveServer, refused_loopback_url


def test_http_status_and_socket_timeout_are_distinct():
    state = FakeServicesState()
    with LiveServer(create_fake_services_app(state)) as server:
        state.faults.add(
            "/v1/chat/completions",
            Fault(kind="http_status", status=504),
        )
        response = FirmwareClient(server.base_url).chat([])
        assert response.status_code == 504

        state.faults.add(
            "/v1/chat/completions",
            Fault(kind="socket_timeout", status=504, delay_seconds=0.2),
        )
        # The client timeout is deliberately shorter than the scripted server
        # delay, producing a real ReadTimeout rather than an HTTP 504.
        with pytest.raises(requests.Timeout):
            FirmwareClient(server.base_url, timeout=0.02).chat([])


def test_connection_refused_uses_an_unopened_loopback_port():
    with refused_loopback_url() as unavailable:
        with pytest.raises(requests.ConnectionError):
            FirmwareClient(unavailable, timeout=0.2).chat([])


def test_malformed_success_response_is_not_valid_json():
    state = FakeServicesState()
    state.faults.add(
        "/v1/chat/completions",
        Fault(kind="malformed_json", status=200),
    )
    with LiveServer(create_fake_services_app(state)) as server:
        response = FirmwareClient(server.base_url).chat([])
    assert response.status_code == 200
    with pytest.raises(requests.JSONDecodeError):
        response.json()
