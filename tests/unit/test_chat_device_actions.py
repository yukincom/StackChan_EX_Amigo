from __future__ import annotations

from services import chat_service


class _Memory:
    def add_conversation(self, *args, **kwargs):
        return None


def _prepare_song(monkeypatch):
    monkeypatch.setattr(chat_service, "RobotMemory", _Memory)
    monkeypatch.setattr(chat_service, "load_member_data", lambda: {})
    monkeypatch.setattr(chat_service.config, "SONG_TRIGGER", ["歌って"])
    monkeypatch.setattr(chat_service.config, "SONG_MAP", {"歌って": "fixture-song"})
    monkeypatch.setattr(chat_service.config, "VISION_ARCHIVE_KEYWORDS", [])
    monkeypatch.setattr(chat_service.config, "VISION_TRANSIENT_KEYWORDS", [])
    monkeypatch.setattr(chat_service.config, "VOICE_SERVER_URL", "http://127.0.0.1:5001")


def test_song_action_defers_push_and_uses_existing_mp3_proxy(monkeypatch):
    _prepare_song(monkeypatch)
    pushed = []
    monkeypatch.setattr(chat_service, "push_to_m5stack", pushed.append)

    result = chat_service.process_chat("歌って", defer_device_actions=True)

    assert result["success"] is True
    assert result["device_action"] == {
        "type": "audio_playback",
        "audio_url": chat_service.build_push_audio_url(
            "http://127.0.0.1:5001/song/fixture-song"
        ),
    }
    assert pushed == []


def test_legacy_song_request_still_pushes_immediately(monkeypatch):
    _prepare_song(monkeypatch)
    pushed = []
    monkeypatch.setattr(chat_service, "push_to_m5stack", pushed.append)

    result = chat_service.process_chat("歌って")

    assert result == {"success": True, "response": "うたうよ～！", "error": None}
    assert pushed == ["http://127.0.0.1:5001/song/fixture-song"]


def test_ai_camera_marker_only_triggers_camera(monkeypatch):
    monkeypatch.setattr(
        chat_service.config,
        "AI_VISION_ARCHIVE_MARKER",
        "[[VISION_ARCHIVE_TRIGGER]]",
    )
    monkeypatch.setattr(
        chat_service.config,
        "AI_VISION_TRANSIENT_MARKER",
        "[[VISION_TRANSIENT_TRIGGER]]",
    )

    cases = [
        ("[[VISION_TRANSIENT_TRIGGER]]", "transient"),
        ("$$VISION_TRANSIENT_TRIGGER$$", "transient"),
        (r"$$VISION\_TRANSIENT\_TRIGGER$$", "transient"),
        ("[[VISION_ARCHIVE_TRIGGER]]", "archive"),
    ]
    for response, expected_mode in cases:
        assert chat_service._extract_ai_camera_mode(response) == ("", expected_mode)


def test_ai_camera_marker_leak_is_removed_without_triggering_camera(monkeypatch):
    monkeypatch.setattr(
        chat_service.config,
        "AI_VISION_ARCHIVE_MARKER",
        "[[VISION_ARCHIVE_TRIGGER]]",
    )
    monkeypatch.setattr(
        chat_service.config,
        "AI_VISION_TRANSIENT_MARKER",
        "[[VISION_TRANSIENT_TRIGGER]]",
    )

    markers = [
        "[[VISION_TRANSIENT_TRIGGER]]",
        "$$VISION_TRANSIENT_TRIGGER$$",
        r"$$VISION\_TRANSIENT\_TRIGGER$$",
    ]
    for marker in markers:
        response = f"バナナケーキはしっとり焼けるよ。 {marker}"
        assert chat_service._extract_ai_camera_mode(response) == (
            "バナナケーキはしっとり焼けるよ。",
            None,
        )


def test_ambiguous_camera_markers_are_removed_without_trigger(monkeypatch):
    monkeypatch.setattr(
        chat_service.config,
        "AI_VISION_ARCHIVE_MARKER",
        "[[VISION_ARCHIVE_TRIGGER]]",
    )
    monkeypatch.setattr(
        chat_service.config,
        "AI_VISION_TRANSIENT_MARKER",
        "[[VISION_TRANSIENT_TRIGGER]]",
    )

    response = "[[VISION_TRANSIENT_TRIGGER]][[VISION_ARCHIVE_TRIGGER]]"
    assert chat_service._extract_ai_camera_mode(response) == ("", None)
