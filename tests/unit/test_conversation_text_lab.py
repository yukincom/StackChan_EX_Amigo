"""Synthetic fixtures only; the real Live corpus belongs in the private repo."""
import copy
import json

import pytest

from services.conversation_participation import Addressee, Mode, Scene, Source, State
from services.conversation_text_lab import (
    LocalSceneReader, TextContext, Utterance, load_cases, parse_scene,
    replay_case, save_report, score_rows,
)


def label(target="human", exchange=False, called=False, reply=False):
    return dict(addressee=target, human_exchange=exchange,
                called_assistant=called, reply_to_assistant=reply)


def case():
    return {"id": "synthetic", "split": "dev", "source_group": "invented-1",
            "assistant_names": ["ロボ"], "assistant_persona": "親切なロボット。一人称はぼく。", "turns": [
        {"id": "1", "role": "human", "text": "ロボ、ゲームを教えて。",
         "gold": {"action": "reply", "mode": "active", "category": "direct_call", "scene": label("assistant", called=True)}},
        {"id": "2", "role": "assistant", "text": "どのゲームがいい？"},
        {"id": "3", "role": "human", "text": "二人で遊べるものがいいな。",
         "gold": {"action": "reply", "mode": "active", "category": "assistant_question", "scene": label("assistant", reply=True)}},
        {"id": "4", "role": "human", "text": "そこの駒、取ってくれる？",
         "gold": {"action": "wait", "mode": "active", "category": "human_exchange", "scene": label()}},
        {"id": "5", "role": "human", "text": "はい、どうぞ。ありがとう。",
         "gold": {"action": "wait", "mode": "quiet", "category": "human_exchange", "scene": label(exchange=True)}},
        {"id": "gap", "kind": "turn_boundary", "gold": {"action": "announce_quiet", "mode": "quiet", "category": "announcement"}},
        {"id": "6", "role": "human", "text": "ロボって丸い顔だね。",
         "gold": {"action": "wait", "mode": "quiet", "category": "name_mention", "scene": label(exchange=True)}},
        {"id": "touch", "kind": "face_touch", "gold": {"action": "listen", "mode": "active", "category": "face_touch"}},
    ]}


def test_oracle_tests_policy_not_semantic_accuracy():
    run = replay_case(case(), backend="oracle")
    assert run["metrics"]["whole_case_pass"]
    assert run["rows"][1]["action"] == "context_only"
    assert run["rows"][4]["allow_conversation_history"] is False
    assert run["rows"][6]["context_ids"] == ["1", "2", "3", "4", "5"]


@pytest.mark.parametrize("backend", ["always_silent", "always_reply"])
def test_degenerate_baselines_do_not_pass(backend):
    assert not replay_case(case(), backend=backend)["metrics"]["whole_case_pass"]


def test_baseline_metrics_separate_interruptions_and_missed_replies():
    assert replay_case(case(), backend="always_silent")["metrics"]["missed_replies"]["count"] == 2
    assert replay_case(case(), backend="always_reply")["metrics"]["false_interruptions"]["count"] == 3


def test_reader_has_no_gold_future_source_or_inferred_speaker():
    fixture = case()
    fixture["turns"][0].update(source={"private": "never"}, speaker_guess="parent")
    seen = []
    def reader(payload):
        seen.append(copy.deepcopy(payload))
        return Scene(Source.HUMAN, Addressee.HUMAN), {}
    replay_case(fixture, reader, history_utterances=1)
    first = seen[0]
    assert set(first) == {"state", "assistant_names", "history", "current"}
    assert first["history"] == []
    assert set(first["current"]) == {"id", "role", "text"}
    assert len(seen[-1]["history"]) == 1
    assert seen[-1]["history"][0]["id"] == "5"


def test_no_ordinary_memory_device_or_app_imports():
    import ast
    from pathlib import Path
    from services import conversation_text_lab, conversation_lab
    paths = [Path(conversation_text_lab.__file__), *Path(conversation_lab.__file__).parent.glob("*.py")]
    forbidden = {"config", "app", "memory_manager", "voice_service", "chat_service"}
    for path in paths:
        tree = ast.parse(path.read_text())
        names = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                names.append(node.module or "")
            elif isinstance(node, ast.Import):
                names.extend(alias.name for alias in node.names)
        assert not any(set(name.split(".")) & forbidden for name in names), path.name


def test_context_caps_keep_whole_recent_utterances_and_current():
    context = TextContext(2, 9)
    for n in range(4):
        context.append(Utterance(str(n), "human", "abc"))
    payload = context.payload(Utterance("now", "human", "abcde"), State(), ["R"])
    assert [u["id"] for u in payload["history"]] == ["3"]
    assert payload["current"]["text"] == "abcde"
    with pytest.raises(ValueError):
        context.payload(Utterance("long", "human", "x" * 10), State(), ["R"])
    zero = TextContext(0)
    zero.append(Utterance("x", "human", "hi"))
    assert zero.payload(Utterance("y", "human", "now"), State(), ["R"])["history"] == []


