"""Structured reader contracts and invented contrast-suite wiring."""
import json
from pathlib import Path

import pytest

from services.conversation_lab.cases import load_cases
from services.conversation_lab.context import TextContext, Utterance
from services.conversation_lab.evaluation import replay_case, score_rows
from services.conversation_lab.structured_reader import StructuredSceneReader, parse_structure
from services.conversation_participation import State


def payload():
    context = TextContext()
    context.append(Utterance("a1", "assistant", "何色がいい？"))
    return context.payload(Utterance("u2", "human", "ロボ、青がいいな。"), State(), ["ロボ"])


def prediction(**changes):
    return {"addressee": "assistant", "name_use": "direct_call", "name_text": "ロボ",
            "reply_to_id": "a1", "human_exchange": False, "evidence_ids": ["a1", "u2"], **changes}


def test_direct_registered_call_and_reply_to_are_distinct():
    scene = parse_structure(prediction(), payload())
    assert scene.called_assistant and scene.reply_to_assistant
    scene = parse_structure(prediction(name_use="none", name_text=None), payload())
    assert not scene.called_assistant and scene.reply_to_assistant


@pytest.mark.parametrize("use", ["mention", "quoted_call"])
def test_mention_or_quote_can_be_reported_to_assistant_without_calling_it(use):
    scene = parse_structure(prediction(name_use=use), payload())
    assert not scene.called_assistant
    assert scene.addressee.value == "assistant"


def test_human_parent_is_not_an_assistant_reply():
    inputs = payload()
    inputs["history"][0]["role"] = "human"
    assert not parse_structure(prediction(), inputs).reply_to_assistant


@pytest.mark.parametrize("change", [
    {"name_use": "invented"}, {"name_use": []}, {"name_text": ""},
    {"name_use": "none"}, {"name_text": "not in current"},
    {"name_use": "other_call"}, {"addressee": "human"},
    {"reply_to_id": "future"}, {"reply_to_id": "u2"}, {"reply_to_id": []},
    {"evidence_ids": ["a1"]},
    {"human_exchange": True}, {"human_exchange": "false"},
    {"extra": "forbidden"},
])
def test_inconsistent_structure_is_an_error_not_successful_silence(change):
    with pytest.raises(ValueError):
        parse_structure(prediction(**change), payload())


def test_wrong_model_name_is_not_an_alias():
    inputs = payload()
    inputs["current"]["text"] = "GPT、青がいいな。"
    with pytest.raises(ValueError, match="registered"):
        parse_structure(prediction(name_text="GPT"), inputs)
    scene = parse_structure(prediction(name_text="GPT", name_use="other_call",
                                      addressee="unknown", reply_to_id=None), inputs)
    assert not scene.called_assistant and not scene.reply_to_assistant


def test_reader_retains_structure_for_review_with_same_request_budget(monkeypatch):
    reader = StructuredSceneReader("existing-local")
    def request(messages, max_tokens=400):
        assert max_tokens == 400
        assert json.loads(messages[1]["content"]) == payload()
        return json.dumps(prediction()), {"completion_tokens": 40}
    monkeypatch.setattr(reader, "_request", request)
    scene, info = reader(payload())
    assert scene.called_assistant
    assert info["structure"] == prediction()


def test_contrast_suite_oracle_and_final_segmentation_consistency():
    suite = load_cases(Path(__file__).parents[1] / "fixtures/conversation_structure_cases.json")
    assert len(suite["cases"]) == 18
    final = {}
    for case in suite["cases"]:
        run = replay_case(case, backend="oracle")
        assert run["metrics"]["whole_case_pass"], case["id"]
        row = run["rows"][-1]
        if case.get("segmentation") in {"split", "merged"}:
            final.setdefault(case["contrast_group"], []).append((row["action"], row["mode"]))
    assert all(len(rows) == 2 and rows[0] == rows[1] for rows in final.values())


def test_reply_to_is_validated_independently_of_redundant_evidence_list():
    assert parse_structure(prediction(evidence_ids=["u2"]), payload()).reply_to_assistant


def test_same_action_does_not_hide_wrong_structure():
    row = {"kind": "speech", "action": "reply", "mode": "active",
           "scene": {"reply_to_assistant": False},
           "structure": {"name_use": "mention", "reply_to_id": None},
           "gold": {"action": "reply", "scene": {"reply_to_assistant": True},
                    "structure": {"name_use": "quoted_call", "reply_to_id": "a1"}}}
    metrics = score_rows([row])
    assert metrics["whole_case_pass"]
    for group in ("scene_fields", "structure_fields"):
        assert all(s["correct"] == 0 and s["total"] == 1 for s in metrics[group].values())
    del row["structure"]["reply_to_id"]
    row["gold"]["structure"]["reply_to_id"] = None
    assert score_rows([row])["structure_fields"]["reply_to_id"] == {"correct": 0, "total": 1, "missing": 1}


def test_invalid_prediction_is_retained_for_local_review_but_not_scored(monkeypatch):
    reader = StructuredSceneReader("local")
    monkeypatch.setattr(reader, "_request", lambda *a, **k: (json.dumps(prediction(name_text="GPT")), {}))
    case = {"id": "invalid", "split": "dev", "assistant_names": ["ロボ"], "initial_mode": "quiet",
            "turns": [{"id": "u2", "role": "human", "text": "ロボ。",
                       "gold": {"action": "wait", "structure": {"name_use": "none"}}}]}
    run = replay_case(case, reader)
    assert run["rows"][0]["rejected_prediction"]["name_text"] == "GPT"
    assert run["metrics"]["errors"] == 1
    assert not run["metrics"]["whole_case_pass"]
    assert run["metrics"]["structure_fields"]["name_use"]["correct"] == 0


def test_new_silent_categories_are_counted_as_interruption_opportunities():
    row = {"id": "u", "kind": "speech", "action": "reply", "mode": "active",
           "gold": {"action": "wait", "category": "quoted_call"}}
    assert score_rows([row])["false_interruptions"] == {"count": 1, "opportunities": 1}


def test_cli_candidate_is_opt_in_and_reports_selected_prompt(tmp_path, monkeypatch):
    from services.conversation_lab import cli
    cases = Path(__file__).parents[1] / "fixtures/conversation_structure_cases.json"
    class FakeReader:
        prompt = cli.STRUCTURED_PROMPT
        def __init__(self, *args, **kwargs):
            pass
        def __call__(self, inputs):
            data = prediction(name_use="none", name_text=None, reply_to_id=None,
                              addressee="unknown", evidence_ids=[])
            return parse_structure(data, inputs), {}
    monkeypatch.setattr(cli, "StructuredSceneReader", FakeReader)
    cli.main(["--cases", str(cases), "--backend", "local", "--reader", "structured",
              "--model", "local", "--output", str(tmp_path)])
    report = json.loads((tmp_path / "report.json").read_text())
    import hashlib
    assert report["reader"] == "structured"
    assert report["prompt_sha256"] == hashlib.sha256(cli.STRUCTURED_PROMPT.encode()).hexdigest()
    with pytest.raises(SystemExit):
        cli.main(["--cases", str(cases), "--reader", "structured"])
