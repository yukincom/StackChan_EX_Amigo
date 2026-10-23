from __future__ import annotations

from importlib import metadata
from pathlib import Path
from types import SimpleNamespace
import json
import subprocess
import sys

import pytest

from server import security_runtime as security
from tools import bootstrap


@pytest.mark.parametrize("actual,minimum,expected", [
    ("2026.7.22", security.MINIMUM_CERTIFI, False),
    ("2026.10.18", security.MINIMUM_CERTIFI, False),
    ("2026.10.19", security.MINIMUM_CERTIFI, True),
    ("2027.1.1", security.MINIMUM_CERTIFI, True),
    ("26.2.1", security.MINIMUM_PIP, False),
    ("26.2.2", security.MINIMUM_PIP, True),
    ("0.10.2", security.MINIMUM_UV, False),
    ("0.13.4", security.MINIMUM_UV, True),
    ("0.13.4rc1", security.MINIMUM_UV, False),
    ("unknown", security.MINIMUM_UV, False),
])
def test_security_floors(actual, minimum, expected):
    assert security.version_at_least(actual, minimum) is expected


@pytest.mark.parametrize("package,old", [("certifi", "2026.7.22"), ("pip", "26.2.1"), ("uv", "0.10.2")])
def test_runtime_rejects_old_installed_packages(monkeypatch, package, old):
    versions = {"certifi": "2026.10.19", "pip": "26.2.2", "uv": "0.13.4"}
    versions[package] = old
    monkeypatch.setattr(security.metadata, "version", versions.__getitem__)
    with pytest.raises(RuntimeError, match=package):
        security.require_secure_environment()


def test_runtime_requires_certifi_but_does_not_require_packaging_tools(monkeypatch):
    def version(package):
        if package == "certifi":
            return "2026.10.19"
        raise metadata.PackageNotFoundError(package)
    monkeypatch.setattr(security.metadata, "version", version)
    security.require_secure_environment()
    monkeypatch.setattr(security.metadata, "version", lambda name: (_ for _ in ()).throw(metadata.PackageNotFoundError(name)))
    with pytest.raises(RuntimeError, match="certifi"):
        security.require_secure_environment()


@pytest.mark.parametrize("output,accepted", [("uv 0.10.2\n", False), ("uv 0.13.4 (build)\n", True), ("not-uv 99.0\n", False)])
def test_selected_uv_is_checked_before_launch(monkeypatch, output, accepted):
    monkeypatch.setattr(security.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=output))
    if accepted:
        security.require_secure_uv(Path("/fixture/uv"))
    else:
        with pytest.raises(RuntimeError, match="uv>=0.13.4"):
            security.require_secure_uv(Path("/fixture/uv"))


def test_bootstrap_first_network_install_is_hash_pinned(monkeypatch, tmp_path):
    monkeypatch.setattr(bootstrap, "ROOT", tmp_path)
    monkeypatch.setattr(bootstrap.venv.EnvBuilder, "create", lambda *_: None)
    commands = []
    monkeypatch.setattr(bootstrap.subprocess, "run", lambda cmd, **kwargs: commands.append(cmd))
    bootstrap.bootstrap()
    first, second, version = commands
    assert "--isolated" in first and "--no-deps" in first
    assert first[-1] == bootstrap.PIP_WHEEL
    assert "#sha256=fd7f4dfa8190e014013b9f08f3e973f4e4c26ffd179a204895533a7254813a70" in first[-1]
    assert second[-1] == "uv==0.13.4"
    assert version[-1] == "--version"


def test_offline_doctor_reports_old_certifi_without_blocking_diagnostics(tmp_path):
    script = """
import importlib.metadata, runpy, sys
original = importlib.metadata.version
importlib.metadata.version = lambda name: '2026.7.22' if name == 'certifi' else original(name)
sys.argv = ['stackchan-lab', 'doctor', '--repo', sys.argv[1], '--env-dir', sys.argv[2]]
runpy.run_module('tools.stackchan_lab', run_name='__main__')
"""
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run([sys.executable, "-B", "-c", script, str(root), str(tmp_path)], cwd=root, capture_output=True, text=True)
    report = json.loads(result.stdout)
    assert result.returncode == 1
    assert any(check["name"] == "security:packaging" for check in report["checks"])