def test_mic_off_clears_buffer_and_skips_model_even_for_face_touch():
    fixture = case()
    fixture["turns"] = [fixture["turns"][0], {"id": "off", "kind": "mic_off"},
                        {"id": "touch", "kind": "face_touch"},
                        {"id": "private", "role": "human", "text": "discard this"},
                        {"id": "on", "kind": "mic_on"},
                        {"id": "new", "role": "human", "text": "new start"}]
    payloads = []
    def reader(payload):
        payloads.append(payload)
        return Scene(Source.HUMAN), {}
    run = replay_case(fixture, reader)
    assert len(payloads) == 2
    assert payloads[-1]["history"] == []
    assert run["rows"][3]["mode"] == "mic_off"


def test_unknown_source_does_not_invoke_model_or_wake():
    fixture = case()
    fixture.update(initial_mode="quiet")
    fixture["turns"] = [{"id": "u", "role": "unknown", "text": "ロボ、返事して"}]
    def forbidden(_):
        raise AssertionError("must not be called")
    run = replay_case(fixture, forbidden)
    assert run["rows"][0]["mode"] == "quiet"
    assert run["rows"][0]["action"] == "wait"
    assert run["rows"][0]["error"] is None


def test_model_errors_are_not_successful_silence():
    fixture = case()
    fixture["turns"] = [fixture["turns"][3]]
    def broken(_):
        raise ValueError("do not log private response")
    metrics = replay_case(fixture, broken)["metrics"]
    assert metrics["correct"] == 0
    assert metrics["errors"] == 1
    assert not metrics["whole_case_pass"]


@pytest.mark.parametrize("change", [
    {"called_assistant": "false"}, {"addressee": "nobody"},
    {"evidence_ids": ["future"]}, {"called_assistant": True},
    {"additional_action": "reply"},
])
def test_scene_rejects_bad_types_contradictions_extra_keys_and_future_evidence(change):
    with pytest.raises(ValueError):
        parse_scene({**label(), "evidence_ids": [], **change}, {"1"})


@pytest.mark.parametrize("url", ["https://example.org/v1/chat/completions", "http://localhost:8080/v1/chat/completions",
                               "http://127.0.0.1:8080/other", "http://user@127.0.0.1:8080/v1/chat/completions"])
def test_local_reader_refuses_nonlocal_or_ambiguous_routes(url):
    with pytest.raises(ValueError):
        LocalSceneReader("existing", url)


def test_split_leakage_rejected(tmp_path):
    first, second = case(), case()
    second.update(id="same-conversation-another-excerpt", split="holdout")
    path = tmp_path / "cases.json"
    path.write_text(json.dumps({"schema_version": 1, "cases": [first, second]}))
    with pytest.raises(ValueError, match="both splits"):
        load_cases(path)


def test_private_html_escapes_input_and_preserves_previous_evidence(tmp_path):
    fixture = case()
    fixture["turns"][0]["text"] = "<script>alert(1)</script>"
    report = {"runs": [replay_case(fixture, backend="oracle")]}
    save_report(tmp_path, report)
    content = (tmp_path / "report.html").read_text()
    assert "<script>" not in content
    assert "&lt;script&gt;" in content
    with pytest.raises(ValueError, match="already exists"):
        save_report(tmp_path, report)


def test_empty_scores_are_not_success():
    assert not score_rows([])["whole_case_pass"]


def test_wrong_mode_is_failure_in_category_and_html(tmp_path):
    fixture = case()
    run = replay_case(fixture, backend="oracle")
    run["rows"][4]["mode"] = "active"
    run["metrics"] = score_rows(run["rows"])
    assert run["metrics"]["by_category"]["human_exchange"] == {"correct": 1, "total": 2}
    save_report(tmp_path, {"runs": [run]})
    assert "<tr class='bad'><td>5</td>" in (tmp_path / "report.html").read_text()


def test_mode_only_gold_is_not_silently_ignored(tmp_path):
    fixture = case()
    fixture["turns"][0]["gold"] = {"mode": "quiet"}
    path = tmp_path / "suite.json"
    path.write_text(json.dumps({"schema_version": 1, "cases": [fixture]}))
    with pytest.raises(ValueError, match="require an action"):
        load_cases(path)


def test_playback_flag_and_side_effect_expectations_are_exercised():
    fixture = case()
    fixture["turns"] = [{"id": "touch", "kind": "face_touch", "playback_active": True,
                         "gold": {"action": "listen", "mode": "active", "stop_playback": True,
                                  "allow_conversation_history": False}}]
    run = replay_case(fixture, backend="oracle")
    assert run["metrics"]["whole_case_pass"]
    run["rows"][0]["stop_playback"] = False
    assert not score_rows(run["rows"])["whole_case_pass"]


