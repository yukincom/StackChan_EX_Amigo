"""Conversation transport checks without models, network, audio, or hardware."""

from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from services.conversation_event_service import (
    ConversationEventStore,
    EventConflictError,
    EventContractError,
    materialize_device_action,
)


def event(event_type, event_id, *, version=1, session_id="device-lab-session", **extra):
    return {
        "schema_version": version,
        "session_id": session_id,
        "event_id": event_id,
        "type": event_type,
        **extra,
    }


def reply(text):
    return {"success": True, "response": f"聞こえたよ: {text}", "error": None}


def assert_conflict(code, call):
    with pytest.raises(EventConflictError) as raised:
        call()
    assert raised.value.code == code


def test_v1_round_trip_and_retry_remain_compatible():
    store = ConversationEventStore()
    calls = []

    def counted_reply(text):
        calls.append(text)
        return {"success": True, "response": "聞こえたよ", "error": None}

    started = store.handle(event("start", "start"), counted_reply)
    assert started["schema_version"] == 1
    assert "device_action" not in started

    speech = event("speech", "speech", text="こんにちは")
    first = store.handle(speech, counted_reply)
    assert store.handle(speech, counted_reply) == first
    assert first["action"] == "reply"
    assert first["reply"]["text"] == "聞こえたよ"
    assert first["reason"] == "first_input"
    assert calls == ["こんにちは"]

    reply_id = first["reply"]["id"]
    completed = store.handle(
        event("playback_completed", "played", reply_id=reply_id),
        counted_reply,
    )
    assert completed["action"] == "listen"
    assert completed["reason"] == "playback_completed"
    assert store.handle(event("stop", "stop"), counted_reply)["state"] == "closed"


def test_v2_reply_requires_completion_status_and_exposes_null_action():
    store = ConversationEventStore()
    started = store.handle(event("start", "start", version=2), reply)
    assert started["device_action"] is None

    response = store.handle(
        event("speech", "speech", version=2, text="こんにちは"),
        reply,
    )
    assert response["action"] == "reply"
    assert response["device_action"] is None

    with pytest.raises(EventContractError, match="unexpected or missing"):
        store.handle(
            event(
                "playback_completed",
                "played-missing-status",
                version=2,
                reply_id=response["reply"]["id"],
            ),
            reply,
        )

    completed = store.handle(
        event(
            "playback_completed",
            "played",
            version=2,
            reply_id=response["reply"]["id"],
            status="failed",
        ),
        reply,
    )
    assert completed["state"] == "active"
    assert completed["action"] == "listen"
    assert completed["reason"] == "playback_failed"


def test_v2_device_action_blocks_speech_until_matching_completion():
    store = ConversationEventStore()
    store.handle(event("start", "start", version=2), reply)

    def camera(_text):
        return {
            "success": True,
            "response": "internal marker is not spoken",
            "device_action": {
                "type": "camera_capture",
                "camera_mode": "transient",
                "camera_requester": "user",
                "transcript": "見て",
                "speaker": "master",
                "speaker_label": "",
                "announce_endpoint": "vision_ai_trigger",
            },
        }

    response = store.handle(
        event("speech", "camera", version=2, text="見て"),
        camera,
    )
    action = response["device_action"]
    assert response["action"] == "perform"
    assert response["reply"] is None
    assert action["type"] == "camera_capture"
    assert len(action["action_id"]) <= 64

    assert_conflict(
        "device_action_pending",
        lambda: store.handle(event("speech", "too-soon", version=2, text="次"), reply),
    )
    assert_conflict(
        "action_id_mismatch",
        lambda: store.handle(
            event(
                "action_completed",
                "wrong",
                version=2,
                action_id="different",
                status="succeeded",
            ),
            reply,
        ),
    )

    completed = store.handle(
        event(
            "action_completed",
            "done",
            version=2,
            action_id=action["action_id"],
            status="failed",
        ),
        reply,
    )
    assert completed["state"] == "active"
    assert completed["action"] == "listen"
    assert completed["reason"] == "action_failed"


