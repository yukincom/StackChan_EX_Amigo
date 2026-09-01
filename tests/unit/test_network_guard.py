from __future__ import annotations

import socket

import pytest


def test_non_loopback_connections_are_blocked_by_test_harness():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(AssertionError, match="non-loopback network blocked"):
            sock.connect(("203.0.113.1", 80))
    finally:
        sock.close()


def test_non_loopback_dns_is_blocked_by_test_harness():
    with pytest.raises(AssertionError, match="non-loopback DNS blocked"):
        socket.getaddrinfo("example.com", 443)

    assert socket.getaddrinfo("localhost", 80)


def test_non_loopback_udp_is_blocked_by_test_harness():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        with pytest.raises(AssertionError, match="non-loopback UDP blocked"):
            sock.sendto(b"fixture", ("203.0.113.1", 53))
    finally:
        sock.close()
