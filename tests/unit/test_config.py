from __future__ import annotations

import config as config_module


def test_invalid_numbers_are_reported_without_exposing_values(monkeypatch):
    config_module.CONFIG_LOAD_ERRORS.clear()
    monkeypatch.setenv("LAB_INVALID_INTEGER", "not-a-secret-but-do-not-echo")
    monkeypatch.setenv("LAB_INVALID_FLOAT", "also-invalid")

    assert config_module._int_env("LAB_INVALID_INTEGER", 7) == 7
    assert config_module._float_env("LAB_INVALID_FLOAT", 0.5) == 0.5
    joined = " ".join(config_module.CONFIG_LOAD_ERRORS)
    assert "LAB_INVALID_INTEGER: expected integer" in joined
    assert "LAB_INVALID_FLOAT: expected number" in joined
    assert "not-a-secret" not in joined
    assert "also-invalid" not in joined
