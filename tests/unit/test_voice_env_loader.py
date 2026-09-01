from __future__ import annotations

import os

from voice_server.env_loader import load_voice_environment


def test_voice_dotenv_is_skipped_in_no_device_mode(tmp_path, monkeypatch):
    env_dir = tmp_path / "env"
    env_dir.mkdir()
    (env_dir / ".env").write_text(
        "STACKCHAN_VOICE_ENV_SENTINEL=must-not-load\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("STACKCHAN_VOICE_ENV_SENTINEL", raising=False)
    monkeypatch.setenv("STACKCHAN_ENV_DIR", str(env_dir))
    monkeypatch.setenv("STACKCHAN_SKIP_DOTENV", "1")

    load_voice_environment()

    assert "STACKCHAN_VOICE_ENV_SENTINEL" not in os.environ


def test_voice_dotenv_uses_explicit_environment_directory(tmp_path, monkeypatch):
    env_dir = tmp_path / "env"
    env_dir.mkdir()
    (env_dir / ".env").write_text(
        "STACKCHAN_VOICE_ENV_SENTINEL=selected-directory\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("STACKCHAN_VOICE_ENV_SENTINEL", raising=False)
    monkeypatch.setenv("STACKCHAN_ENV_DIR", str(env_dir))
    monkeypatch.setenv("STACKCHAN_SKIP_DOTENV", "0")

    load_voice_environment()

    assert os.environ["STACKCHAN_VOICE_ENV_SENTINEL"] == "selected-directory"
