"""Scriptable local replacements for AI, STT, TTS, and voice-server HTTP APIs."""

from __future__ import annotations

import hashlib
import io
import time
import wave
from dataclasses import dataclass, field
from threading import RLock
from typing import Any

from flask import Flask, Response, jsonify, request

from .faults import FaultPlan, fault_from_payload


def silent_wav(duration_ms: int = 80, sample_rate: int = 16_000) -> bytes:
    frames = max(1, sample_rate * duration_ms // 1000)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(b"\x00\x00" * frames)
    return buffer.getvalue()


@dataclass
class FakeServicesState:
    transcript: str = "こんにちは"
    chat_text: str = "こんにちは。仮想環境で応答しています。"
    wav_bytes: bytes = field(default_factory=silent_wav)
    mp3_bytes: bytes = b"ID3\x04\x00\x00\x00\x00\x00\x0fSTACKCHAN-LAB"
    events: list[dict[str, Any]] = field(default_factory=list)
    faults: FaultPlan = field(default_factory=FaultPlan)
    _sequence: int = 0
    _lock: RLock = field(default_factory=RLock, repr=False)

    def record(self, event: str, **data: Any) -> dict[str, Any]:
        with self._lock:
            self._sequence += 1
            item = {"sequence": self._sequence, "event": event, **data}
            self.events.append(item)
            return item

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "events": list(self.events),
                "faults": self.faults.snapshot(),
                "transcript_configured": bool(self.transcript),
                "chat_configured": bool(self.chat_text),
                "wav_bytes": len(self.wav_bytes),
                "mp3_bytes": len(self.mp3_bytes),
            }


def create_fake_services_app(state: FakeServicesState | None = None) -> Flask:
    state = state or FakeServicesState()
    app = Flask("stackchan-fake-services")
    app.config["FAKE_SERVICES_STATE"] = state

    def fault_response():
        fault = state.faults.pop(request.path)
        if fault is None:
            return None
        if fault.delay_seconds:
            time.sleep(fault.delay_seconds)
        state.record("fault_injected", path=request.path, kind=fault.kind, status=fault.status)
        if fault.kind == "malformed_json":
            return Response("{", status=fault.status, content_type="application/json")
        if fault.kind == "empty":
            return Response(b"", status=fault.status, content_type=fault.content_type)
        body = fault.body or {"error": "simulated_fault", "kind": fault.kind}
        if isinstance(body, (dict, list)):
            return jsonify(body), fault.status
        return Response(str(body), status=fault.status, content_type=fault.content_type)

    @app.get("/version")
    def version():
        return Response('"stackchan-lab"', content_type="application/json")

    @app.post("/v1/audio/transcriptions")
    def transcriptions():
        fault = fault_response()
        if fault is not None:
            return fault
        uploaded = request.files.get("file")
        audio = uploaded.read() if uploaded else request.get_data(cache=False)
        state.record(
            "transcribe",
            bytes=len(audio),
            language=request.form.get("language", ""),
            model=request.form.get("model", ""),
        )
        if not state.transcript:
            return jsonify(error={"message": "No speech detected"}), 400
        return jsonify(text=state.transcript)

    @app.post("/v1/chat/completions")
    def chat():
        fault = fault_response()
        if fault is not None:
            return fault
        payload = request.get_json(silent=True) or {}
        messages = payload.get("messages", [])
        event = state.record("chat", messages=len(messages), functions=len(payload.get("functions", [])))
        return jsonify(
            id=f"chatcmpl-lab-{event['sequence']:04d}",
            object="chat.completion",
            created=0,
            model=payload.get("model", "stackchan-lab"),
            choices=[
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": state.chat_text},
                }
            ],
        )

    @app.post("/v1/audio/speech")
    def speech():
        fault = fault_response()
        if fault is not None:
            return fault
        payload = request.get_json(silent=True) or {}
        response_format = str(payload.get("response_format", "mp3")).lower()
        state.record("speech", input_chars=len(str(payload.get("input", ""))), format=response_format)
        if response_format in {"mp3", "mpeg"}:
            return Response(state.mp3_bytes, content_type="audio/mpeg")
        return Response(state.wav_bytes, content_type="audio/wav")

    def voice_result():
        fault = fault_response()
        if fault is not None:
            return fault
        payload = request.get_json(silent=True) or {}
        text = str(payload.get("text", ""))
        if not text:
            return jsonify(success=False, error="text is required"), 400
        voice_id = "lab-voice"
        state.record("voice_generate", chars=len(text), path=request.path)
        return jsonify(
            success=True,
            voice_id=voice_id,
            size=len(state.wav_bytes),
            sha256=hashlib.sha256(state.wav_bytes).hexdigest(),
            download_path=f"/voice/{voice_id}",
            settings={"engine": "stackchan-lab"},
        )

    app.add_url_rule("/generate", "generate", voice_result, methods=["POST"])
    app.add_url_rule("/generate_mixed", "generate_mixed", voice_result, methods=["POST"])

    @app.get("/voice/<voice_id>")
    def voice(voice_id: str):
        fault = fault_response()
        if fault is not None:
            return fault
        state.record("voice_fetch", voice_id=voice_id)
        return Response(state.wav_bytes, content_type="audio/wav")

    @app.get("/__lab__/state")
    def lab_state():
        return jsonify(state.snapshot())

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
        return jsonify(success=True)

    return app
