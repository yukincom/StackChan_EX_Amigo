#!/usr/bin/env python3
"""Local-only StackChan service preflight, status and supervised launch wrapper."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path
import shutil
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import urlopen


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.stackchan_lab.doctor import doctor_summary, run_doctor
from server.security_runtime import require_secure_uv


def _print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _valid_repo(path: Path) -> Path:
    repo = path.expanduser().resolve()
    if not (repo / "server" / "app.py").is_file() or not (repo / "pyproject.toml").is_file():
        raise ValueError("repository layout is invalid")
    return repo


def _loopback_base_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "http" or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("status URL must be plain HTTP on loopback")
    host = parsed.hostname or ""
    if host.lower() != "localhost":
        try:
            if not ipaddress.ip_address(host).is_loopback:
                raise ValueError
        except ValueError as exc:
            raise ValueError("status URL must be plain HTTP on loopback") from exc
    if parsed.path not in {"", "/"}:
        raise ValueError("status URL must not contain a path")
    port = parsed.port or 80
    rendered_host = f"[{host}]" if ":" in host else host
    return f"http://{rendered_host}:{port}"


def _read_probe(base_url: str, path: str) -> tuple[int, dict]:
    try:
        with urlopen(f"{base_url}{path}", timeout=3) as response:
            raw = response.read(64 * 1024 + 1)
            status = response.status
    except HTTPError as exc:
        raw = exc.read(64 * 1024 + 1)
        status = exc.code
    if len(raw) > 64 * 1024:
        raise RuntimeError("probe response is too large")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise RuntimeError("probe response is invalid")
    return status, value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stackchanctl")
    subparsers = parser.add_subparsers(dest="command", required=True)

    preflight = subparsers.add_parser("preflight", help="run read-only local diagnostics")
    preflight.add_argument("--repo", type=Path, default=REPO_ROOT)
    preflight.add_argument("--env-dir", type=Path, default=Path.home() / "env")

    status = subparsers.add_parser("status", help="read loopback liveness/readiness")
    status.add_argument("--url", default="http://127.0.0.1:5050")

    run_server = subparsers.add_parser("run-server", help="validate identity then exec the server")
    run_server.add_argument("--repo", type=Path, default=REPO_ROOT)
    run_server.add_argument("--env-dir", type=Path, default=Path.home() / "env")
    run_server.add_argument("--uv", type=Path)
    run_server.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "preflight":
        repo = _valid_repo(args.repo)
        report = doctor_summary(run_doctor(repo, args.env_dir))
        _print_json(report)
        return 1 if report["status"] == "error" else 0

    if args.command == "status":
        try:
            base_url = _loopback_base_url(args.url)
            live_status, live = _read_probe(base_url, "/health/live")
            ready_status, ready = _read_probe(base_url, "/health/ready")
        except (ValueError, RuntimeError, URLError, json.JSONDecodeError) as exc:
            _print_json({"status": "unreachable", "error_type": type(exc).__name__})
            return 1
        report = {
            "status": "ready" if live_status == 200 and ready_status == 200 else "not_ready",
            "live": live,
            "ready": ready,
        }
        _print_json(report)
        return 0 if report["status"] == "ready" else 2

    if args.command == "run-server":
        repo = _valid_repo(args.repo)
        env_dir = args.env_dir.expanduser().resolve()
        bundled_uv = repo / ".bootstrap-venv" / ("Scripts/uv.exe" if os.name == "nt" else "bin/uv")
        uv = str(args.uv.expanduser().resolve()) if args.uv else (
            str(bundled_uv) if bundled_uv.is_file() else shutil.which("uv")
        )
        env_ready = env_dir.is_dir() and (env_dir / ".env").is_file()
        if not uv or not Path(uv).is_file() or not os.access(uv, os.X_OK):
            raise RuntimeError("secure uv is unavailable; run python tools/bootstrap.py")
        require_secure_uv(uv)
        command = [uv, "run", "--frozen", "--no-sync", "python", "server/app.py"]
        if args.dry_run:
            _print_json({
                "status": "dry_run",
                "repo_valid": True,
                "env_ready": env_ready,
                "command": [Path(uv).name, *command[1:]],
            })
            return 0 if env_ready else 2
        if not env_ready:
            raise RuntimeError("env directory or .env is unavailable")
        environment = os.environ.copy()
        environment["STACKCHAN_ENV_DIR"] = str(env_dir)
        os.chdir(repo)
        os.execvpe(uv, command, environment)
    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
