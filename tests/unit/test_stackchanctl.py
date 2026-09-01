from __future__ import annotations

import json

import pytest

from tools import stackchanctl


def test_status_url_is_restricted_to_loopback():
    assert stackchanctl._loopback_base_url("http://127.0.0.1:5050") == "http://127.0.0.1:5050"
    assert stackchanctl._loopback_base_url("http://localhost:5050") == "http://localhost:5050"
    for value in (
        "https://127.0.0.1:5050",
        "http://example.com",
        "http://user:pass@127.0.0.1:5050",
        "http://127.0.0.1:5050/private",
    ):
        with pytest.raises(ValueError, match="loopback|path"):
            stackchanctl._loopback_base_url(value)


def test_status_distinguishes_alive_from_ready(monkeypatch, capsys):
    responses = {
        "/health/live": (200, {"status": "alive"}),
        "/health/ready": (503, {"status": "not_ready"}),
    }
    monkeypatch.setattr(stackchanctl, "_read_probe", lambda _base, path: responses[path])

    assert stackchanctl.main(["status", "--url", "http://127.0.0.1:5050"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "not_ready"
    assert report["live"]["status"] == "alive"


def test_run_server_dry_run_validates_without_starting(monkeypatch, tmp_path, capsys):
    repo = tmp_path / "repo"
    (repo / "server").mkdir(parents=True)
    (repo / "server" / "app.py").write_text("fixture", encoding="utf-8")
    (repo / "pyproject.toml").write_text("fixture", encoding="utf-8")
    uv = tmp_path / "uv"
    uv.write_text("fixture", encoding="utf-8")
    uv.chmod(0o700)
    env_dir = tmp_path / "env"
    env_dir.mkdir()
    (env_dir / ".env").write_text("FIXTURE=1\n", encoding="utf-8")

    assert stackchanctl.main([
        "run-server", "--repo", str(repo), "--env-dir", str(env_dir),
        "--uv", str(uv), "--dry-run",
    ]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "dry_run"
    assert report["env_ready"] is True
    assert report["command"][:4] == ["uv", "run", "--frozen", "--no-sync"]
