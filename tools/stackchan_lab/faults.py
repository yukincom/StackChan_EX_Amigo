"""Deterministic fault injection shared by the lab services."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from threading import Lock
from typing import Any


SUPPORTED_FAULT_KINDS = {
    "http",
    "http_status",
    "malformed_json",
    "empty",
    "socket_timeout",
}


@dataclass(frozen=True)
class Fault:
    """One scripted HTTP failure.

    ``socket_timeout`` deliberately delays the response. The caller must use a
    shorter client timeout, so it exercises a real timeout exception rather
    than confusing it with an HTTP 504 response.
    """

    kind: str = "http"
    status: int = 500
    body: Any = None
    content_type: str = "application/json"
    delay_seconds: float = 0.0

    def public_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if data["body"] is None:
            data["body"] = {}
        return data


class FaultPlan:
    """Thread-safe FIFO fault plan keyed by request path."""

    def __init__(self) -> None:
        self._items: dict[str, deque[Fault]] = defaultdict(deque)
        self._lock = Lock()

    def add(self, path: str, fault: Fault, times: int = 1) -> None:
        if not path.startswith("/"):
            raise ValueError("fault path must start with /")
        if times < 1:
            raise ValueError("times must be at least 1")
        if fault.kind not in SUPPORTED_FAULT_KINDS:
            raise ValueError(f"unsupported fault kind: {fault.kind}")
        if not 0 <= fault.delay_seconds <= 10:
            raise ValueError("delay_seconds must be between 0 and 10")
        if fault.kind == "socket_timeout" and fault.delay_seconds <= 0:
            raise ValueError("socket_timeout requires a positive delay_seconds")
        with self._lock:
            self._items[path].extend(fault for _ in range(times))

    def pop(self, path: str) -> Fault | None:
        with self._lock:
            queue = self._items.get(path)
            if not queue:
                return None
            fault = queue.popleft()
            if not queue:
                self._items.pop(path, None)
            return fault

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def snapshot(self) -> dict[str, list[dict[str, Any]]]:
        with self._lock:
            return {
                path: [fault.public_dict() for fault in faults]
                for path, faults in sorted(self._items.items())
            }


def fault_from_payload(data: dict[str, Any]) -> Fault:
    """Parse the lab control API payload without conflating timeout and 504."""
    raw_kind = str(data.get("kind", "http_status"))
    kind = "socket_timeout" if raw_kind == "timeout" else raw_kind
    default_delay = 0.25 if kind == "socket_timeout" else 0.0
    return Fault(
        kind=kind,
        status=int(data.get("status", 504 if kind == "socket_timeout" else 500)),
        body=data.get("body"),
        content_type=str(data.get("content_type", "application/json")),
        delay_seconds=float(data.get("delay_seconds", default_delay)),
    )
