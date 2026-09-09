from __future__ import annotations

import ipaddress
import os
import shutil
import socket
import tempfile
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
LAB_ROOT = Path(tempfile.mkdtemp(prefix="stackchan-lab-pytest-"))
LAB_JSON_DIR = LAB_ROOT / "server" / "json"
shutil.copytree(REPO_ROOT / "server" / "json_example", LAB_JSON_DIR)

# These values are installed before test modules import the server. Tests must
# never load ~/env, real conversations, models, or live service addresses.
os.environ["STACKCHAN_SKIP_DOTENV"] = "1"
os.environ["STACKCHAN_ENV_DIR"] = str(LAB_ROOT / "env")
os.environ["STACKCHAN_JSON_DIR"] = str(LAB_JSON_DIR)
os.environ["MEMORY_DIR"] = str(LAB_ROOT / "server" / "memory")
os.environ["ARCHIVE_DIR"] = str(LAB_ROOT / "server" / "archive")
os.environ["STACKCHAN_STATE_DIR"] = str(LAB_ROOT / "server" / "state")
os.environ["TRACE_ENABLED"] = "1"
os.environ["TRACE_DIR"] = str(LAB_ROOT / "server" / "state" / "trace")
os.environ["JOURNAL_DIR"] = str(LAB_ROOT / "server" / "state" / "journal")
os.environ["VOICE_STORAGE_DIR"] = str(LAB_ROOT / "server" / "voice_store")
os.environ["VISION_DIR"] = str(LAB_ROOT / "vision-tmp")
os.environ["VISION_ARCHIVE_DIR"] = str(LAB_ROOT / "vision-archive")
os.environ["AI_PROVIDER"] = "lab"
os.environ["AI_SUMMARY_PROVIDER"] = "lab"
os.environ["CONVERSATION_FORECAST_ENABLED"] = "false"
os.environ["ADMIN_TOKEN"] = ""


def pytest_report_header() -> str:
    return "StackChan lab: dotenv skipped; real services/models/device disabled"


@pytest.fixture(autouse=True)
def block_non_loopback_network(monkeypatch):
    """Fail immediately if a test tries to reach a real network host."""
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_getaddrinfo = socket.getaddrinfo
    original_sendto = socket.socket.sendto

    def is_allowed(address) -> bool:
        if not isinstance(address, tuple) or not address:
            return True  # AF_UNIX and other local socket families
        host = address[0]
        if isinstance(host, bytes):
            host = host.decode("ascii", errors="ignore")
        if str(host).lower() == "localhost":
            return True
        try:
            return ipaddress.ip_address(str(host)).is_loopback
        except ValueError:
            return False

    def guarded_connect(sock, address):
        if not is_allowed(address):
            raise AssertionError(f"non-loopback network blocked in tests: {address!r}")
        return original_connect(sock, address)

    def guarded_connect_ex(sock, address):
        if not is_allowed(address):
            raise AssertionError(f"non-loopback network blocked in tests: {address!r}")
        return original_connect_ex(sock, address)

    def guarded_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        if host is not None and not is_allowed((host, port)):
            raise AssertionError(f"non-loopback DNS blocked in tests: {host!r}")
        return original_getaddrinfo(host, port, family, type, proto, flags)

    def guarded_sendto(sock, data, *args):
        if not args:
            raise TypeError("sendto requires an address")
        address = args[-1]
        if not is_allowed(address):
            raise AssertionError(f"non-loopback UDP blocked in tests: {address!r}")
        return original_sendto(sock, data, *args)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
    monkeypatch.setattr(socket.socket, "sendto", guarded_sendto)
    yield
