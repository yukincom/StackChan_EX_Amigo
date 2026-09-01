"""Host-side client that emits the same payload shapes as the current firmware."""

from __future__ import annotations

from typing import Any

import requests


class FirmwareClient:
    def __init__(self, server_url: str, timeout: float = 5.0) -> None:
        self.server_url = server_url.rstrip("/")
        self.timeout = timeout

    def transcribe(self, audio: bytes, language: str = "ja") -> requests.Response:
        return requests.post(
            f"{self.server_url}/v1/audio/transcriptions",
            data={"model": "whisper-1", "language": language},
            files={"file": ("speak.wav", audio, "application/octet-stream")},
            timeout=self.timeout,
        )

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str = "stackchan-chat",
        functions: list[dict[str, Any]] | None = None,
    ) -> requests.Response:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            # These fields are always present in firmware's json_ChatString.
            "functions": functions or [],
            "function_call": "auto",
        }
        return requests.post(
            f"{self.server_url}/v1/chat/completions",
            json=payload,
            timeout=self.timeout,
        )

    def speech(
        self,
        text: str,
        *,
        response_format: str = "mp3",
        model: str = "tts-1",
        voice: str = "alloy",
    ) -> requests.Response:
        return requests.post(
            f"{self.server_url}/v1/audio/speech",
            json={
                "input": text,
                "model": model,
                "voice": voice,
                "speed": 1,
                "response_format": response_format,
            },
            timeout=self.timeout,
        )

    def upload_vision(
        self,
        image: bytes,
        *,
        requester: str = "user",
        transcript: str = "",
        speaker: str = "master",
        speaker_label: str = "",
        mode: str | None = None,
    ) -> requests.Response:
        params = {
            "r": requester,
            "t": transcript,
            "s": speaker,
            "sl": speaker_label,
        }
        # mode=None reproduces the current firmware contract. Supplying mode is
        # the proposed v2 contract and lets tests expose the current drift.
        if mode is not None:
            params["mode"] = mode
        return requests.post(
            f"{self.server_url}/vision/upload",
            params=params,
            data=image,
            headers={"Content-Type": "image/jpeg"},
            timeout=self.timeout,
        )
