"""A deterministic HTTP state machine for the current M5Stack firmware API."""

from __future__ import annotations

import hashlib
import ipaddress
import time
from collections import deque
from dataclasses import dataclass, field
from threading import RLock
from typing import Any
from urllib.parse import urlparse

import requests
from flask import Flask, Response, jsonify, request

from .faults import FaultPlan, fault_from_payload


@dataclass
class CameraTransportPlan:
    """Firmware-specific upload outcomes that plain HTTP status cannot model."""

    _outcomes: deque[str] = field(default_factory=deque)
    _lock: RLock = field(default_factory=RLock, repr=False)

    def add(self, outcome: str, times: int = 1) -> None:
        if outcome not in {"normal", "partial_send", "response_lost_after_body"}:
            raise ValueError(f"unsupported camera transport outcome: {outcome}")
        if times < 1:
            raise ValueError("times must be at least 1")
        with self._lock:
            self._outcomes.extend(outcome for _ in range(times))

    def pop(self) -> str:
        with self._lock:
            return self._outcomes.popleft() if self._outcomes else "normal"

    def clear(self) -> None:
        with self._lock:
            self._outcomes.clear()

    def snapshot(self) -> list[str]:
        with self._lock:
            return list(self._outcomes)


def _synthetic_jpeg() -> bytes:
    # Vision endpoint only requires JPEG-like bytes larger than 500 bytes. The
    # VLM boundary is replaced in lab scenarios, so no private photo is needed.
    return b"\xff\xd8\xff\xe0" + (b"STACKCHAN-LAB" * 64) + b"\xff\xd9"


def _safe_local_endpoint(value: str) -> bool:
    return bool(value) and "/" not in value and "\\" not in value and ".." not in value


def _loopback_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme != "http" or not parsed.hostname:
        return False
    if parsed.hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        return False