def test_conflicts_have_stable_codes():
    store = ConversationEventStore()
    assert_conflict(
        "session_not_started",
        lambda: store.handle(event("speech", "early", text="もしもし"), reply),
    )
    store.handle(event("start", "start"), reply)
    store.handle(event("speech", "speech", text="聞こえる？"), reply)
    assert_conflict(
        "reply_playback_pending",
        lambda: store.handle(event("speech", "next", text="次"), reply),
    )
    assert_conflict(
        "event_id_reused",
        lambda: store.handle(event("start", "speech"), reply),
    )
    store.handle(event("stop", "stop"), reply)
    assert_conflict(
        "session_closed",
        lambda: store.handle(event("face_touch", "touch"), reply),
    )


@pytest.mark.parametrize("replacement", ["stop", "start"])
def test_stop_and_restart_supersede_slow_speech_without_waiting(replacement):
    store = ConversationEventStore()
    store.handle(event("start", "initial"), reply)
    entered = threading.Event()
    release = threading.Event()

    def slow_reply(text):
        entered.set()
        assert release.wait(2)
        return {"success": True, "response": text, "error": None}

    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(
            store.handle,
            event("speech", "slow", text="考えて"),
            slow_reply,
        )
        assert entered.wait(1)
        assert_conflict(
            "event_processing",
            lambda: store.handle(event("speech", "slow", text="考えて"), reply),
        )
        assert_conflict(
            "speech_processing",
            lambda: store.handle(event("speech", "other", text="別件"), reply),
        )

        control = store.handle(event(replacement, "control"), reply)
        assert control["action"] == ("stopped" if replacement == "stop" else "listen")
        release.set()
        expected = "session_closed" if replacement == "stop" else "session_not_started"
        with pytest.raises(EventConflictError) as raised:
            pending.result(timeout=1)
        assert raised.value.code == expected


def test_touch_retry_is_cached_and_action_session_closes_after_completion():
    store = ConversationEventStore()
    request_payload = {
        "model": "lab",
        "messages": [{"role": "user", "content": "歌って"}],
        "stackchan_device": {
            "action_schema_version": 2,
            "session_id": "touch-session",
            "event_id": "touch-event",
        },
    }
    reservation, cached = store.reserve_touch_request(
        request_payload["stackchan_device"],
        request_payload,
    )
    assert reservation is not None and cached is None
    assert_conflict(
        "event_processing",
        lambda: store.reserve_touch_request(
            request_payload["stackchan_device"],
            request_payload,
        ),
    )

    action = materialize_device_action({
        "type": "audio_playback",
        "audio_url": "http://127.0.0.1:5050/audio/proxy.mp3?src=fixture",
    })
    response = {"id": "fixed-response", "stackchan_device": {"device_action": action}}
    assert store.complete_touch_request(
        reservation,
        response,
        device_action=action,
    ) == response

    retry_reservation, retry = store.reserve_touch_request(
        request_payload["stackchan_device"],
        request_payload,
    )
    assert retry_reservation is None
    assert retry == response
    store.validate_pending_action("touch-session", action["action_id"], action_type="audio_playback")

    completed = store.handle(
        event(
            "action_completed",
            "touch-done",
            version=2,
            session_id="touch-session",
            action_id=action["action_id"],
            status="succeeded",
        ),
        reply,
    )
    assert completed["state"] == "closed"
    assert completed["action"] == "stopped"


def test_device_action_wire_values_are_strictly_bounded():
    with pytest.raises(EventContractError, match="announce_endpoint"):
        materialize_device_action({
            "type": "camera_capture",
            "camera_mode": "transient",
            "camera_requester": "user",
            "transcript": "見て",
            "speaker": "master",
            "speaker_label": "",
            "announce_endpoint": "../unsafe",
        })
    with pytest.raises(EventContractError, match="audio_url"):
        materialize_device_action({
            "type": "audio_playback",
            "audio_url": "http://user:secret@127.0.0.1/song/fixture",
        })
