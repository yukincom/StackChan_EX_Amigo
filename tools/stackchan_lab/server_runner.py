"""Small in-process HTTP server used by contract tests and the scenario CLI."""

from __future__ import annotations

import threading
import socket
from contextlib import contextmanager
from types import TracebackType
from typing import Any

from werkzeug.serving import BaseWSGIServer, make_server


@contextmanager
def refused_loopback_url():
    """Reserve a loopback port without listening, producing connection-refused."""
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.bind(("127.0.0.1", 0))
    try:
        yield f"http://127.0.0.1:{holder.getsockname()[1]}"
    finally:
        holder.close()


class LiveServer:
    def __init__(self, app: Any, host: str = "127.0.0.1", port: int = 0) -> None:
        self._server: BaseWSGIServer = make_server(host, port, app, threaded=True)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="stackchan-lab-http",
            daemon=True,
        )
        self.base_url = f"http://{host}:{self._server.server_port}"

    def __enter__(self) -> "LiveServer":
        self._thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._server.shutdown()
        self._thread.join(timeout=5)
        self._server.server_close()
