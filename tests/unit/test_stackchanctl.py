from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from tools import stackchanctl


ROOT = Path(__file__).resolve().parents[2]
REFERENCE = ROOT / "tests/fixtures/stackchanctl_python_reference.py"


@pytest.fixture(scope="module")
def native_binary():
    name = "stackchanctl.exe" if os.name == "nt" else "stackchanctl"
    binary = Path(os.environ.get("STACKCHANCTL_BIN") or ROOT / "native/stackchanctl/target/release" / name)
    if not binary.is_file():
        pytest.skip("native CLI not built: cargo build --release --locked --manifest-path native/stackchanctl/Cargo.toml")
    return binary.resolve()


def cli(command, *args, environment=None, input=None):
    env = os.environ.copy()
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(ROOT), NO_PROXY="*", no_proxy="*")
    if command[-1] == str(REFERENCE):
        env["STACKCHANCTL_TEST_REFERENCE"] = "1"
    if environment:
        env.update(environment)
    return subprocess.run([*command, *map(str, args)], cwd=ROOT, env=env, input=input, capture_output=True, text=True, timeout=15)


def commands(native_binary):
    return ([sys.executable, "-B", str(REFERENCE)], [str(native_binary)])


@contextmanager
def health_server(live=(200, b'{"status":"alive"}'), ready=(200, b'{"status":"ready"}'), headers=None, requests=None):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if requests is not None:
                requests.append(self.path)
            status, body = live if self.path == "/health/live" else ready
            self.send_response(status)
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


@pytest.mark.parametrize("permission", [None, "", "0"])
@pytest.mark.parametrize("optimized", [False, True])
def test_reference_refuses_direct_execution_before_http(permission, optimized):
    environment = os.environ.copy()
    environment.pop("STACKCHANCTL_TEST_REFERENCE", None)
    if permission is not None:
        environment["STACKCHANCTL_TEST_REFERENCE"] = permission
    requests = []
    with health_server(requests=requests) as url:
        command = [sys.executable, *(["-O"] if optimized else []), "-B", str(REFERENCE)]
        result = subprocess.run([*command, "status", "--url", url], cwd=ROOT, env=environment,
                                capture_output=True, text=True, timeout=15)
    assert result.returncode == 2
    assert "test-only Python reference" in result.stderr
    assert "pytest" in result.stderr
    assert result.stdout == ""
    assert requests == []


@pytest.mark.parametrize("ready_status,body,exit_code,error", [
    (200, json.dumps({"status": "準備完了🤖", "counter": 2**100}, ensure_ascii=False).encode(), 0, None),
    (503, b'{"status":"not_ready"}', 2, None),
    (200, b'invalid json', 1, "JSONDecodeError"),
    (200, b'[]', 1, "RuntimeError"),
    (200, b'{"padding":"' + b'x' * 65536 + b'"}', 1, "RuntimeError"),
])
def test_native_status_matches_python_reference(native_binary, ready_status, body, exit_code, error):
    with health_server(ready=(ready_status, body)) as url:
        results = [cli(command, "status", "--url", url) for command in commands(native_binary)]
    assert [r.returncode for r in results] == [exit_code, exit_code]
    reports = [json.loads(r.stdout) for r in results]
    assert reports[0] == reports[1]
    if error:
        assert reports[1]["error_type"] == error
    if exit_code == 0:
        assert reports[1]["ready"]["counter"] == 2**100


@pytest.mark.parametrize("url", [
    "https://127.0.0.1", "http://example.com", "http://user:pass@127.0.0.1",
    "http://127.0.0.1/private", "http://localhost:65536", "http://localhost:bad",
    "http://127.1", "http://2130706433", "http://localhost?secret=value", "http://[::2]",
])
def test_invalid_status_urls_match_without_network(native_binary, url):
    results = [cli(command, "status", "--url", url) for command in commands(native_binary)]
    assert all(r.returncode == 1 for r in results)
    assert [json.loads(r.stdout) for r in results] == [{"status": "unreachable", "error_type": "ValueError"}] * 2


def test_native_needs_no_python_and_ignores_proxy(native_binary):
    with health_server() as url:
        result = cli([native_binary], "status", "--url", url, environment={
            "PATH": "/nonexistent", "STACKCHAN_PYTHON": "/nonexistent/python",
            "HTTP_PROXY": "http://127.0.0.1:1", "http_proxy": "http://127.0.0.1:1",
            "NO_PROXY": "", "no_proxy": "",
        })
    assert result.returncode == 0
    assert json.loads(result.stdout)["status"] == "ready"


def test_native_does_not_follow_redirects(native_binary):
    with health_server(live=(302, b'{}'), headers={"Location": "http://127.0.0.1:1"}) as url:
        result = cli([native_binary], "status", "--url", url)
    assert result.returncode == 1
    assert json.loads(result.stdout)["error_type"] == "URLError"


