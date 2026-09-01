from __future__ import annotations

from services import vision_handler


def test_vlm_failure_pushes_fallback_records_failure_and_deletes_image(tmp_path, monkeypatch):
    conversations = []
    pushed = []

    class FakeMemory:
        def add_conversation(self, **kwargs):
            conversations.append(kwargs)

    monkeypatch.setattr(vision_handler, "VISION_TMP_DIR", tmp_path)
    monkeypatch.setattr(vision_handler, "RobotMemory", FakeMemory)
    monkeypatch.setattr(vision_handler, "analyze_image", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        vision_handler,
        "generate_voice_mixed",
        lambda text: {"source_url": "http://127.0.0.1/voice/fallback"},
    )
    monkeypatch.setattr(
        vision_handler,
        "push_to_m5stack",
        lambda url, fire_and_forget=False: pushed.append((url, fire_and_forget)) or True,
    )

    result = vision_handler.handle_vision_upload(
        b"fixture-jpeg" * 100,
        requester="user",
        transcript="見て",
        speaker="master",
    )

    assert result == {
        "success": False,
        "error": "VLM analysis failed",
        "user_message": vision_handler.VISION_USER_ERROR_MESSAGE,
    }
    assert pushed == [("http://127.0.0.1/voice/fallback", False)]
    assert any("vision失敗" in item["ai_response"] for item in conversations)
    assert list(tmp_path.glob("*.jpg")) == []