def _audio_response_valid(response: requests.Response) -> tuple[bool, str]:
    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    body = response.content
    if not response.ok:
        return False, f"http_{response.status_code}"
    if not body:
        return False, "empty_body"
    if not content_type.startswith("audio/"):
        return False, "invalid_content_type"
    if not (body.startswith(b"RIFF") or body.startswith(b"ID3") or body[:2] in {b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"}):
        return False, "invalid_audio_magic"
    return True, ""


@dataclass
class VirtualM5State:
    callback_server_url: str = ""
    camera_enabled: bool = True
    camera_upload_v2: bool = False
    fetch_loopback_audio: bool = True
    play_pending: bool = False
    play_active: bool = False
    camera_pending: bool = False
    camera_busy: bool = False
    camera_await: bool = False
    pending_voice_url: str = ""
    pending_camera: dict[str, str] | None = None
    expression: int = 0
    events: list[dict[str, Any]] = field(default_factory=list)
    faults: FaultPlan = field(default_factory=FaultPlan)
    camera_transport: CameraTransportPlan = field(default_factory=CameraTransportPlan)
    _sequence: int = 0
    _lock: RLock = field(default_factory=RLock, repr=False)

    @property
    def mode(self) -> str:
        """Match the priority order of firmware `/audio/status`."""
        if self.camera_busy:
            return "camera"
        if self.camera_await:
            return "camera_await"
        if self.play_active:
            return "play"
        if self.play_pending:
            return "play_queue"
        return "idle"

    def record(self, event: str, **data: Any) -> dict[str, Any]:
        with self._lock:
            self._sequence += 1
            item = {"sequence": self._sequence, "event": event, **data}
            self.events.append(item)
            return item

    def reset(self) -> None:
        with self._lock:
            self.play_pending = False
            self.play_active = False
            self.camera_pending = False
            self.camera_busy = False
            self.camera_await = False
            self.pending_voice_url = ""
            self.pending_camera = None
            self.expression = 0
            self.events.clear()
            self.faults.clear()
            self.camera_transport.clear()
            self._sequence = 0

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "mode": self.mode,
                "ready": self.mode == "idle",
                "play_pending": self.play_pending,
                "play_active": self.play_active,
                "camera_pending": self.camera_pending,
                "camera_busy": self.camera_busy,
                "camera_await": self.camera_await,
                "pending_voice_url": self.pending_voice_url,
                "pending_camera": dict(self.pending_camera or {}),
                "expression": self.expression,
                "camera_enabled": self.camera_enabled,
                "camera_upload_v2": self.camera_upload_v2,
                "events": list(self.events),
                "faults": self.faults.snapshot(),
                "camera_transport": self.camera_transport.snapshot(),
            }

    def begin_advance(self) -> dict[str, Any]:
        with self._lock:
            # main.cpp processes play before camera on each loop. A queued play
            # waits while the camera owns the hardware.
            if self.play_pending and not self.camera_busy:
                self.play_pending = False
                self.play_active = True
                self.record("audio_started", voice_url=self.pending_voice_url)
                return {"action": "fetch_audio", "voice_url": self.pending_voice_url}
            if self.play_active:
                self.play_active = False
                self.pending_voice_url = ""
                self.record("audio_completed")
                return {"action": "audio_completed"}
            if self.camera_pending and not self.camera_busy:
                payload = dict(self.pending_camera or {})
                self.camera_pending = False
                self.camera_busy = True
                self.record("camera_capture_started", **payload)
                return {"action": "upload_camera", "payload": payload}
            if self.camera_await:
                self.camera_await = False
                self.pending_camera = None
                self.record("camera_await_completed")
                return {"action": "camera_await_completed"}
            return {"action": "idle"}

    def finish_camera_upload(self, success: bool, status: int | None, error: str = "") -> None:
        with self._lock:
            self.camera_busy = False
            self.camera_await = success
            if not success:
                self.pending_camera = None
            self.record("camera_upload", success=success, status=status, error=error)


def _fault_response(fault: Fault):
    if fault.delay_seconds:
        time.sleep(fault.delay_seconds)
    if fault.kind == "malformed_json":
        return Response("{", status=fault.status, content_type="application/json")
    if fault.kind == "empty":
        return Response(b"", status=fault.status, content_type=fault.content_type)
    body = fault.body
    if body is None:
        label = "simulated_timeout" if fault.kind == "socket_timeout" else "simulated_fault"
        body = {"success": False, "error": label}
    if isinstance(body, (dict, list)):
        return jsonify(body), fault.status
    return Response(str(body), status=fault.status, content_type=fault.content_type)


def create_virtual_m5_app(state: VirtualM5State | None = None) -> Flask:
    state = state or VirtualM5State()
    app = Flask("virtual-m5stack")
    app.config["VIRTUAL_M5_STATE"] = state

    def scripted_fault():
        fault = state.faults.pop(request.path)
        if fault is None:
            return None
        state.record("fault_injected", path=request.path, kind=fault.kind, status=fault.status)
        return _fault_response(fault)

    @app.post("/play")
    def play():
        fault = scripted_fault()
        if fault is not None:
            return fault
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify(success=False, error="invalid_json"), 400
        voice_url = str(data.get("voice_url", "")).strip()
        if not voice_url:
            return jsonify(success=False, error="voice_url_required"), 400
        with state._lock:
            if state.camera_await:
                state.camera_await = False
                state.pending_camera = None
                state.record("camera_await_cleared_by_play")
            if state.play_pending or state.play_active:
                return jsonify(success=False, error="play_busy"), 409
            state.pending_voice_url = voice_url
            state.play_pending = True
            state.record("play_accepted", voice_url=voice_url)
        return jsonify(success=True)

    @app.post("/play_local")
    def play_local():
        fault = scripted_fault()
        if fault is not None:
            return fault
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify(success=False, error="invalid_json"), 400
        endpoint = str(data.get("endpoint", "")).strip()
        if not _safe_local_endpoint(endpoint):
            return jsonify(success=False, error="endpoint_required"), 400
        with state._lock:
            if state.camera_await:
                state.camera_await = False
                state.pending_camera = None
                state.record("camera_await_cleared_by_play_local")
            if state.play_pending or state.play_active:
                return jsonify(success=False, error="play_busy"), 409
            state.pending_voice_url = f"sd:/stack_sd_audio/{endpoint}.mp3"
            state.play_pending = True
            state.record("play_local_accepted", endpoint=endpoint)
        return jsonify(success=True)

    @app.get("/audio/status")
    def audio_status():
        fault = scripted_fault()
        if fault is not None:
            return fault
        snapshot = state.snapshot()
        return jsonify(ready=snapshot["ready"], mode=snapshot["mode"])

    @app.post("/camera/trigger")
    def camera_trigger():
        fault = scripted_fault()
        if fault is not None:
            return fault
        if not state.camera_enabled:
            return jsonify(success=False, error="camera_disabled"), 503
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify(success=False, error="invalid_json"), 400
        if not state.callback_server_url:
            return jsonify(success=False, error="base_url_required"), 500
        with state._lock:
            if state.camera_pending or state.camera_busy:
                return jsonify(success=False, error="camera_busy"), 409
            state.camera_await = False
            payload = {
                "requester": str(data.get("requester", "user")).strip() or "user",
                "context": str(data.get("context", "")),
                "mode": "archive" if data.get("mode") == "archive" else "transient",
                "speaker": str(data.get("speaker", "master")).strip() or "master",
                "speaker_label": str(data.get("speaker_label", "")),
                "announce_endpoint": str(data.get("announce_endpoint", "")),
            }
            state.pending_camera = payload
            state.camera_pending = True
            state.record("camera_accepted", **payload)
        return jsonify(success=True)

    @app.get("/speech")
    def legacy_speech():
        state.record("legacy_speech", say=request.args.get("say", ""), voice=request.args.get("voice", ""))
        return Response("OK", content_type="text/plain")

    @app.get("/chat")
    def legacy_chat():
        state.record("legacy_chat", text=request.args.get("text", ""), voice=request.args.get("voice", ""))
        return Response("<html><body></body></html>", content_type="text/html")

    @app.get("/face")
    def legacy_face():
        try:
            expression = int(request.args.get("expression", "0"))
        except ValueError:
            expression = 0
        state.expression = min(5, max(0, expression))
        state.record("face", expression=state.expression)
        return Response("OK", content_type="text/plain")

    @app.get("/__lab__/state")
    def lab_state():
        return jsonify(state.snapshot())

    @app.post("/__lab__/reset")
    def lab_reset():
        state.reset()
        return jsonify(success=True)

    @app.post("/__lab__/fault")
    def lab_fault():
        data = request.get_json(force=True)
        try:
            state.faults.add(
                str(data.get("path", "")),
                fault_from_payload(data),
                times=int(data.get("times", 1)),
            )
        except (TypeError, ValueError) as exc:
            return jsonify(success=False, error=str(exc)), 400
        return jsonify(success=True, faults=state.faults.snapshot())

    @app.post("/__lab__/camera-transport")
    def lab_camera_transport():
        data = request.get_json(force=True)
        try:
            state.camera_transport.add(
                str(data.get("outcome", "normal")),
                times=int(data.get("times", 1)),
            )
        except (TypeError, ValueError) as exc:
            return jsonify(success=False, error=str(exc)), 400
        return jsonify(success=True, camera_transport=state.camera_transport.snapshot())

    @app.post("/__lab__/advance")
    def lab_advance():
        transition = state.begin_advance()
        action = transition["action"]
        if action == "fetch_audio":
            voice_url = str(transition["voice_url"])
            if state.fetch_loopback_audio and _loopback_url(voice_url):
                try:
                    response = requests.get(voice_url, timeout=2)
                    valid, reason = _audio_response_valid(response)
                    state.record(
                        "audio_fetched" if valid else "audio_fetch_failed",
                        status=response.status_code,
                        bytes=len(response.content),
                        sha256=hashlib.sha256(response.content).hexdigest(),
                        content_type=response.headers.get("Content-Type", ""),
                        reason=reason,
                    )
                except requests.RequestException as exc:
                    state.record("audio_fetch_failed", error=type(exc).__name__)
            else:
                state.record("audio_fetch_skipped", reason="non_loopback_or_disabled")
        elif action == "upload_camera":
            payload = transition["payload"]
            params = {
                "r": payload.get("requester", "user"),
                "t": payload.get("context", ""),
                "s": payload.get("speaker", "master"),
                "sl": payload.get("speaker_label", ""),
            }
            if state.camera_upload_v2:
                params["mode"] = payload.get("mode", "transient")
            image = _synthetic_jpeg()
            success = False
            final_status: int | None = None
            final_error = ""
            for attempt in range(1, 3):
                outcome = state.camera_transport.pop()
                if outcome == "partial_send":
                    state.record(
                        "camera_transport_attempt",
                        attempt=attempt,
                        outcome=outcome,
                        body_sent=False,
                        status=None,
                    )
                    final_error = "partial_send"
                    if attempt < 2:
                        continue
                    break
                try:
                    response = requests.post(
                        f"{state.callback_server_url.rstrip('/')}/vision/upload",
                        params=params,
                        data=image,
                        headers={"Content-Type": "image/jpeg"},
                        timeout=5,
                    )
                    if outcome == "response_lost_after_body":
                        # Camera.cpp maps a lost response after full body send
                        # to synthetic 202 and does not retry.
                        success = True
                        final_status = 202
                    else:
                        success = response.ok
                        final_status = response.status_code
                    if success:
                        final_error = ""
                    state.record(
                        "camera_transport_attempt",
                        attempt=attempt,
                        outcome=outcome,
                        body_sent=True,
                        upstream_status=response.status_code,
                        status=final_status,
                    )
                    if not success:
                        final_error = f"http_{response.status_code}"
                    break
                except requests.RequestException as exc:
                    final_error = type(exc).__name__
                    state.record(
                        "camera_transport_attempt",
                        attempt=attempt,
                        outcome=outcome,
                        body_sent=False,
                        status=None,
                        error=final_error,
                    )
                    if attempt >= 2:
                        break
            state.finish_camera_upload(success, final_status, final_error)
        return jsonify(success=True, transition=transition, state=state.snapshot())

    return app
