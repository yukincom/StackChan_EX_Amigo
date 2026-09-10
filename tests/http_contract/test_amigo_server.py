from __future__ import annotations

import io
import queue

import pytest

import app as app_module
import ai_handler
import openai_compat_routes
from services.conversation_event_service import conversation_event_store


def test_firmware_multipart_stt_contract(monkeypatch):
    monkeypatch.setattr(app_module.config, "SPEECH_MIN_BYTES", 8)
    monkeypatch.setattr(openai_compat_routes.config, "SPEECH_MIN_BYTES", 8)
    seen = {}

    def fake_transcribe(audio, **kwargs):
        seen["bytes"] = len(audio)
        seen.update(kwargs)
        return "仮想音声です"

    monkeypatch.setattr(openai_compat_routes.speech_service, "transcribe", fake_transcribe)
    client = app_module.create_app({"TESTING": True}).test_client()
    response = client.post(
        "/v1/audio/transcriptions",
        data={
            "model": "whisper-1",
            "language": "ja",
            "file": (io.BytesIO(b"RIFF" + b"\x00" * 32), "speak.wav"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert response.get_json() == {"text": "仮想音声です"}
    assert seen["bytes"] == 36
    assert seen["language_code"] == "ja"


def test_short_audio_is_rejected_before_whisper(monkeypatch):
    monkeypatch.setattr(openai_compat_routes.config, "SPEECH_MIN_BYTES", 100)
    called = False

    def should_not_run(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(openai_compat_routes.speech_service, "transcribe", should_not_run)
    client = app_module.create_app({"TESTING": True}).test_client()
    response = client.post(
        "/v1/audio/transcriptions",
        data={"file": (io.BytesIO(b"short"), "speak.wav")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400
    assert called is False


def test_english_mode_selects_english_transcription(monkeypatch):
    monkeypatch.setattr(openai_compat_routes.config, "SPEECH_MIN_BYTES", 8)
    monkeypatch.setattr(ai_handler, "is_english_mode", lambda: True)
    seen = {}

    def fake_transcribe(audio, **kwargs):
        seen.update(kwargs)
        return "hello"

    monkeypatch.setattr(openai_compat_routes.speech_service, "transcribe", fake_transcribe)
    client = app_module.create_app({"TESTING": True}).test_client()
    response = client.post(
        "/v1/audio/transcriptions",
        data={"language": "ja", "file": (io.BytesIO(b"RIFF" + b"\x00" * 32), "speak.wav")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    assert response.get_json() == {"text": "hello"}
    assert seen == {"language_code": "en", "use_english_model": True}


def test_stt_no_speech_and_backend_error_are_distinct(monkeypatch):
    monkeypatch.setattr(openai_compat_routes.config, "SPEECH_MIN_BYTES", 8)
    client = app_module.create_app({"TESTING": True}).test_client()

    monkeypatch.setattr(openai_compat_routes.speech_service, "transcribe", lambda *args, **kwargs: None)
    no_speech = client.post(
        "/v1/audio/transcriptions",
        data={"file": (io.BytesIO(b"RIFF" + b"\x00" * 32), "speak.wav")},
        content_type="multipart/form-data",
    )
    assert no_speech.status_code == 400
    assert no_speech.get_json()["error"]["message"] == "No speech detected"

    def fail_backend(*args, **kwargs):
        raise TimeoutError("fixture timeout")

    monkeypatch.setattr(openai_compat_routes.speech_service, "transcribe", fail_backend)
    backend_error = client.post(
        "/v1/audio/transcriptions",
        data={"file": (io.BytesIO(b"RIFF" + b"\x00" * 32), "speak.wav")},
        content_type="multipart/form-data",
    )
    assert backend_error.status_code == 500
    assert backend_error.get_json()["error"]["type"] == "server_error"


def test_chat_response_and_camera_action_contract(monkeypatch):
    monkeypatch.setattr(
        openai_compat_routes,
        "resolve_speaker_context",
        lambda text: ("master", "[マスター]"),
    )
    monkeypatch.setattr(
        openai_compat_routes,
        "process_chat",
        lambda *args, **kwargs: {
            "success": True,
            "response": "[[VISION_TRANSIENT_TRIGGER]]",
            "camera_triggered": True,
            "camera_mode": "transient",
            "camera_requester": "user",
            "speaker": "master",
            "speaker_label": "[マスター]",
            "announce_endpoint": "vision_ai_trigger",
        },
    )
    client = app_module.create_app({"TESTING": True}).test_client()
    response = client.post(
        "/v1/chat/completions",
        json={"model": "lab", "messages": [{"role": "user", "content": "見て"}]},
    )
    payload = response.get_json()
    assert response.status_code == 200
    assert payload["choices"][0]["message"]["content"] == "[[VISION_TRANSIENT_TRIGGER]]"
    assert payload["stackchan_action"]["type"] == "camera_trigger"
    assert payload["stackchan_action"]["camera_mode"] == "transient"


def test_firmware_tts_mp3_contract(monkeypatch):
    monkeypatch.setattr(
        openai_compat_routes,
        "generate_voice_mixed",
        lambda *args, **kwargs: {"source_url": "http://127.0.0.1:5001/voice/lab"},
    )

    class FakeWav:
        content = b"RIFF-lab-wav"

    monkeypatch.setattr(openai_compat_routes.req, "get", lambda *args, **kwargs: FakeWav())
    monkeypatch.setattr(openai_compat_routes, "_wav_to_mp3", lambda data: b"ID3-lab-mp3")
    client = app_module.create_app({"TESTING": True}).test_client()
    response = client.post(
        "/v1/audio/speech",
        json={"input": "テスト", "model": "tts-1", "voice": "alloy", "response_format": "mp3"},
    )
    assert response.status_code == 200
    assert response.content_type == "audio/mpeg"
    assert response.data == b"ID3-lab-mp3"


def test_tts_upstream_failure_is_visible(monkeypatch):
    monkeypatch.setattr(openai_compat_routes, "generate_voice_mixed", lambda *args, **kwargs: None)
    client = app_module.create_app({"TESTING": True}).test_client()
    response = client.post(
        "/v1/audio/speech",
        json={"input": "テスト", "response_format": "mp3"},
    )
    assert response.status_code == 500
    assert response.get_json()["error"]["message"] == "TTS failed"


def test_chat_backend_failure_is_visible(monkeypatch):
    monkeypatch.setattr(openai_compat_routes, "resolve_speaker_context", lambda text: ("master", "[マスター]"))

    def fail_chat(*args, **kwargs):
        raise RuntimeError("fixture LLM failure")

    monkeypatch.setattr(openai_compat_routes, "process_chat", fail_chat)
    client = app_module.create_app({"TESTING": True}).test_client()
    response = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "こんにちは"}]},
    )
    assert response.status_code == 500
    assert response.get_json()["error"]["type"] == "server_error"


def _conversation_event(event_type, event_id, **extra):
    return {
        "schema_version": 1,
        "session_id": "device-lab-session",
        "event_id": event_id,
        "type": event_type,
        **extra,
    }


def test_continuous_conversation_device_round_trip_is_idempotent(monkeypatch):
    conversation_event_store.clear()
    calls = []
    monkeypatch.setattr(
        openai_compat_routes,
        "resolve_speaker_context",
        lambda text: ("master", "[マスター]"),
    )

    def fake_chat(text, **kwargs):
        calls.append((text, kwargs))
        return {"success": True, "response": "聞こえたよ", "error": None}

    monkeypatch.setattr(openai_compat_routes, "process_chat", fake_chat)
    client = app_module.create_app({"TESTING": True}).test_client()

    started = client.post(
        "/v1/conversation/events", json=_conversation_event("start", "event-start"),
    )
    assert started.status_code == 200
    assert started.get_json()["action"] == "listen"

    speech = _conversation_event("speech", "event-speech", text="こんにちは")
    first = client.post("/v1/conversation/events", json=speech)
    retried = client.post("/v1/conversation/events", json=speech)
    assert first.status_code == retried.status_code == 200
    assert first.get_json() == retried.get_json()
    assert first.get_json()["action"] == "reply"
    assert first.get_json()["reply"]["text"] == "聞こえたよ"
    assert first.get_json()["reason"] == "first_input"
    assert len(calls) == 1

    reply_id = first.get_json()["reply"]["id"]
    completed = client.post(
        "/v1/conversation/events",
        json=_conversation_event("playback_completed", "event-played", reply_id=reply_id),
    )
    assert completed.status_code == 200
    assert completed.get_json()["action"] == "listen"

    stopped = client.post(
        "/v1/conversation/events", json=_conversation_event("stop", "event-stop"),
    )
    assert stopped.status_code == 200
    assert stopped.get_json()["state"] == "closed"


def test_continuous_conversation_requires_start_and_matching_playback(monkeypatch):
    conversation_event_store.clear()
    client = app_module.create_app({"TESTING": True}).test_client()

    missing = client.post(
        "/v1/conversation/events",
        json=_conversation_event("speech", "event-before-start", text="もしもし"),
    )
    assert missing.status_code == 409
    assert missing.get_json()["error"]["message"] == "session_not_started"

    client.post("/v1/conversation/events", json=_conversation_event("start", "event-start"))
    monkeypatch.setattr(openai_compat_routes, "resolve_speaker_context", lambda text: ("master", ""))
    monkeypatch.setattr(
        openai_compat_routes,
        "process_chat",
        lambda *args, **kwargs: {"success": True, "response": "はい", "error": None},
    )
    reply = client.post(
        "/v1/conversation/events",
        json=_conversation_event("speech", "event-speech", text="聞こえる？"),
    )
    assert reply.status_code == 200

    mismatch = client.post(
        "/v1/conversation/events",
        json=_conversation_event("playback_completed", "event-played", reply_id="different"),
    )
    assert mismatch.status_code == 409


def test_continuous_conversation_validates_shape_and_size():
    conversation_event_store.clear()
    client = app_module.create_app({"TESTING": True}).test_client()

    invalid = client.post(
        "/v1/conversation/events",
        json={"schema_version": 1, "session_id": "s", "event_id": "e", "type": "speech"},
    )
    assert invalid.status_code == 400

    oversized = client.post(
        "/v1/conversation/events",
        data=b"{" + b" " * (8 * 1024) + b"}",
        content_type="application/json",
    )
    assert oversized.status_code == 413


def test_vision_queue_full_returns_429(monkeypatch):
    lab_queue = queue.Queue(maxsize=1)
    monkeypatch.setattr(app_module, "_VISION_QUEUE", lab_queue)
    monkeypatch.setattr(app_module, "_ensure_vision_worker", lambda: None)
    monkeypatch.setattr(app_module, "_VISION_JOB_SEQ", 0)
    client = app_module.create_app({"TESTING": True}).test_client()

    first = client.post("/vision/upload?t=first", data=b"J" * 600, content_type="image/jpeg")
    second = client.post("/vision/upload?t=second", data=b"J" * 600, content_type="image/jpeg")
    assert first.status_code == 200
    assert second.status_code == 429
    assert second.get_json()["error"] == "vision_busy"


@pytest.mark.xfail(
    strict=True,
    reason="Current firmware/server v1 upload contract omits capture mode; fix in a batched firmware campaign",
)
def test_vision_upload_v2_preserves_capture_mode(monkeypatch):
    lab_queue = queue.Queue(maxsize=1)
    monkeypatch.setattr(app_module, "_VISION_QUEUE", lab_queue)
    monkeypatch.setattr(app_module, "_ensure_vision_worker", lambda: None)
    client = app_module.create_app({"TESTING": True}).test_client()
    response = client.post(
        "/vision/upload?mode=archive",
        data=b"J" * 600,
        content_type="image/jpeg",
    )
    assert response.status_code == 200
    queued_item = lab_queue.get_nowait()
    assert queued_item[-1] == "archive"
