"""Synthetic model metadata and the exact rendered English scene prompt."""
import hashlib
import json
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace

import pytest

from services.conversation_lab import cli
from services.conversation_lab.context import TextContext, Utterance
from services.conversation_lab.model_identity import ModelIdentity, resolve_model_identity
from services.conversation_lab.reader import LocalSceneReader, PROMPT
from services.conversation_participation import State


@pytest.mark.parametrize("model,series", [
    ("mlx-community/Qwen3.6-35B-A3B-4bit-DWQ", "Qwen"),
    ("provider/Llama-3.3-70B", "Llama"),
    ("gpt-5", "gpt"),
    ("o3", "o"),
    ("provider/Gemma_3_8bit", "Gemma"),
    ("custom/household-model", "household"),
])
def test_family_is_extracted_from_model_prefix_without_a_vendor_list(model, series):
    assert ModelIdentity(model).series == series


def test_config_only_supplies_model_id_and_series_tracks_the_selected_model():
    config = SimpleNamespace(AI_CHAT_MODEL="custom/Lumen-42",
                             ASSISTANT_NAME="not-a-model-name", API_KEY="not-for-the-prompt")
    identity = ModelIdentity.from_config(config)
    assert identity.names == ["custom/Lumen-42", "Lumen"]
    assert resolve_model_identity(config_identity=identity) == identity
    assert resolve_model_identity(identity.model, config_identity=identity) == identity
    assert resolve_model_identity("provider/Nova4", config_identity=identity).series == "Nova"


@pytest.mark.parametrize("model", [None, "", "   "])
def test_invalid_model_identity_fails_before_inference(model):
    with pytest.raises(ValueError):
        ModelIdentity(model)


def test_actual_system_message_is_english_and_injects_model_names_not_aliases(monkeypatch):
    reader = LocalSceneReader("custom/Lumen-42")
    payload = TextContext().payload(Utterance("u1", "human", "ロボ、聞いて。"), State(), ["ロボ"])
    seen = []
    def request(messages, max_tokens=400):
        seen.extend(messages)
        return json.dumps({"addressee": "assistant", "called_assistant": True,
                           "reply_to_assistant": False, "human_exchange": False,
                           "evidence_ids": ["u1"]}), {}
    monkeypatch.setattr(reader, "_request", request)
    reader(payload)
    system = seen[0]["content"]
    assert system == reader.prompt and system.isascii()
    assert '{model_names_json}' not in system
    assert '["custom/Lumen-42", "Lumen"]' in system
    assert not any(name in system for name in ("Qwen", "GPT", "ChatGPT"))
    assert "an interaction with other people" in system
    assert "that account is not itself an ongoing human-to-human exchange" in system
    assert "unless it is included in assistant_names" in system
    assert json.loads(seen[1]["content"]) == payload
    assert payload["assistant_names"] == ["ロボ"]
    # Configured series is not an exclusion list: it can be explicitly registered.
    payload["assistant_names"] = ["Lumen"]
    reader(payload)
    assert json.loads(seen[-1]["content"])["assistant_names"] == ["Lumen"]


def test_model_names_are_json_escaped_without_formatting_the_output_schema():
    reader = LocalSceneReader('Family "A"\n{example}')
    assert json.dumps(reader.model_identity.names) in reader.prompt
    assert '"called_assistant":false' in reader.prompt
    assert "{model_names_json}" in PROMPT  # The compatibility export is a template.


@pytest.mark.parametrize("override", [None, "Nova4"])
def test_cli_report_hashes_rendered_prompt_and_loads_config_only_if_needed(tmp_path, monkeypatch, override):
    suite = {"schema_version": 1, "cases": [{"id": "one", "split": "dev",
        "source_group": "synthetic-config", "assistant_names": ["ロボ"], "turns": [
            {"id": "u1", "role": "human", "text": "ロボ、こんにちは。",
             "gold": {"action": "reply", "mode": "active"}}]}]}
    path = tmp_path / "suite.json"
    path.write_text(json.dumps(suite))
    seen = []
    def request(self, messages, max_tokens=400):
        seen.append((self.model, messages[0]["content"]))
        return json.dumps({"addressee": "assistant", "called_assistant": True,
                           "reply_to_assistant": False, "human_exchange": False,
                           "evidence_ids": ["u1"]}), {}
    monkeypatch.setattr(LocalSceneReader, "_request", request)
    identity = ModelIdentity("custom/Lumen-42")
    config_reads = []
    def load_config():
        config_reads.append(True)
        return identity
    args = ["--cases", str(path), "--backend", "local", "--output", str(tmp_path / "out")]
    if override:
        args.extend(["--model", override])
    assert cli.main(args, model_config_loader=load_config) == 0
    assert len(config_reads) == (0 if override else 1)
    expected = ModelIdentity(override) if override else identity
    report = json.loads((tmp_path / "out/report.json").read_text())
    assert report["model"] == seen[0][0] == expected.model
    assert report["model_series"] == expected.series
    assert report["prompt"] == seen[0][1]
    assert report["prompt_sha256"] == hashlib.sha256(seen[0][1].encode()).hexdigest()
    assert report["prompt_sha256"] != hashlib.sha256(PROMPT.encode()).hexdigest()


@pytest.mark.parametrize("needs_config", [False, True])
def test_launcher_does_not_read_config_until_local_model_is_needed(monkeypatch, needs_config):
    from services import conversation_text_lab
    launcher = Path(__file__).parents[2] / "tools/conversation_lab.py"
    if not launcher.exists():
        # Household tests use the common public launcher, not a second copy.
        pytest.skip("shared launcher is tested in the public checkout")
    class ConfigModule:
        @property
        def config(self):
            assert needs_config, "explicit model must not access application config"
            return SimpleNamespace(AI_CHAT_MODEL="Lumen-42")
    monkeypatch.setitem(sys.modules, "config", ConfigModule())
    seen = []
    def main(argv, *, model_config_loader=None):
        seen.append(model_config_loader() if needs_config else None)
        return 0
    monkeypatch.setattr(conversation_text_lab, "main", main)
    monkeypatch.setattr(sys, "argv", [str(launcher)])
    with pytest.raises(SystemExit) as result:
        runpy.run_path(str(launcher), run_name="__main__")
    assert result.value.code == 0
    assert seen == [ModelIdentity("Lumen-42") if needs_config else None]