@pytest.mark.parametrize("collision,widths", [(True, "16"), (False, "16,-1")])
def test_cli_validates_all_outputs_and_widths_before_model(tmp_path, monkeypatch, collision, widths):
    from services.conversation_lab import cli as lab
    target = tmp_path / "report"
    target.mkdir()
    if collision:
        (target / "report.json").write_text("existing")
    def forbidden(*args, **kwargs):
        raise AssertionError("must validate before constructing a model reader")
    monkeypatch.setattr(lab, "LocalSceneReader", forbidden)
    with pytest.raises(ValueError):
        lab.main(["--cases", str(tmp_path / "not-even-loaded.json"), "--backend", "local",
                  "--model", "existing", "--history", widths, "--output", str(target)])


def test_manual_input_needs_no_device_and_does_not_keep_mic_off_text(monkeypatch, capsys):
    from services.conversation_text_lab import interactive
    lines = iter(["hi", "/off", "discard me", "/touch", "/on", "new", "/quit"])
    monkeypatch.setattr("builtins.input", lambda _: next(lines))
    seen = []
    def reader(payload):
        seen.append(payload)
        return Scene(Source.HUMAN, Addressee.ASSISTANT), {}
    interactive(reader, aliases=["ロボ"])
    assert len(seen) == 2
    assert seen[-1]["history"] == []
    assert '"action": "reply"' in capsys.readouterr().out


def test_reply_demo_uses_same_wording_only_when_admitted_and_never_sees_gold():
    from services.conversation_text_lab import reply_demos
    fixture = case()
    run = replay_case(fixture, backend="oracle")
    class Reader:
        def respond(self, payload):
            assert all(set(t) == {"id", "role", "text"} for t in payload["history"] + [payload["current"]])
            assert payload["assistant_names"] == fixture["assistant_names"]
            assert payload["assistant_persona"] == fixture["assistant_persona"]
            return "実際に生成した比較文", {"completion_tokens": 5}
    demos = reply_demos(fixture, run, Reader(), {"1", "5"})
    assert demos[0]["before_ungated"] == demos[0]["after_gated"]
    assert demos[1]["before_ungated"] and demos[1]["after_gated"] == ""


def test_long_quiet_context_eviction_does_not_reset_participation():
    fixture = case()
    first = fixture["turns"][4]
    fixture["turns"] = [first] + [
        {"id": f"long-{i}", "role": "human", "text": f"この駒を{i}番に置こう。",
         "gold": {"action": "wait", "mode": "quiet", "scene": label(exchange=True)}}
        for i in range(40)] + [fixture["turns"][0]]
    run = replay_case(fixture, backend="oracle", history_utterances=4)
    assert run["metrics"]["whole_case_pass"]
    assert len(run["rows"][-1]["context_ids"]) == 4
    assert run["rows"][-1]["mode"] == "active"


def test_scene_and_demo_share_explicit_identity_not_model_name(monkeypatch):
    fixture = case()
    fixture["assistant_names"] = ["サクラ", "さくら"]
    reader = LocalSceneReader("Qwen")
    messages_seen = []
    def request(messages, max_tokens=400):
        messages_seen.append(messages)
        if max_tokens == 400:
            return json.dumps({**label("assistant", called=True), "evidence_ids": []}), {}
        return "ぼくはサクラだよ。", {}
    monkeypatch.setattr(reader, "_request", request)
    run = replay_case(fixture, reader)
    from services.conversation_text_lab import reply_demos
    reply_demos(fixture, run, reader, {"1"})
    assert json.loads(messages_seen[0][1]["content"])["assistant_names"] == ["サクラ", "さくら"]
    prompt = messages_seen[-1][0]["content"]
    assert "あなたの名前は「サクラ」" in prompt
    assert fixture["assistant_persona"] in prompt
    assert "あなたの名前は「Qwen」" not in prompt


@pytest.mark.parametrize("profile", [{}, {"assistant_names": ["ロボ"]},
                                     {"assistant_names": [], "assistant_persona": "親切"}])
def test_reply_demo_refuses_unconfigured_character_before_network(profile, monkeypatch):
    reader = LocalSceneReader("Qwen")
    def forbidden(*args, **kwargs):
        raise AssertionError("must not infer without a character")
    monkeypatch.setattr(reader, "_request", forbidden)
    with pytest.raises(ValueError):
        reader.respond({**profile, "history": [], "current": {"role": "human", "text": "GPT、返事して"}})


@pytest.mark.parametrize("args", [["--interactive"], ["--cases", "unused.json", "--name", "ロボ"]])
def test_cli_does_not_silently_default_or_ignore_character(args):
    from services.conversation_text_lab import main
    with pytest.raises(SystemExit):
        main(args)
