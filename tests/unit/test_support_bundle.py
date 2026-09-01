from __future__ import annotations

import json
import stat
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools.stackchan_lab.cli import main
from tools.stackchan_lab.support_bundle import (
    SupportBundleError,
    create_support_bundle,
    verify_support_bundle,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def _write_fixture_trace(trace_dir: Path, sentinel: str) -> None:
    trace_dir.mkdir(parents=True)
    valid = {
        "schema_version": 1,
        "timestamp": "2026-09-01T00:00:00+00:00",
        "trace_id": "0123456789abcdef",
        "component": "vision",
        "operation": "process",
        "status": "ok",
        "extra": sentinel,
    }
    (trace_dir / "trace.jsonl").write_text(
        json.dumps(valid) + "\n{malformed\n" + json.dumps({**valid, "timestamp": sentinel}) + "\n",
        encoding="utf-8",
    )


def test_support_bundle_has_fixed_secret_safe_contents(tmp_path):
    sentinel = "DO_NOT_LEAK_dotenv_prompt_image_audio"
    env_dir = tmp_path / "env"
    env_dir.mkdir()
    (env_dir / ".env").write_text(f"OPENAI_API_KEY={sentinel}\n", encoding="utf-8")
    trace_dir = tmp_path / "trace"
    _write_fixture_trace(trace_dir, sentinel)
    (trace_dir / "private.jpg").write_bytes(sentinel.encode())
    (trace_dir / "private.wav").write_bytes(sentinel.encode())
    output = tmp_path / "support.stackchan-support.zip"

    manifest = create_support_bundle(
        REPO_ROOT,
        output,
        env_dir=env_dir,
        trace_dir=trace_dir,
        max_events=10,
        now=lambda: datetime(2026, 9, 1, tzinfo=timezone.utc),
    )

    assert verify_support_bundle(output) == manifest
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    with zipfile.ZipFile(output) as archive:
        assert set(archive.namelist()) == {
            "README.txt",
            "doctor.json",
            "manifest.json",
            "redaction_report.json",
            "runtime.json",
            "traces/events.jsonl",
        }
        extracted = b"\n".join(archive.read(name) for name in archive.namelist())
        redaction = json.loads(archive.read("redaction_report.json"))
    assert sentinel.encode() not in extracted
    assert redaction == {"accepted": 1, "dropped": 1, "malformed": 1, "truncated": 0}


def test_support_bundle_rejects_existing_symlink_and_repo_output(tmp_path):
    existing = tmp_path / "existing.zip"
    existing.write_bytes(b"keep")
    with pytest.raises(SupportBundleError, match="already exists"):
        create_support_bundle(REPO_ROOT, existing, env_dir=tmp_path, trace_dir=tmp_path)

    link = tmp_path / "link.zip"
    try:
        link.symlink_to(existing)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    with pytest.raises(SupportBundleError, match="symlink"):
        create_support_bundle(REPO_ROOT, link, env_dir=tmp_path, trace_dir=tmp_path)

    with pytest.raises(SupportBundleError, match="outside"):
        create_support_bundle(
            REPO_ROOT,
            REPO_ROOT / "forbidden.stackchan-support.zip",
            env_dir=tmp_path,
            trace_dir=tmp_path,
        )


def test_support_bundle_verifier_rejects_unlisted_payload(tmp_path):
    output = tmp_path / "support.zip"
    create_support_bundle(REPO_ROOT, output, env_dir=tmp_path, trace_dir=tmp_path)
    with zipfile.ZipFile(output, "a") as archive:
        archive.writestr("logs/raw.log", "private")
    with pytest.raises(SupportBundleError, match="unknown or missing"):
        verify_support_bundle(output)


def test_support_bundle_ignores_symlinked_trace_directory(tmp_path):
    real_trace = tmp_path / "real-trace"
    _write_fixture_trace(real_trace, "DO_NOT_LEAK_symlinked_trace")
    linked_trace = tmp_path / "linked-trace"
    try:
        linked_trace.symlink_to(real_trace, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    output = tmp_path / "support.zip"
    create_support_bundle(REPO_ROOT, output, env_dir=tmp_path, trace_dir=linked_trace)

    with zipfile.ZipFile(output) as archive:
        assert archive.read("traces/events.jsonl") == b""


def test_support_cli_create_and_verify(tmp_path, capsys):
    output = tmp_path / "support.zip"
    assert main([
        "support", "create", "--repo", str(REPO_ROOT), "--env-dir", str(tmp_path),
        "--trace-dir", str(tmp_path / "missing"), "--output", str(output),
    ]) == 0
    capsys.readouterr()
    assert main(["support", "verify", str(output)]) == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["schema_version"] == 1
