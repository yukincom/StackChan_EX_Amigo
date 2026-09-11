from __future__ import annotations

import json

import pytest

from services import vision_handler


@pytest.mark.parametrize("mode,transcript,archived", [
    ("archive", "一時参照", True),
    ("archive", "見て", True),
    ("transient", "資料保存", False),
    ("transient", "見て", False),
    ("", "資料保存", False),
    ("unknown", "資料保存", False),
    (None, "資料保存", True),
    (None, "一時参照", False),
    (None, "見て", False),
])
def test_capture_mode_controls_archive_and_preserves_camera_conversation(
    monkeypatch, tmp_path, mode, transcript, archived,
):
    conversations = []
    pushed = []

    class Memory:
        def add_conversation(self, **kwargs):
            conversations.append(kwargs)

        def get_context(self, **kwargs):
            return ""

    image = b"synthetic-image"
    temporary = tmp_path / "temporary"
    archive = tmp_path / "archive"
    monkeypatch.setattr(vision_handler, "VISION_TMP_DIR", temporary)
    monkeypatch.setattr(vision_handler.config, "VISION_ARCHIVE_DIR", str(archive))
    monkeypatch.setattr(vision_handler.config, "VISION_ARCHIVE_KEYWORDS", ["資料保存"])
    monkeypatch.setattr(vision_handler.config, "VISION_TRANSIENT_KEYWORDS", ["一時参照"])
    monkeypatch.setattr(vision_handler, "RobotMemory", Memory)
    monkeypatch.setattr(vision_handler, "analyze_image", lambda *a: "赤い積み木")
    monkeypatch.setattr(vision_handler, "get_ai_response", lambda **kw: "赤い積み木だね。")
    monkeypatch.setattr(vision_handler, "generate_voice_mixed", lambda text: {
        "source_url": "http://127.0.0.1/fixture.wav",
    })
    monkeypatch.setattr(
        vision_handler,
        "push_to_m5stack",
        lambda url, **kwargs: pushed.append((url, kwargs)) or True,
    )

    result = vision_handler.handle_vision_upload(
        image,
        "user",
        transcript,
        capture_mode=mode,
    )

    assert result["success"] is True
    assert list(temporary.iterdir()) == []
    images = list(archive.glob("*.jpg"))
    metadata = list(archive.glob("*.json"))
    assert len(images) == len(metadata) == int(archived)
    if archived:
        assert images[0].read_bytes() == image
        assert json.loads(metadata[0].read_text())["transcript"] == transcript
    # Capture mode controls the image archive, not camera conversation history.
    assert any(item["ai_response"] == "赤い積み木だね。" for item in conversations)
    assert sum(item["speaker_label"] == "資料保存" for item in conversations) == int(archived)
    assert pushed == [("http://127.0.0.1/fixture.wav", {"fire_and_forget": False})]


def test_correlated_vision_push_carries_action_result(monkeypatch, tmp_path):
    pushed = []

    class Memory:
        def add_conversation(self, **kwargs):
            return None

        def get_context(self, **kwargs):
            return ""

    monkeypatch.setattr(vision_handler, "VISION_TMP_DIR", tmp_path / "temporary")
    monkeypatch.setattr(vision_handler, "RobotMemory", Memory)
    monkeypatch.setattr(vision_handler, "analyze_image", lambda *a: "机")
    monkeypatch.setattr(vision_handler, "get_ai_response", lambda **kw: "机が見えるよ。")
    monkeypatch.setattr(vision_handler, "generate_voice_mixed", lambda text: {
        "source_url": "http://127.0.0.1/fixture.wav",
    })
    monkeypatch.setattr(
        vision_handler,
        "push_to_m5stack",
        lambda url, **kwargs: pushed.append((url, kwargs)) or True,
    )

    result = vision_handler.handle_vision_upload(
        b"synthetic-image",
        "user",
        "見て",
        capture_mode="transient",
        session_id="camera-session",
        action_id="camera-action",
    )

    assert result["success"] is True
    assert pushed == [(
        "http://127.0.0.1/fixture.wav",
        {
            "fire_and_forget": False,
            "session_id": "camera-session",
            "action_id": "camera-action",
            "action_status": "succeeded",
        },
    )]
