from __future__ import annotations

from pathlib import Path

import config


def test_mutable_json_configuration_is_copied_outside_repository():
    repo_examples = Path(__file__).resolve().parents[2] / "server" / "json_example"
    configured = Path(config.config.JSON_DIR).resolve()

    assert configured != repo_examples.resolve()
    assert not configured.is_relative_to(Path(__file__).resolve().parents[2])
    assert (configured / "member.json").is_file()
