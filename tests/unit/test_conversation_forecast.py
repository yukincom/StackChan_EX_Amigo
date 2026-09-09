"""Synthetic forecast and STT-prompt checks; no model, network, audio, or device."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import threading

import pytest

from services import conversation_forecast as forecast
from services import speech_service as speech_module


def payload(prefix="続き"):
    return json.dumps({"patterns": [
        {"pattern_id": f"p{index}", "description": f"{prefix}{index}"}
        for index in range(1, 6)
    ]}, ensure_ascii=False)


def test_closed_forecast_contract_and_prompt_context():
    parsed = forecast.parse_forecast(payload())
    assert [item.pattern_id for item in parsed.patterns] == ["p1", "p2", "p3", "p4", "p5"]

    prompt = forecast.build_forecast_prompt(
        "今の話", "今の返事", previous_accepted=("前の話", "前の返事"),
    )
    inputs = json.loads(prompt.splitlines()[-1])
    assert inputs == {
        "previous_accepted": {"user": "前の話", "assistant": "前の返事"},
        "current": {"user": "今の話", "assistant": "今の返事"},
    }
    assert "返答、抑止、履歴変更、操作は決めません" in prompt


@pytest.mark.parametrize("bad", [
    "not json",
    {},
    {"patterns": []},
    {"patterns": [
        {"pattern_id": "p1", "description": "same"} for _ in range(5)
    ]},
    {"patterns": [
        {"pattern_id": f"p{i}", "description": "ok", "extra": True}
        for i in range(1, 6)
    ]},
    {"patterns": [
        {"pattern_id": f"p{i}", "description": "line\nbreak"}
        for i in range(1, 6)
    ]},
])
def test_forecast_rejects_prose_wrong_shape_duplicates_extras_and_multiline(bad):
    with pytest.raises((ValueError, json.JSONDecodeError)):
        forecast.parse_forecast(bad)


def test_runtime_keeps_only_short_lived_valid_semantic_hints(capsys):
    now = [10.0]
    seen = []

    def generate(prompt):
        seen.append(prompt)
        return payload("予測")

    runtime = forecast.ConversationForecastRuntime(
        generator=generate, clock=lambda: now[0], ttl_seconds=5,
    )
    assert runtime.update_now("最初", "返答一")
    assert "予測1" in " / ".join(item.description for item in runtime.current().patterns)
    assert runtime.update_now("次", "返答二")
    second_inputs = json.loads(seen[-1].splitlines()[-1])
    assert second_inputs["previous_accepted"] == {"user": "最初", "assistant": "返答一"}
    now[0] = 16.0
    assert runtime.current() is None

    secret = "private-utterance-fixture"
    broken = forecast.ConversationForecastRuntime(generator=lambda _: secret)
    assert not broken.update_now(secret, "reply")
    assert broken.current() is None
    assert secret not in capsys.readouterr().out


def test_runtime_rejects_nonpositive_ttl():
    with pytest.raises(ValueError, match="ttl"):
        forecast.ConversationForecastRuntime(ttl_seconds=0)


def test_replaced_background_result_cannot_overwrite_newer_forecast():
    started = threading.Event()
    release = threading.Event()

    def generate(prompt):
        inputs = json.loads(prompt.splitlines()[-1])
        if inputs["current"]["user"] == "old":
            started.set()
            assert release.wait(2)
            return payload("古い")
        return payload("新しい")

    runtime = forecast.ConversationForecastRuntime(generator=generate)
    runtime.submit("old", "reply-old")
    assert started.wait(2)
    runtime.submit("new", "reply-new")
    release.set()
    runtime._queue.join()  # bounded private queue; deterministic test synchronization.
    assert [p.description for p in runtime.current().patterns] == [
        "新しい1", "新しい2", "新しい3", "新しい4", "新しい5",
    ]


def test_japanese_stt_uses_hint_but_returns_observed_transcript_unchanged(monkeypatch):
    commands = []
    monkeypatch.setattr(speech_module, "WHISPER_CLI", "whisper-fixture")
    monkeypatch.setattr(speech_module.config, "WHISPER_MODEL", "ja-model")
    monkeypatch.setattr(speech_module.conversation_forecast, "current_stt_hint", lambda: "洞窟の続き / 道具を答える")

    def run(command, **kwargs):
        commands.append((command, kwargs))
        return SimpleNamespace(
            returncode=0,
            stdout="[00:00:00.000 --> 00:00:01.000] 聞こえた原文です。\n",
            stderr="",
        )

    monkeypatch.setattr(speech_module.subprocess, "run", run)
    transcript = speech_module.SpeechService().transcribe(b"RIFF" + b"\x00" * 64)
    assert transcript == "聞こえた原文です。"
    command = commands[0][0]
    prompt = command[command.index("--prompt") + 1]
    assert "洞窟の続き" in prompt
    assert "候補外の発話も聞こえた通り" in prompt


def test_english_stt_does_not_receive_japanese_forecast(monkeypatch):
    commands = []
    monkeypatch.setattr(speech_module, "WHISPER_CLI", "whisper-fixture")
    monkeypatch.setattr(speech_module.config, "WHISPER_MODEL_EN", "en-model")
    monkeypatch.setattr(speech_module.conversation_forecast, "current_stt_hint", lambda: "日本語の秘密候補")

    def run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(
            returncode=0,
            stdout="[00:00:00.000 --> 00:00:01.000] observed English transcript\n",
            stderr="",
        )

    monkeypatch.setattr(speech_module.subprocess, "run", run)
    result = speech_module.SpeechService().transcribe(
        b"RIFF" + b"\x00" * 64, language_code="en", use_english_model=True,
    )
    assert result == "observed English transcript"
    prompt = commands[0][commands[0].index("--prompt") + 1]
    assert prompt == "User talking to StackChan in English."
    assert "日本語の秘密候補" not in prompt


def test_forecast_module_has_no_memory_web_or_participation_policy_import():
    source = Path(forecast.__file__).read_text(encoding="utf-8")
    assert "memory_manager" not in source
    assert "firecrawl_search" not in source
    assert "conversation_participation" not in source


def test_selected_chat_reply_schedules_forecast_without_owning_reply(monkeypatch):
    from services import chat_service

    scheduled = []

    class Memory:
        def add_conversation(self, *args, **kwargs):
            return None

    monkeypatch.setattr(chat_service, "RobotMemory", Memory)
    monkeypatch.setattr(
        chat_service.conversation_forecast,
        "schedule_forecast",
        lambda user, reply: scheduled.append((user, reply)),
    )
    result = chat_service._build_voice_response(
        "返答です", "入力です", "master", "[マスター]", False, "[マスター]",
    )
    assert result == {"success": True, "response": "返答です", "error": None}
    assert scheduled == [(chat_service.mask_names("入力です"), chat_service.mask_names("返答です"))]


def test_forecast_scheduling_failure_never_fails_primary_reply(monkeypatch):
    from services import chat_service

    class Memory:
        def add_conversation(self, *args, **kwargs):
            return None

    monkeypatch.setattr(chat_service, "RobotMemory", Memory)
    monkeypatch.setattr(
        chat_service.conversation_forecast,
        "schedule_forecast",
        lambda *args: (_ for _ in ()).throw(RuntimeError("fixture")),
    )
    result = chat_service._build_voice_response(
        "返答です", "入力です", "master", None, False, "[マスター]",
    )
    assert result["success"] is True
    assert result["response"] == "返答です"