@pytest.fixture
def launch_fixture(tmp_path):
    if os.name == "nt":
        pytest.skip("POSIX fake executable fixture; native Windows execution is not verified here")
    repo = tmp_path / "空白 repo"
    (repo / "server").mkdir(parents=True)
    (repo / "server/app.py").write_text("# fixture\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text("# fixture\n", encoding="utf-8")
    env_dir = tmp_path / "設定 env"
    env_dir.mkdir()
    (env_dir / ".env").write_text("FIXTURE=1\n", encoding="utf-8")
    uv = tmp_path / "uv"
    uv.write_text(f"#!{sys.executable}\n" + '''import json, os, sys
if sys.argv[1:] == ["--version"]:
    print(os.environ.get("FIXTURE_UV_VERSION", "uv 0.13.4"))
else:
    print(json.dumps({"args": sys.argv[1:], "cwd": os.getcwd(), "env_dir": os.environ["STACKCHAN_ENV_DIR"], "input": sys.stdin.read()}, ensure_ascii=False))
    print("fixture stderr", file=sys.stderr)
    raise SystemExit(7)
''', encoding="utf-8")
    uv.chmod(0o700)
    return repo, env_dir, uv


def test_reference_refuses_direct_execution_before_uv_or_server(launch_fixture, tmp_path):
    repo, env_dir, uv = launch_fixture
    marker = tmp_path / "uv-was-executed"
    uv.write_text(f"#!{sys.executable}\nfrom pathlib import Path\nPath({str(marker)!r}).touch()\nraise SystemExit(77)\n", encoding="utf-8")
    environment = os.environ.copy()
    environment.pop("STACKCHANCTL_TEST_REFERENCE", None)
    result = subprocess.run([sys.executable, "-B", str(REFERENCE), "run-server", "--repo", str(repo),
                             "--env-dir", str(env_dir), "--uv", str(uv)], cwd=ROOT, env=environment,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 2
    assert "test-only Python reference" in result.stderr
    assert not marker.exists()


@pytest.mark.parametrize("env_ready", [True, False])
@pytest.mark.parametrize("selection", ["explicit", "path", "bundled"])
def test_dry_run_matches_without_starting(native_binary, launch_fixture, env_ready, selection):
    repo, env_dir, uv = launch_fixture
    if not env_ready:
        (env_dir / ".env").unlink()
    if selection == "bundled":
        bundled = repo / ".bootstrap-venv/bin/uv"
        bundled.parent.mkdir(parents=True)
        bundled.symlink_to(uv)
    args = ["run-server", "--repo", repo, "--env-dir", env_dir, "--dry-run"]
    if selection == "explicit":
        args += ["--uv", uv]
    results = [cli(command, *args, environment={"PATH": str(uv.parent)}) for command in commands(native_binary)]
    assert [r.returncode for r in results] == [0 if env_ready else 2] * 2
    assert json.loads(results[0].stdout) == json.loads(results[1].stdout)


@pytest.mark.parametrize("version", ["uv 0.10.2", "uv 0.13.3", "not-uv 99.0", "uv 0.13.4rc1"])
def test_launch_rejects_old_or_unrecognized_uv(native_binary, launch_fixture, version):
    repo, env_dir, uv = launch_fixture
    for command in commands(native_binary):
        result = cli(command, "run-server", "--repo", repo, "--env-dir", env_dir, "--uv", uv, "--dry-run", environment={"FIXTURE_UV_VERSION": version})
        assert result.returncode != 0
        assert "uv>=0.13.4" in result.stderr


def test_exec_preserves_arguments_cwd_environment_stdio_and_exit(native_binary, launch_fixture):
    repo, env_dir, uv = launch_fixture
    results = [cli(command, "run-server", "--repo", repo, "--env-dir", env_dir, "--uv", uv, input="入力🤖\n") for command in commands(native_binary)]
    assert all(r.returncode == 7 and r.stderr == "fixture stderr\n" for r in results)
    reports = [json.loads(r.stdout) for r in results]
    assert reports[0] == reports[1] == {
        "args": ["run", "--frozen", "--no-sync", "python", "server/app.py"],
        "cwd": str(repo.resolve()), "env_dir": str(env_dir.resolve()), "input": "入力🤖\n",
    }


@pytest.mark.parametrize("port,exit_code", [("5050", 0), ("70000", 1)])
def test_offline_preflight_matches_and_does_not_leak_values(native_binary, tmp_path, port, exit_code):
    (tmp_path / ".env").write_text(f"OPENAI_API_KEY=fixture-private-token\nM5STACK_PORT={port}\n", encoding="utf-8")
    results = [cli(command, "preflight", "--repo", ROOT, "--env-dir", tmp_path, environment={"STACKCHAN_PYTHON": sys.executable}) for command in commands(native_binary)]
    assert [r.returncode for r in results] == [exit_code, exit_code]
    assert json.loads(results[0].stdout) == json.loads(results[1].stdout)
    assert all("fixture-private-token" not in r.stdout + r.stderr for r in results)


def test_adapter_execs_native_without_python_implementation(monkeypatch, tmp_path):
    binary = tmp_path / "native"
    binary.touch(mode=0o700)
    monkeypatch.setenv("STACKCHANCTL_BIN", str(binary))
    class ExecCalled(Exception):
        pass
    def execve(path, args, environment):
        assert path == str(binary.resolve())
        assert args == [path, "status"]
        assert environment["STACKCHAN_REPO_ROOT"] == str(ROOT)
        assert environment["STACKCHAN_PYTHON"] == sys.executable
        raise ExecCalled
    monkeypatch.setattr(stackchanctl.os, "execve", execve)
    with pytest.raises(ExecCalled):
        stackchanctl.main(["status"])


def test_missing_native_binary_fails_without_fallback(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("STACKCHANCTL_BIN", str(tmp_path / "absent"))
    assert stackchanctl.main(["status"]) == 1
    assert "cargo build --release --locked" in capsys.readouterr().err


def test_python_adapter_runs_native_status(native_binary):
    with health_server() as url:
        result = cli([sys.executable, "-B", str(ROOT / "tools/stackchanctl.py")], "status", "--url", url, environment={"STACKCHANCTL_BIN": str(native_binary)})
    assert result.returncode == 0
    assert json.loads(result.stdout)["status"] == "ready"
